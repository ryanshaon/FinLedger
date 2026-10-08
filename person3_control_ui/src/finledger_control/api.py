"""FastAPI adapter for the control service and server-rendered review desk."""
from __future__ import annotations
import hashlib
import os
import secrets
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from finledger_platform.browser_sessions import SessionDenied
from finledger_platform.db import firm
from .service import Conflict, ControlService, Forbidden, NotFound
from .web import render_home, render_inbox, render_login, render_mfa, render_review


def create_app(pool, source_url=lambda path, name: "", browser_sessions=None) -> FastAPI:
    app=FastAPI(title="FinLedger Control & Review", version="0.1.0")

    @app.middleware("http")
    async def security_headers(request:Request,call_next):
        response=await call_next(request)
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="no-referrer"
        response.headers["X-Frame-Options"]="DENY"
        if browser_sessions is not None:
            response.headers["Cache-Control"]="no-store"
        return response

    def secure_browser(request: Request) -> bool:
        return request.url.scheme == "https" or os.environ.get("FINLEDGER_ENV","").lower() in {"staging","production"}

    def session_cookie_name(request: Request) -> str:
        return "__Host-fl_session" if secure_browser(request) else "fl_dev_session"

    def user(request: Request, authorization: str | None = Header(None)):
        if browser_sessions is not None:
            cookie=request.cookies.get(session_cookie_name(request))
            if not cookie: raise HTTPException(303,headers={"Location":"/login"})
            try:
                with pool.connection() as conn: staff=browser_sessions.resolve(conn,cookie)
            except SessionDenied:
                raise HTTPException(303,headers={"Location":"/login"}) from None
            return {"user_id":staff.user_id,"firm_id":staff.firm_id,"firm_admin":staff.firm_admin,"aal":staff.aal}
        if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401,"Bearer token required")
        digest=hashlib.sha256(authorization[7:].encode()).digest()
        with pool.connection() as conn:
            row=conn.execute("select * from auth_user(%s)",(digest,)).fetchone()
        if not row: raise HTTPException(401,"Invalid token")
        return row

    def access(client_id, who, conn):
        roles=conn.execute("select user_client_roles(%s,%s) as roles",(who["user_id"],client_id)).fetchone()["roles"]
        if not roles: raise HTTPException(403,"No access to this client")
        if "aal" in who and who["aal"] != "aal2":
            raise HTTPException(303,headers={"Location":"/mfa"})
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

    def verify_login_csrf(request: Request, csrf_token: str):
        cookie=request.cookies.get("fl_login_csrf")
        if not cookie or not secrets.compare_digest(cookie, csrf_token):
            raise HTTPException(403,"CSRF validation failed")
        source=request.headers.get("origin") or request.headers.get("referer")
        parsed=urlsplit(source or "")
        if parsed.scheme != request.url.scheme or parsed.netloc != request.url.netloc:
            raise HTTPException(403,"Untrusted request origin")

    if browser_sessions is not None:
        @app.get("/",response_class=HTMLResponse)
        def home(request:Request,who=Depends(user)):
            if who["aal"] != "aal2":
                raise HTTPException(303,headers={"Location":"/mfa"})
            with pool.connection() as conn, firm(conn,who["firm_id"]):
                if who["firm_admin"]:
                    clients=conn.execute("select id,name from clients order by name").fetchall()
                else:
                    clients=conn.execute("select c.id,c.name from clients c where exists "
                                         "(select 1 from client_members m where m.client_id=c.id and m.user_id=%s) "
                                         "order by c.name",(who["user_id"],)).fetchall()
            token=request.cookies.get("fl_login_csrf") or secrets.token_urlsafe(32)
            response=HTMLResponse(render_home(clients,token),headers={"Cache-Control":"no-store"})
            response.set_cookie("fl_login_csrf",token,httponly=True,secure=secure_browser(request),
                                samesite="strict",path="/")
            return response

        @app.get("/login",response_class=HTMLResponse)
        def login_page(request:Request):
            token=secrets.token_urlsafe(32)
            response=HTMLResponse(render_login(token),headers={"Cache-Control":"no-store"})
            response.set_cookie("fl_login_csrf",token,httponly=True,secure=secure_browser(request),
                                samesite="strict",path="/")
            return response

        @app.post("/login")
        def login(request:Request,email:str=Form(...,max_length=320),password:str=Form(...,max_length=1024),
                  csrf_token:str=Form(...)):
            verify_login_csrf(request,csrf_token)
            try:
                with pool.connection() as conn: cookie=browser_sessions.sign_in(conn,email,password)
            except SessionDenied:
                return HTMLResponse(render_login(csrf_token,"Sign-in failed or this account is not provisioned."),
                                    status_code=401,headers={"Cache-Control":"no-store"})
            response=RedirectResponse("/",303,headers={"Cache-Control":"no-store"})
            response.set_cookie(session_cookie_name(request),cookie,httponly=True,
                                secure=secure_browser(request),samesite="lax",path="/")
            return response

        @app.post("/logout")
        def logout(request:Request,csrf_token:str=Form(...)):
            verify_login_csrf(request,csrf_token)
            cookie=request.cookies.get(session_cookie_name(request))
            if cookie:
                with pool.connection() as conn: browser_sessions.sign_out(conn,cookie)
            response=RedirectResponse("/login",303,headers={"Cache-Control":"no-store"})
            response.delete_cookie(session_cookie_name(request),path="/")
            return response

        @app.get("/mfa",response_class=HTMLResponse)
        def mfa_page(request:Request,who=Depends(user)):
            cookie=request.cookies.get(session_cookie_name(request))
            try:
                with pool.connection() as conn: factors=browser_sessions.mfa_factors(conn,cookie)
            except SessionDenied:
                raise HTTPException(303,headers={"Location":"/login"}) from None
            token=request.cookies.get("fl_login_csrf") or secrets.token_urlsafe(32)
            response=HTMLResponse(render_mfa(token,factors[0] if factors else ""),
                                  headers={"Cache-Control":"no-store"})
            response.set_cookie("fl_login_csrf",token,httponly=True,secure=secure_browser(request),
                                samesite="strict",path="/")
            return response

        @app.post("/mfa/enroll",response_class=HTMLResponse)
        def mfa_enroll(request:Request,csrf_token:str=Form(...),who=Depends(user)):
            verify_login_csrf(request,csrf_token)
            cookie=request.cookies.get(session_cookie_name(request))
            try:
                with pool.connection() as conn: factor,secret=browser_sessions.mfa_enroll(conn,cookie)
            except SessionDenied:
                raise HTTPException(503,"MFA setup unavailable") from None
            return HTMLResponse(render_mfa(csrf_token,factor,secret),headers={"Cache-Control":"no-store"})

        @app.post("/mfa/verify")
        def mfa_verify(request:Request,csrf_token:str=Form(...),factor_id:str=Form(...),code:str=Form(...),
                       who=Depends(user)):
            verify_login_csrf(request,csrf_token)
            cookie=request.cookies.get(session_cookie_name(request))
            try:
                with pool.connection() as conn: upgraded_cookie=browser_sessions.mfa_verify(conn,cookie,factor_id,code)
            except SessionDenied:
                return HTMLResponse(render_mfa(csrf_token,factor_id,error="Code could not be verified. Try again."),
                                    status_code=400,headers={"Cache-Control":"no-store"})
            response=RedirectResponse("/",303,headers={"Cache-Control":"no-store"})
            response.set_cookie(session_cookie_name(request),upgraded_cookie,httponly=True,
                                secure=secure_browser(request),samesite="lax",path="/")
            return response

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
            response.set_cookie("finledger_csrf",token,httponly=True,secure=secure_browser(request),
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

