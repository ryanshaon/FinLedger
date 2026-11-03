"""JSON face of the control desk.

Scoring, mapping, approval, and invites stay in the services this module calls.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from urllib.parse import unquote

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from finledger_platform.browser_sessions import MIN_PASSWORD_CHARS, SessionDenied, StaffSession
from finledger_platform.db import firm, tenant
from finledger_platform import files, intake
from finledger_platform.staff_invites import (
    InviteConflict, InviteDenied, InviteInvalid, InviteUnavailable, normalize_email,
)

from .desk_shell import desk_dist, desk_document, mfa_blocks, queue_name
from .reconciliation import load_reconciliation
from .service import Conflict, ControlService, Forbidden, NotFound
from .web import processing_label

LOGIN_CSRF = "fl_login_csrf"
_INVALID = "This request is incomplete or invalid. Nothing was saved."


def jsonable(value):
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def problem(exc: Exception) -> int:
    if isinstance(exc, NotFound):
        return 404
    if isinstance(exc, Forbidden):
        return 403
    return 409


def register_desk_api(app: FastAPI, *, pool, source_url, browser_sessions, staff_invitations,
                      secure_browser, session_cookie_name, verify_form_csrf, user,
                      document_store=None, max_upload_bytes=25 * 1024 * 1024) -> None:
    def api_json(body, status: int = 200, request: Request | None = None, csrf: str | None = None):
        response = JSONResponse(jsonable(body), status_code=status, headers={"Cache-Control": "no-store"})
        if request is not None and csrf is not None:
            response.set_cookie(LOGIN_CSRF, csrf, httponly=True, secure=secure_browser(request),
                                samesite="strict", path="/")
        return response

    def csrf_value(request: Request) -> str:
        current = request.cookies.get(LOGIN_CSRF)
        if current and current.isascii():
            return current
        return secrets.token_urlsafe(32)

    def require_aal2(who) -> None:
        if mfa_blocks(who):
            raise HTTPException(403, detail={"mfa_required": True})

    def require_admin(who) -> None:
        require_aal2(who)
        if not who["firm_admin"]:
            raise HTTPException(403, "Firm admin only")

    def desk_roles(client_id, who, conn):
        roles = conn.execute("select user_client_roles(%s,%s) as roles", (who["user_id"], client_id)).fetchone()["roles"]
        if not roles:
            raise HTTPException(403, "No access to this client")
        require_aal2(who)
        return set(roles)

    def api_user(request: Request, authorization: str | None = Header(None)):
        # Same session lookup as the HTML desk. The desk app wants 401, not a redirect.
        if browser_sessions is not None:
            cookie = request.cookies.get(session_cookie_name(request))
            if not cookie:
                raise HTTPException(401, "Sign in required")
            try:
                with pool.connection() as conn:
                    staff = browser_sessions.resolve(conn, cookie)
            except SessionDenied:
                raise HTTPException(401, "Sign in required") from None
            return {"user_id": staff.user_id, "firm_id": staff.firm_id, "firm_admin": staff.firm_admin,
                    "aal": staff.aal, "mfa_required": staff.mfa_required}
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Bearer token required")
        digest = hashlib.sha256(authorization[7:].encode()).digest()
        with pool.connection() as conn:
            row = conn.execute("select * from auth_user(%s)", (digest,)).fetchone()
        if not row:
            raise HTTPException(401, "Invalid token")
        return row

    async def read_json(request: Request) -> dict:
        raw = await request.body()
        if len(raw) > 64_000:
            raise HTTPException(422, _INVALID)
        try:
            body = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(422, _INVALID) from None
        if not isinstance(body, dict):
            raise HTTPException(422, _INVALID)
        return body

    def check_csrf(request: Request, body: dict) -> None:
        token = body.get("csrf_token")
        verify_form_csrf(request, token if isinstance(token, str) else "", LOGIN_CSRF)

    def need_str(body: dict, key: str, limit: int, *, required: bool) -> str:
        value = body.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
            raise HTTPException(422, _INVALID)
        return value

    def need_revision(body: dict) -> int:
        revision = body.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise HTTPException(422, _INVALID)
        return revision

    def client_rows(who):
        with pool.connection() as conn, firm(conn, who["firm_id"]):
            if who["firm_admin"]:
                rows = conn.execute("select id, name, gstins from clients order by name").fetchall()
            else:
                rows = conn.execute(
                    "select c.id, c.name, c.gstins from clients c where exists "
                    "(select 1 from client_members m where m.client_id = c.id and m.user_id = %s) "
                    "order by c.name", (who["user_id"],)).fetchall()
        return [{"id": row["id"], "name": row["name"],
                 "gstin": (row["gstins"] or [""])[0] if row["gstins"] else ""} for row in rows]

    def client_name(conn, client_id):
        with conn.transaction():
            conn.execute("select set_config('app.client_id', %s, true)", (str(client_id),))
            row = conn.execute("select name from clients where id = %s", (client_id,)).fetchone()
        return row["name"] if row else ""

    def queue_item(row) -> dict:
        item = dict(row)
        return {
            "document_id": item.get("document_id"),
            "invoice_no": item.get("invoice_no"),
            "filename": item.get("original_filename"),
            "vendor_name": item.get("vendor_name"),
            "invoice_date": item.get("invoice_date"),
            "total": item.get("total"),
            "band": item.get("band"),
            "score": item.get("score"),
            "state_label": processing_label(item),
        }

    if browser_sessions is not None:
        @app.get("/api/csrf")
        def csrf(request: Request):
            token = csrf_value(request)
            return api_json({"csrf_token": token}, request=request, csrf=token)

        @app.post("/api/login")
        async def login(request: Request):
            body = await read_json(request)
            check_csrf(request, body)
            email = need_str(body, "email", 320, required=True)
            password = need_str(body, "password", 1024, required=True)
            token = csrf_value(request)
            try:
                with pool.connection() as conn:
                    cookie = browser_sessions.sign_in(conn, email, password)
            except SessionDenied:
                return api_json({"detail": "Sign-in failed or this account is not provisioned.", "csrf_token": token},
                                401, request, token)
            response = api_json({"ok": True}, request=request, csrf=token)
            response.set_cookie(session_cookie_name(request), cookie, httponly=True,
                                secure=secure_browser(request), samesite="lax", path="/")
            return response

        @app.post("/api/logout")
        async def logout(request: Request):
            body = await read_json(request)
            check_csrf(request, body)
            cookie = request.cookies.get(session_cookie_name(request))
            if cookie:
                with pool.connection() as conn:
                    browser_sessions.sign_out(conn, cookie)
            response = api_json({"ok": True})
            response.delete_cookie(session_cookie_name(request), path="/")
            return response

        @app.post("/api/auth/accept")
        async def accept_invite(request: Request):
            body = await read_json(request)
            check_csrf(request, body)
            token_hash = need_str(body, "token_hash", 512, required=True)
            password = need_str(body, "password", 1024, required=True)
            confirm = need_str(body, "password_confirm", 1024, required=True)
            if len(password) < MIN_PASSWORD_CHARS:
                raise HTTPException(422, f"Use at least {MIN_PASSWORD_CHARS} characters.")
            if not secrets.compare_digest(password.encode(), confirm.encode()):
                raise HTTPException(422, "The two passwords do not match.")
            try:
                with pool.connection() as conn:
                    browser_sessions.accept_invite(conn, token_hash, password)
            except SessionDenied:
                raise HTTPException(400, "This invitation is invalid, expired or not provisioned.")
            return api_json({"ok": True})

        @app.get("/api/mfa")
        def mfa(request: Request, who=Depends(api_user)):
            del who
            cookie = request.cookies.get(session_cookie_name(request))
            try:
                with pool.connection() as conn:
                    factors = browser_sessions.mfa_factors(conn, cookie)
            except SessionDenied:
                raise HTTPException(401, "Sign in required") from None
            token = csrf_value(request)
            return api_json({"factor_id": factors[0] if factors else "", "secret": "", "csrf_token": token},
                            request=request, csrf=token)

        @app.post("/api/mfa/enroll")
        async def mfa_enroll(request: Request, who=Depends(api_user)):
            del who
            body = await read_json(request)
            check_csrf(request, body)
            cookie = request.cookies.get(session_cookie_name(request))
            try:
                with pool.connection() as conn:
                    factor, secret = browser_sessions.mfa_enroll(conn, cookie)
            except SessionDenied:
                raise HTTPException(503, "MFA setup unavailable") from None
            token = csrf_value(request)
            return api_json({"factor_id": factor, "secret": secret, "csrf_token": token}, request=request, csrf=token)

        @app.post("/api/mfa/verify")
        async def mfa_verify(request: Request, who=Depends(api_user)):
            del who
            body = await read_json(request)
            check_csrf(request, body)
            factor_id = need_str(body, "factor_id", 80, required=True)
            code = need_str(body, "code", 12, required=True)
            cookie = request.cookies.get(session_cookie_name(request))
            token = csrf_value(request)
            try:
                with pool.connection() as conn:
                    upgraded = browser_sessions.mfa_verify(conn, cookie, factor_id, code)
            except SessionDenied:
                return api_json({"detail": "Code could not be verified. Try again.", "csrf_token": token},
                                400, request, token)
            response = api_json({"ok": True, "csrf_token": token}, request=request, csrf=token)
            response.set_cookie(session_cookie_name(request), upgraded, httponly=True,
                                secure=secure_browser(request), samesite="lax", path="/")
            return response

        @app.get("/api/staff")
        def staff(request: Request, who=Depends(api_user)):
            require_admin(who)
            token = csrf_value(request)
            rows = client_rows(who)
            return api_json({"clients": rows, "csrf_token": token}, request=request, csrf=token)

        @app.post("/api/staff/invite")
        async def staff_invite(request: Request, who=Depends(api_user)):
            body = await read_json(request)
            check_csrf(request, body)
            require_admin(who)
            if staff_invitations is None:
                raise HTTPException(503, "Invitations are not configured on this server.")
            try:
                email = normalize_email(need_str(body, "email", 320, required=True))
            except InviteInvalid as exc:
                raise HTTPException(422, f"Invitation not sent: {exc}.") from None
            name = need_str(body, "name", 200, required=False)
            firm_admin = body.get("firm_admin", False)
            if not isinstance(firm_admin, bool):
                raise HTTPException(422, _INVALID)
            raw = body.get("memberships") or []
            if not isinstance(raw, list):
                raise HTTPException(422, "Choose clients from the list.")
            pairs = []
            for item in raw[:600]:
                if not isinstance(item, dict) or not isinstance(item.get("role"), str):
                    raise HTTPException(422, "Choose clients from the list.")
                try:
                    pairs.append((UUID(str(item.get("client_id"))), item["role"]))
                except ValueError:
                    raise HTTPException(422, "Choose clients from the list.") from None
            actor = StaffSession(who["user_id"], who["firm_id"], who["firm_admin"], who.get("aal", "aal1"))
            try:
                with pool.connection() as conn:
                    result = staff_invitations.invite(conn, actor, email, name=name, firm_admin=firm_admin,
                                                      memberships=pairs)
            except InviteDenied:
                raise HTTPException(403, "Firm admin with two-step verification required") from None
            except InviteInvalid as exc:
                raise HTTPException(422, f"Invitation not sent: {exc}.") from None
            except InviteConflict as exc:
                raise HTTPException(409, f"Invitation not sent: {exc}.") from None
            except InviteUnavailable:
                raise HTTPException(503, "Invitation could not be sent. Nothing was granted; try again shortly.") from None
            verb = "re-sent" if result.resent else "sent"
            return api_json({"notice": f"Invitation {verb} to {email}."}, 201)

    @app.get("/api/session")
    def session(request: Request, who=Depends(api_user)):
        token = csrf_value(request)
        aal = who["aal"] if "aal" in who else "aal2"
        mfa_required = who["mfa_required"] if "mfa_required" in who else True
        return api_json({
            "user_id": who["user_id"],
            "firm_id": who["firm_id"],
            "firm_admin": bool(who["firm_admin"]),
            "aal": aal,
            "mfa_required": bool(mfa_required),
            "csrf_token": token,
        }, request=request, csrf=token)

    @app.get("/api/clients")
    def clients(who=Depends(api_user)):
        require_aal2(who)
        return api_json({"clients": client_rows(who)})

    @app.get("/api/clients/{client_id}/queue")
    def queue(request: Request, client_id: UUID, queue: str = Query("needs_review"), who=Depends(api_user)):
        with pool.connection() as conn:
            roles = desk_roles(client_id, who, conn)
            service = ControlService(conn)
            try:
                items = service.inbox(client_id, queue)
                counts = service.queue_counts(client_id)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            name = client_name(conn, client_id)
        return api_json({
            "client": {"id": client_id, "name": name},
            "roles": sorted(roles),
            "csrf_token": (token := csrf_value(request)),
            "max_upload_bytes": max_upload_bytes,
            "queue": queue,
            "counts": counts,
            "items": [queue_item(row) for row in items],
        }, request=request, csrf=token)

    def accept_staff_upload(client_id, who, filename, data):
        # Recheck membership after the upload; hold no DB connection during its stream.
        with pool.connection() as conn:
            roles = desk_roles(client_id, who, conn)
            if not roles & {"ap_clerk", "firm_admin"}:
                raise HTTPException(403, "Upload role required")
            with tenant(conn, client_id):
                return intake.accept(conn, document_store, client_id, "portal", [(filename, data)],
                                     {"uploaded_by": str(who["user_id"])})

    @app.post("/api/clients/{client_id}/documents")
    async def upload_document(request: Request, client_id: UUID, who=Depends(api_user)):
        verify_form_csrf(request, request.headers.get("x-csrf-token", ""), LOGIN_CSRF)
        with pool.connection() as conn:
            roles = desk_roles(client_id, who, conn)
            if not roles & {"ap_clerk", "firm_admin"}:
                raise HTTPException(403, "Upload role required")
        if document_store is None:
            raise HTTPException(503, "Document storage is not configured")
        encoded_name = request.headers.get("x-filename", "")
        try:
            filename = unquote(encoded_name, encoding="utf-8", errors="strict")
        except UnicodeDecodeError:
            raise HTTPException(422, "Invalid filename") from None
        if not filename.strip() or len(encoded_name) > 1200 or len(filename) > 255:
            raise HTTPException(422, "Invalid filename")
        length = request.headers.get("content-length")
        if length is not None:
            if len(length) > 20 or not length.isascii() or not length.isdecimal():
                raise HTTPException(422, "Invalid upload size")
            if int(length) > max_upload_bytes:
                raise HTTPException(413, "This file exceeds the upload limit")
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > max_upload_bytes:
                raise HTTPException(413, "This file exceeds the upload limit")
            data.extend(chunk)
        if files.sniff(data) not in {files.PDF, files.JPEG, files.PNG}:
            raise HTTPException(422, "Upload a PDF, JPG or PNG invoice")
        try:
            receipt = await run_in_threadpool(accept_staff_upload, client_id, who, filename, bytes(data))
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "The bill could not be saved. Please try again.") from None
        return api_json({"received": receipt.received, "rejected": receipt.rejected},
                        status=201 if receipt.received else 422)

    @app.get("/api/clients/{client_id}/reconciliation")
    def reconciliation(client_id: UUID, who=Depends(api_user)):
        with pool.connection() as conn:
            desk_roles(client_id, who, conn)
            body = load_reconciliation(conn, client_id)
            name = client_name(conn, client_id)
        return api_json({"client": {"id": client_id, "name": name}, **body})

    @app.get("/api/clients/{client_id}/invoices/{document_id}")
    def invoice(request: Request, client_id: UUID, document_id: UUID, who=Depends(api_user)):
        with pool.connection() as conn:
            roles = desk_roles(client_id, who, conn)
            try:
                detail = ControlService(conn).review_detail(client_id, document_id, source_url,
                                                          actor_id=who["user_id"])
            except (NotFound, Forbidden, Conflict) as exc:
                raise HTTPException(problem(exc), str(exc)) from None
            name = client_name(conn, client_id)
            compared = ControlService(conn).bill_match(client_id, document_id)
        detail = dict(detail)
        detail["state_label"] = processing_label({"status": detail.get("status")})
        token = csrf_value(request)
        return api_json({
            "client": {"id": client_id, "name": name},
            "roles": sorted(roles),
            "csrf_token": token,
            "invoice": detail,
            "match": compared["match"],
            "posting": compared["posting"],
        }, request=request, csrf=token)

    @app.post("/api/clients/{client_id}/invoices/{document_id}/edit")
    async def edit(request: Request, client_id: UUID, document_id: UUID, who=Depends(api_user)):
        body = await read_json(request)
        check_csrf(request, body)
        revision = need_revision(body)
        field = need_str(body, "field", 80, required=True)
        value = need_str(body, "value", 4000, required=False)
        with pool.connection() as conn:
            roles = desk_roles(client_id, who, conn)
            if not roles & {"ap_clerk", "approver", "firm_admin"}:
                raise HTTPException(403, "Edit role required")
            try:
                result = ControlService(conn).edit_field(
                    client_id, document_id, who["user_id"], field, value, expected_revision=revision)
            except (NotFound, Forbidden, Conflict) as exc:
                raise HTTPException(problem(exc), str(exc)) from None
        return api_json(result)

    @app.post("/api/clients/{client_id}/invoices/{document_id}/approve")
    async def approve(request: Request, client_id: UUID, document_id: UUID, who=Depends(api_user)):
        body = await read_json(request)
        check_csrf(request, body)
        revision = need_revision(body)
        post = body.get("post")
        if not isinstance(post, bool):
            raise HTTPException(422, _INVALID)
        with pool.connection() as conn:
            desk_roles(client_id, who, conn)
            try:
                result = ControlService(conn).approve(
                    client_id, document_id, who["user_id"], post=post, expected_revision=revision)
            except (NotFound, Forbidden, Conflict) as exc:
                raise HTTPException(problem(exc), str(exc)) from None
        return api_json(result)

    @app.post("/api/clients/{client_id}/invoices/{document_id}/reject")
    async def reject(request: Request, client_id: UUID, document_id: UUID, who=Depends(api_user)):
        body = await read_json(request)
        check_csrf(request, body)
        revision = need_revision(body)
        resubmit = body.get("resubmit", True)
        if not isinstance(resubmit, bool):
            raise HTTPException(422, _INVALID)
        note = need_str(body, "note", 4000, required=False) or "Reviewer requested correction"
        with pool.connection() as conn:
            desk_roles(client_id, who, conn)
            try:
                result = ControlService(conn).reject(
                    client_id, document_id, who["user_id"], note, resubmit=resubmit, expected_revision=revision)
            except (NotFound, Forbidden, Conflict) as exc:
                raise HTTPException(problem(exc), str(exc)) from None
        return api_json(result)

    def desk_page(request: Request):
        shell = desk_document(request)
        if shell is not None:
            return shell
        raise HTTPException(404, "The desk is not built")

    @app.get("/clients")
    def clients_page(request: Request, _who=Depends(user)):
        return desk_page(request)

    @app.get("/transactions")
    def transactions_page(request: Request, _who=Depends(user)):
        return desk_page(request)

    @app.get("/staff")
    def staff_page(request: Request, _who=Depends(user)):
        shell = desk_document(request)
        if shell is not None:
            return shell
        return RedirectResponse("/admin/staff", status_code=302)

    @app.get("/clients/{client_id}")
    def client_page(request: Request, client_id: UUID, _who=Depends(user)):
        shell = desk_document(request)
        if shell is not None:
            return shell
        return RedirectResponse(f"/clients/{client_id}/review?queue={queue_name(request.query_params.get('queue'))}",
                                status_code=302)

    @app.get("/clients/{client_id}/invoices/{document_id}")
    def invoice_page(request: Request, client_id: UUID, document_id: UUID, _who=Depends(user)):
        shell = desk_document(request)
        if shell is not None:
            return shell
        return RedirectResponse(f"/clients/{client_id}/review/{document_id}", status_code=302)

    assets = desk_dist() / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="desk-assets")
