from __future__ import annotations
import argparse, os

from .api import create_app


def browser_sessions_from_env():
    from finledger_platform.auth_gateway import SupabaseAuthClient
    from finledger_platform.browser_sessions import BrowserSessions
    from finledger_platform.supabase_auth import SupabaseStaffJWTVerifier

    url=os.environ.get("SUPABASE_URL","").rstrip("/")
    key=os.environ.get("SUPABASE_PUBLISHABLE_KEY","")
    enc=os.environ.get("FINLEDGER_ENC_KEY","")
    if not all((url,key,enc)):
        if os.environ.get("FINLEDGER_ENV","").lower() in {"production","staging"}:
            raise RuntimeError("Supabase browser authentication configuration is required")
        return None  # Legacy bearer mode for local development only.
    return BrowserSessions(SupabaseAuthClient(url,key),SupabaseStaffJWTVerifier(url+"/auth/v1"),enc)


def staff_invitations_from_env():
    """Invitations need the Supabase secret key; without it the invite page reports "not configured"."""
    url=os.environ.get("SUPABASE_URL","").rstrip("/")
    secret=os.environ.get("SUPABASE_SECRET_KEY","")
    if not (url and secret):
        return None
    import logging
    from finledger_platform.staff_invites import StaffInvitations, SupabaseAdminClient
    return StaffInvitations(SupabaseAdminClient(url,secret),logger=logging.getLogger("finledger.invites").warning)


def main():
    p=argparse.ArgumentParser(prog="finledger-control"); p.add_argument("--host",default="127.0.0.1"); p.add_argument("--port",type=int,default=8770)
    args=p.parse_args(); dsn=os.environ.get("FINLEDGER_DATABASE_URL")
    if not dsn: raise SystemExit("FINLEDGER_DATABASE_URL is required")
    # Same pool as Person 2: refuses superuser/BYPASSRLS/owner roles so RLS always applies.
    from finledger_platform.db import make_pool
    pool=make_pool(dsn,min_size=1,max_size=8)
    from finledger_platform.store import LocalStore, S3Store
    kind=os.environ.get("FINLEDGER_STORE","local")
    if kind == "s3":
        store=S3Store(os.environ["FINLEDGER_S3_BUCKET"],
                      endpoint_url=os.environ.get("FINLEDGER_S3_ENDPOINT_URL",""),
                      region=os.environ.get("FINLEDGER_S3_REGION",""),
                      addressing_style=os.environ.get("FINLEDGER_S3_ADDRESSING_STYLE",""),
                      server_side_encryption=os.environ.get("FINLEDGER_S3_SERVER_SIDE_ENCRYPTION","auto"))
    else:
        store=LocalStore(os.environ.get("FINLEDGER_STORE_ROOT","./var/objects"),
                         os.environ["FINLEDGER_SIGNING_SECRET"].encode(),
                         os.environ.get("FINLEDGER_APP_BASE_URL","http://localhost:8000"))
    import uvicorn
    uvicorn.run(create_app(pool,source_url=lambda path,name: store.signed_url(path,300,name),
                           browser_sessions=browser_sessions_from_env(),
                           staff_invitations=staff_invitations_from_env()),
                host=args.host,port=args.port,proxy_headers=True)
