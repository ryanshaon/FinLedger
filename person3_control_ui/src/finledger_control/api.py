"""FastAPI adapter for the control service and server-rendered review desk."""
from __future__ import annotations
import hashlib
import secrets
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .service import Conflict, ControlService, Forbidden, NotFound
from .web import render_inbox, render_review


def create_app(pool, source_url=lambda path, name: "") -> FastAPI:
    app=FastAPI(title="FinLedger Control & Review", version="0.1.0")

    def user(authorization: str | None = Header(None)):
        if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401,"Bearer token required")
        digest=hashlib.sha256(authorization[7:].encode()).digest()
        with pool.connection() as conn:
            row=conn.execute("select * from auth_user(%s)",(digest,)).fetchone()
        if not row: raise HTTPException(401,"Invalid token")
        return row

    def access(client_id, who, conn):
        roles=conn.execute("select user_client_roles(%s,%s) as roles",(who["user_id"],client_id)).fetchone()["roles"]
        if not roles: raise HTTPException(403,"No access to this client")
        return set(roles)

    def problem(exc):
        if isinstance(exc,NotFound): return 404
        if isinstance(exc,Forbidden): return 403
        return 409

    def verify_csrf(request: Request, csrf_token: str):
        cookie=request.cookies.get("finledger_csrf")
        if not cookie or not csrf_token or not secrets.compare_digest(cookie,csrf_token):
            raise HTTPException(403,"CSRF validation failed")
        source=request.headers.get("origin") or request.headers.get("referer")
        parsed=urlsplit(source or "")
        if parsed.scheme != request.url.scheme or parsed.netloc != request.url.netloc:
            raise HTTPException(403,"Untrusted request origin")

    @app.get("/healthz")
    def health(): return {"ok":True}

    @app.get("/clients/{client_id}/review",response_class=HTMLResponse)
    def inbox(client_id:UUID, queue:str=Query("needs_review"), who=Depends(user)):
        with pool.connection() as conn:
            access(client_id,who,conn); svc=ControlService(conn)
            try: items,counts=svc.inbox(client_id,queue),svc.queue_counts(client_id)
            except ValueError as exc: raise HTTPException(400,str(exc))
            with conn.transaction():
                conn.execute("select set_config('app.client_id',%s,true)",(str(client_id),))
                client=conn.execute("select name from clients where id=%s",(client_id,)).fetchone()
            return HTMLResponse(render_inbox(items,counts,queue,client["name"]))

    @app.get("/clients/{client_id}/review/{document_id}",response_class=HTMLResponse)
    def review(request:Request,client_id:UUID,document_id:UUID,who=Depends(user)):
        with pool.connection() as conn:
            roles=access(client_id,who,conn)
            try: detail=ControlService(conn).review_detail(client_id,document_id,source_url)
            except (NotFound,Forbidden,Conflict) as exc: raise HTTPException(problem(exc),str(exc))
            token=secrets.token_urlsafe(32)
            response=HTMLResponse(render_review(detail,roles,token))
            response.set_cookie("finledger_csrf",token,httponly=True,secure=request.url.scheme=="https",
                                samesite="strict",path=f"/clients/{client_id}/review")
            return response

    @app.post("/clients/{client_id}/review/{document_id}/approve")
    def approve(request:Request,client_id:UUID,document_id:UUID,revision:int=Form(...),post:bool=Form(...),csrf_token:str=Form(...),who=Depends(user)):
        verify_csrf(request,csrf_token)
        with pool.connection() as conn:
            access(client_id,who,conn)
            try: ControlService(conn).approve(client_id,document_id,who["user_id"],post=post,expected_revision=revision)
            except (NotFound,Forbidden,Conflict) as exc: raise HTTPException(problem(exc),str(exc))
        return RedirectResponse(f"/clients/{client_id}/review/{document_id}",303)

    @app.post("/clients/{client_id}/review/{document_id}/edit")
    def edit(request:Request,client_id:UUID,document_id:UUID,revision:int=Form(...),field:str=Form(...),value:str=Form(...),csrf_token:str=Form(...),who=Depends(user)):
        verify_csrf(request,csrf_token)
        with pool.connection() as conn:
            roles=access(client_id,who,conn)
            if not roles & {"ap_clerk","approver","firm_admin"}: raise HTTPException(403,"Edit role required")
            try: ControlService(conn).edit_field(client_id,document_id,who["user_id"],field,value,expected_revision=revision)
            except (NotFound,Forbidden,Conflict) as exc: raise HTTPException(problem(exc),str(exc))
        return RedirectResponse(f"/clients/{client_id}/review/{document_id}",303)

    @app.post("/clients/{client_id}/review/{document_id}/reject")
    def reject(request:Request,client_id:UUID,document_id:UUID,revision:int=Form(...),resubmit:bool=Form(False),note:str=Form("Reviewer requested correction"),csrf_token:str=Form(...),who=Depends(user)):
        verify_csrf(request,csrf_token)
        with pool.connection() as conn:
            access(client_id,who,conn)
            try: ControlService(conn).reject(client_id,document_id,who["user_id"],note,resubmit=resubmit,expected_revision=revision)
            except (NotFound,Forbidden,Conflict) as exc: raise HTTPException(problem(exc),str(exc))
        return RedirectResponse(f"/clients/{client_id}/review?queue=exception",303)
    return app

