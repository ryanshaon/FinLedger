"""HTTP surface for Person 2.

Public doors      GET/POST /i/{token}             unique upload link (vendors)
                  POST     /inbound/email          raw MIME from the MX provider, HMAC-signed
                  GET      /files/{signed}         signed downloads (local store only)
Staff (Bearer)    /clients...                      workspace, policy, doors, documents list, signed raw URL, CSV
Agent (Bearer)    /agent/...                       Person 4 site agent: masters snapshot, open bills, mapping
"""
from __future__ import annotations

import hashlib
import hmac
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator, Literal
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import email_in, intake, tenancy, web
from .config import Settings
from .db import firm, tenant
from .store import LocalStore

MAX_FILES_PER_UPLOAD = 10
MAX_EMAIL_BYTES = 40 * 1024 * 1024
SIGNED_URL_TTL = 300
DOC_COLUMNS = ("id, channel, status, status_reason, original_filename, mime, size_bytes, page_count, document_class, "
               "content_hash, virus_ok, phish_flag, po_number, vendor_note, source_meta, received_at, prepared_at, updated_at")


@dataclass
class Staff:
    user_id: UUID
    firm_id: UUID
    firm_admin: bool


class ClientIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    erp_type: Literal["tally", "zoho", "qbo", "sap", "oracle"] = "tally"
    gstins: list[str] = []
    inbound_slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
    policy: dict[str, Any] = {}


class MemberIn(BaseModel):
    user_id: UUID
    role: Literal["ap_clerk", "approver", "payer"]


class PairIn(BaseModel):
    erp_type: Literal["tally", "zoho", "qbo", "sap", "oracle"] = "tally"
    company_name: str | None = None


class MastersIn(BaseModel):
    kind: Literal["ledger", "group", "vendor", "cost_centre", "company"]
    items: list[dict[str, Any]]


class OpenBill(BaseModel):
    party_ledger: str
    vendor_gstin: str | None = None
    bill_ref: str
    bill_date: str | None = None
    due_date: str | None = None
    amount: float
    pending_amount: float


class OpenBillsIn(BaseModel):
    as_of: datetime
    bills: list[OpenBill]


class MappingIn(BaseModel):
    kind: Literal["party", "ledger", "tax", "tds", "cost_centre"]
    our_key: str = Field(min_length=1)
    erp_value: str = Field(min_length=1)


def create_app(settings: Settings, pool, store) -> FastAPI:
    app = FastAPI(title="FinLedger platform (Person 2)", docs_url="/docs", redoc_url=None)

    def conn_dep() -> Iterator[psycopg.Connection]:
        with pool.connection() as conn:
            yield conn

    # ---------- helpers ----------

    def rate_ok(conn: psycopg.Connection, key: str, limit: int) -> bool:
        row = conn.execute(
            """insert into rate_limits (key, window_start, hits) values (%s, date_trunc('hour', now()), 1)
               on conflict (key, window_start) do update set hits = rate_limits.hits + 1 returning hits""",
            (key,)).fetchone()
        if random.random() < 0.01:  # ponytail: lazy GC of old windows; move to a cron if the table ever matters
            conn.execute("delete from rate_limits where window_start < now() - interval '1 day'")
        return row["hits"] <= limit

    def bearer(authorization: str | None) -> str:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "missing bearer token")
        return authorization[7:].strip()

    def staff_dep(authorization: str | None = Header(default=None),
                  conn: psycopg.Connection = Depends(conn_dep)) -> Staff:
        row = conn.execute("select * from auth_user(%s)", (tenancy.token_hash(bearer(authorization)),)).fetchone()
        if not row:
            raise HTTPException(401, "invalid token")
        return Staff(row["user_id"], row["firm_id"], row["firm_admin"])

    def roles_for(conn: psycopg.Connection, staff: Staff, client_id: UUID) -> list[str]:
        roles = conn.execute("select user_client_roles(%s, %s) as r", (staff.user_id, client_id)).fetchone()["r"]
        if not roles:
            raise HTTPException(404, "client not found")  # 404, not 403: do not confirm other firms' ids
        return roles

    def require_admin(staff: Staff) -> None:
        if not staff.firm_admin:
            raise HTTPException(403, "firm admin only")

    def client_view(c: dict) -> dict:
        out = {k: v for k, v in c.items() if k not in ("firm_id",)}
        out["upload_link"] = settings.upload_link(c["public_token"])
        out["inbound_email"] = settings.inbound_address(c["inbound_slug"])
        return out

    def wants_html(request: Request) -> bool:
        return "text/html" in request.headers.get("accept", "")

    # ---------- public: unique link ----------

    @app.get("/healthz")
    def healthz(conn: psycopg.Connection = Depends(conn_dep)):
        conn.execute("select 1")
        return {"ok": True}

    def link_client(conn: psycopg.Connection, token: str) -> dict | None:
        cid = conn.execute("select client_by_public_token(%s) as id", (token,)).fetchone()["id"]
        if cid is None:
            return None
        with tenant(conn, cid):
            return conn.execute("select id, name, inbound_slug from clients where id = %s", (cid,)).fetchone()

    @app.get("/i/{token}", response_class=HTMLResponse)
    def upload_form(token: str, conn: psycopg.Connection = Depends(conn_dep)):
        c = link_client(conn, token)
        if c is None:
            return HTMLResponse(web.not_found_page(), status_code=404)
        return web.upload_page(c["name"], f"/i/{token}", settings.inbound_address(c["inbound_slug"]),
                               settings.max_upload_bytes // (1024 * 1024))

    @app.post("/i/{token}")
    async def upload(token: str, request: Request, files: list[UploadFile] = File(...),
                     po_number: str = Form(default="", max_length=60), note: str = Form(default="", max_length=500),
                     conn: psycopg.Connection = Depends(conn_dep)):
        html = wants_html(request)
        ip = request.client.host if request.client else "unknown"
        if not rate_ok(conn, f"ip:{ip}", settings.rate_per_ip_hour):
            return _error(html, 429, "Too many uploads", "Too many uploads from this network. Try again in an hour.")
        c = link_client(conn, token)
        if c is None:
            return HTMLResponse(web.not_found_page(), 404) if html else JSONResponse({"detail": "unknown link"}, 404)
        if not rate_ok(conn, f"token:{token}", settings.rate_per_token_hour):
            return _error(html, 429, "Too many uploads", "This link has had too many uploads. Try again in an hour.")
        if len(files) > MAX_FILES_PER_UPLOAD:
            return _error(html, 400, "Too many files", f"Send at most {MAX_FILES_PER_UPLOAD} files at once.")

        blobs, total = [], 0
        for f in files:
            data = await f.read(settings.max_upload_bytes + 1)
            total += len(data)
            if total > settings.max_upload_bytes:
                mb = settings.max_upload_bytes // (1024 * 1024)
                return _error(html, 413, "Files too large", f"Keep one upload under {mb} MB.")
            blobs.append((f.filename or "upload", data))

        meta = {"ip": ip, "user_agent": request.headers.get("user-agent", "")[:200]}
        with tenant(conn, c["id"]):
            receipt = intake.accept(conn, store, c["id"], "link", blobs, meta,
                                    po_number=po_number.strip() or None, vendor_note=note.strip() or None)
        status = 201 if receipt.received else 422
        if html:
            return HTMLResponse(web.receipt_page(c["name"], receipt.received, receipt.rejected, f"/i/{token}",
                                                 settings.inbound_address(c["inbound_slug"])), status_code=status)
        return JSONResponse({"received": receipt.received, "rejected": receipt.rejected}, status_code=status)

    def _error(html: bool, code: int, title: str, text: str) -> Response:
        if html:
            return HTMLResponse(web.message_page(title, text), status_code=code)
        return JSONResponse({"detail": text}, status_code=code)

    # ---------- public: inbound email ----------

    @app.post("/inbound/email")
    async def inbound_email(request: Request, to: str | None = Query(default=None),
                            x_finledger_signature: str | None = Header(default=None),
                            conn: psycopg.Connection = Depends(conn_dep)):
        buffered = bytearray()
        async for chunk in request.stream():
            if len(buffered) + len(chunk) > MAX_EMAIL_BYTES:
                raise HTTPException(413, "message too large")
            buffered.extend(chunk)
        if (not x_finledger_signature or len(x_finledger_signature) != 71
                or not x_finledger_signature.startswith("sha256=")
                or any(c not in "0123456789abcdef" for c in x_finledger_signature[7:])):
            raise HTTPException(401, "bad signature")
        raw = bytes(buffered)
        del buffered
        good = "sha256=" + hmac.new(settings.inbound_webhook_secret, raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(x_finledger_signature, good):
            raise HTTPException(401, "bad signature")

        parsed = email_in.parse(raw, settings.inbound_authserv_id)
        candidates = ([to] if to else []) + parsed.recipients
        slug = next((s for a in candidates if (s := email_in.slug_from_address(a, settings.inbound_domain))), None)
        cid = conn.execute("select client_by_inbound_slug(%s) as id", (slug,)).fetchone()["id"] if slug else None
        if cid is None:
            # 200 so the provider does not retry; there is no second inbox to fall back to.
            return {"status": "dropped", "reason": "unknown recipient"}

        meta = {"from": parsed.sender, "sender_domain": parsed.sender_domain, "subject": parsed.subject,
                "message_id": parsed.message_id, "recipient": settings.inbound_address(slug), "auth": parsed.auth,
                "phish_reasons": parsed.phish_reasons}
        with tenant(conn, cid):
            receipt = intake.accept(conn, store, cid, "email", parsed.attachments, meta,
                                    intake_key=parsed.intake_key, phish_flag=parsed.phish_flag)
            replayed = bool(receipt.received) and all(r.get("replayed") for r in receipt.received)
            # Reply only to authenticated senders (no backscatter to spoofed addresses) and only once per message.
            if parsed.sender and not parsed.phish_flag and not replayed:
                template = "received" if receipt.received else "resubmit"
                reasons = receipt.rejected or ([] if parsed.attachments else
                                               [{"filename": "", "reason": "no invoice attached"}])
                conn.execute(
                    "insert into outbox (client_id, document_id, to_addr, template, payload) values (%s, %s, %s, %s, %s)",
                    (cid, receipt.received[0]["document_id"] if receipt.received else None, parsed.sender, template,
                     Jsonb({"subject": parsed.subject, "received": receipt.received,
                                               "rejected": reasons})))
        return {"status": "accepted" if receipt.received else "rejected",
                "received": receipt.received, "rejected": receipt.rejected}

    # ---------- public: signed downloads (local store) ----------

    @app.get("/files/{signed}")
    def signed_file(signed: str):
        if not isinstance(store, LocalStore):
            raise HTTPException(404)
        claim = store.verify_token(signed)
        if claim is None:
            raise HTTPException(403, "link expired or invalid")
        key, filename = claim
        data = store.get(key)
        mime = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "md": "text/markdown",
                "json": "application/json"}.get(key.rsplit(".", 1)[-1], "application/octet-stream")
        name = filename or key.rsplit("/", 1)[-1]
        if all(32 <= ord(c) < 127 and c not in '"\\' for c in name):
            disposition = f'inline; filename="{name}"'
        else:
            from urllib.parse import quote

            disposition = "inline; filename*=UTF-8''" + quote(name, safe="")
        headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                   "Content-Disposition": disposition}
        return Response(data, media_type=mime, headers=headers)

    # ---------- staff ----------

    @app.post("/clients", status_code=201)
    def create_client(body: ClientIn, staff: Staff = Depends(staff_dep), conn: psycopg.Connection = Depends(conn_dep)):
        require_admin(staff)
        try:
            with firm(conn, staff.firm_id):
                c = tenancy.create_client(conn, body.name, erp_type=body.erp_type, gstins=body.gstins,
                                          inbound_slug=body.inbound_slug, policy=body.policy)
        except ValueError as e:
            raise HTTPException(422, str(e))
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "inbound slug already taken")
        except (psycopg.errors.CheckViolation, psycopg.errors.InvalidTextRepresentation) as e:
            raise HTTPException(422, f"invalid policy: {e.diag.message_primary}")
        return client_view(c)

    @app.get("/clients")
    def list_clients(staff: Staff = Depends(staff_dep), conn: psycopg.Connection = Depends(conn_dep)):
        with firm(conn, staff.firm_id):
            if staff.firm_admin:
                rows = conn.execute("select * from clients order by name").fetchall()
            else:
                rows = conn.execute("""select c.* from clients c where exists (select from client_members m
                                       where m.client_id = c.id and m.user_id = %s) order by name""",
                                    (staff.user_id,)).fetchall()
        return [client_view(c) for c in rows]

    @app.get("/clients/{client_id}")
    def get_client(client_id: UUID, staff: Staff = Depends(staff_dep), conn: psycopg.Connection = Depends(conn_dep)):
        roles = roles_for(conn, staff, client_id)
        with tenant(conn, client_id):
            c = conn.execute("select * from clients where id = %s", (client_id,)).fetchone()
        return {**client_view(c), "my_roles": roles}

    @app.patch("/clients/{client_id}/policy")
    def patch_policy(client_id: UUID, policy: dict[str, Any], staff: Staff = Depends(staff_dep),
                     conn: psycopg.Connection = Depends(conn_dep)):
        roles_for(conn, staff, client_id)
        require_admin(staff)
        try:
            with tenant(conn, client_id):
                return client_view(tenancy.update_policy(conn, client_id, policy))
        except (psycopg.errors.CheckViolation, psycopg.errors.InvalidTextRepresentation,
                psycopg.errors.DatatypeMismatch) as e:
            raise HTTPException(422, f"invalid policy: {e.diag.message_primary}")

    @app.post("/clients/{client_id}/rotate-token")
    def rotate(client_id: UUID, staff: Staff = Depends(staff_dep), conn: psycopg.Connection = Depends(conn_dep)):
        roles_for(conn, staff, client_id)
        require_admin(staff)
        with tenant(conn, client_id):
            token = tenancy.rotate_public_token(conn, client_id)
        return {"upload_link": settings.upload_link(token)}

    @app.post("/clients/{client_id}/members", status_code=201)
    def add_member(client_id: UUID, body: MemberIn, staff: Staff = Depends(staff_dep),
                   conn: psycopg.Connection = Depends(conn_dep)):
        roles_for(conn, staff, client_id)
        require_admin(staff)
        with firm(conn, staff.firm_id):
            if not conn.execute("select 1 from users where id = %s", (body.user_id,)).fetchone():
                raise HTTPException(404, "user not in this firm")
            tenancy.add_member(conn, client_id, body.user_id, body.role)
        return {"ok": True}

    @app.get("/clients/{client_id}/documents")
    def list_documents(client_id: UUID, status: str | None = None, channel: str | None = None,
                       before: datetime | None = None, limit: int = Query(default=50, ge=1, le=200),
                       staff: Staff = Depends(staff_dep), conn: psycopg.Connection = Depends(conn_dep)):
        roles_for(conn, staff, client_id)
        where, args = ["true"], []
        if status:
            where.append("status = any(%s)")
            args.append(status.split(","))
        if channel:
            where.append("channel = %s")
            args.append(channel)
        if before:
            where.append("received_at < %s")
            args.append(before)
        with tenant(conn, client_id):
            return conn.execute(f"select {DOC_COLUMNS} from documents where {' and '.join(where)} "
                                f"order by received_at desc limit %s", [*args, limit]).fetchall()

    @app.get("/clients/{client_id}/documents/{document_id}")
    def get_document(client_id: UUID, document_id: UUID, staff: Staff = Depends(staff_dep),
                     conn: psycopg.Connection = Depends(conn_dep)):
        roles_for(conn, staff, client_id)
        with tenant(conn, client_id):
            doc = conn.execute(f"select {DOC_COLUMNS} from documents where id = %s", (document_id,)).fetchone()
            if not doc:
                raise HTTPException(404, "document not found")
            doc["assets"] = conn.execute("select kind, path, page_no from document_assets where document_id = %s "
                                         "order by kind, page_no", (document_id,)).fetchall()
            doc["jobs"] = conn.execute("select id, queue, state, attempts, last_error, updated_at from jobs "
                                       "where document_id = %s order by id", (document_id,)).fetchall()
        return doc

    @app.get("/clients/{client_id}/documents/{document_id}/raw-url")
    def raw_url(client_id: UUID, document_id: UUID, kind: Literal["raw", "page_image"] = "raw", page: int | None = None,
                staff: Staff = Depends(staff_dep), conn: psycopg.Connection = Depends(conn_dep)):
        roles_for(conn, staff, client_id)
        with tenant(conn, client_id):
            doc = conn.execute("select status, original_filename from documents where id = %s", (document_id,)).fetchone()
            if not doc:
                raise HTTPException(404, "document not found")
            if doc["status"] == "quarantined":
                raise HTTPException(409, "document is quarantined (virus); download refused")
            asset = conn.execute("select path from document_assets where document_id = %s and kind = %s "
                                 "and page_no is not distinct from %s", (document_id, kind, page)).fetchone()
        if not asset:
            raise HTTPException(404, "asset not found")
        name = doc["original_filename"] if kind == "raw" else ""
        return {"url": store.signed_url(asset["path"], SIGNED_URL_TTL, name), "expires_in": SIGNED_URL_TTL}

    @app.post("/clients/{client_id}/csv", status_code=201)
    async def csv_door(client_id: UUID, file: UploadFile = File(...), staff: Staff = Depends(staff_dep),
                       conn: psycopg.Connection = Depends(conn_dep)):
        roles_for(conn, staff, client_id)
        data = await file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, "CSV too large")
        try:
            parts = tenancy.csv_rows_as_parts(file.filename or "bills.csv", data)
        except (ValueError, UnicodeDecodeError) as e:
            raise HTTPException(422, f"bad CSV: {e}")
        meta = {"uploaded_by": str(staff.user_id), "csv_file": file.filename}
        with tenant(conn, client_id):
            received = [intake.store_document(conn, store, client_id, "csv", p, meta) for p in parts]
        return {"received": received}

    @app.post("/clients/{client_id}/erp/pair", status_code=201)
    def pair(client_id: UUID, body: PairIn, staff: Staff = Depends(staff_dep), conn: psycopg.Connection = Depends(conn_dep)):
        roles_for(conn, staff, client_id)
        require_admin(staff)
        with tenant(conn, client_id):
            token = tenancy.pair_erp_agent(conn, client_id, body.erp_type, body.company_name)
        return {"agent_token": token, "note": "shown once; paste into the FinLedger site agent"}

    @app.get("/clients/{client_id}/jobs")
    def list_jobs(client_id: UUID, state: str = "dead", staff: Staff = Depends(staff_dep),
                  conn: psycopg.Connection = Depends(conn_dep)):
        roles_for(conn, staff, client_id)
        with tenant(conn, client_id):
            return conn.execute("select id, document_id, queue, state, attempts, last_error, updated_at from jobs "
                                "where state = %s order by updated_at desc limit 200", (state,)).fetchall()

    @app.post("/clients/{client_id}/jobs/{job_id}/requeue")
    def requeue(client_id: UUID, job_id: int, staff: Staff = Depends(staff_dep), conn: psycopg.Connection = Depends(conn_dep)):
        roles_for(conn, staff, client_id)
        require_admin(staff)
        with tenant(conn, client_id):  # RLS check that the job belongs to this client
            if not conn.execute("select 1 from jobs where id = %s", (job_id,)).fetchone():
                raise HTTPException(404, "job not found")
            ok = conn.execute("select requeue_dead_job(%s) as ok", (job_id,)).fetchone()["ok"]
        if not ok:
            raise HTTPException(409, "job is not dead")
        return {"ok": True}

    # ---------- Person 4 site agent ----------

    def agent_dep(authorization: str | None = Header(default=None),
                  conn: psycopg.Connection = Depends(conn_dep)) -> UUID:
        cid = conn.execute("select client_by_agent_token(%s) as id",
                           (tenancy.token_hash(bearer(authorization)),)).fetchone()["id"]
        if cid is None:
            raise HTTPException(401, "invalid agent token")
        return cid

    @app.post("/agent/masters", status_code=201)
    def agent_masters(body: MastersIn, client_id: UUID = Depends(agent_dep), conn: psycopg.Connection = Depends(conn_dep)):
        with tenant(conn, client_id):
            snap = tenancy.write_masters(conn, client_id, body.kind, body.items)
        return {"snapshot_id": snap, "items": len(body.items)}

    @app.post("/agent/open-bills", status_code=201)
    def agent_open_bills(body: OpenBillsIn, client_id: UUID = Depends(agent_dep),
                         conn: psycopg.Connection = Depends(conn_dep)):
        with tenant(conn, client_id):
            n = tenancy.replace_open_bills(conn, client_id, [b.model_dump() for b in body.bills], body.as_of)
        return {"open_bills": n}

    @app.put("/agent/mapping")
    def agent_mapping(body: MappingIn, client_id: UUID = Depends(agent_dep), conn: psycopg.Connection = Depends(conn_dep)):
        with tenant(conn, client_id):
            tenancy.upsert_mapping(conn, client_id, body.kind, body.our_key, body.erp_value)
        return {"ok": True}

    return app
