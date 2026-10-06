from __future__ import annotations
import argparse, os

from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

from .api import create_app


def main():
    p=argparse.ArgumentParser(prog="finledger-control"); p.add_argument("--host",default="127.0.0.1"); p.add_argument("--port",type=int,default=8770)
    args=p.parse_args(); dsn=os.environ.get("FINLEDGER_DATABASE_URL")
    if not dsn: raise SystemExit("FINLEDGER_DATABASE_URL is required")
    pool=ConnectionPool(dsn,kwargs={"row_factory":dict_row,"autocommit":True},min_size=1,max_size=8)
    from finledger_platform.store import LocalStore, S3Store
    kind=os.environ.get("FINLEDGER_STORE","local")
    if kind == "s3":
        store=S3Store(os.environ["FINLEDGER_S3_BUCKET"])
    else:
        store=LocalStore(os.environ.get("FINLEDGER_STORE_ROOT","./var/objects"),
                         os.environ["FINLEDGER_SIGNING_SECRET"].encode(),
                         os.environ.get("FINLEDGER_APP_BASE_URL","http://localhost:8000"))
    import uvicorn
    uvicorn.run(create_app(pool,source_url=lambda path,name: store.signed_url(path,300,name)),host=args.host,port=args.port)

