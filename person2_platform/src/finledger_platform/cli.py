"""finledger-platform migrate | create-firm | serve | worker"""
from __future__ import annotations

import argparse
import logging

import psycopg

from .config import Settings


def app():
    """ASGI factory: uvicorn finledger_platform.cli:app --factory"""
    from .api import create_app
    from .db import make_pool
    from .store import make_store

    s = Settings()
    return create_app(s, make_pool(s.database_url), make_store(s))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="finledger-platform")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply SQL migrations (owner DSN)")
    f = sub.add_parser("create-firm", help="bootstrap a firm and its first admin; prints the API token once")
    f.add_argument("--name", required=True)
    f.add_argument("--admin-email", required=True)
    sv = sub.add_parser("serve", help="run the API")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sub.add_parser("worker", help="run the ingest worker")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.cmd == "migrate":
        from .db import migrate

        s = Settings()
        print("applied:", migrate(s.owner_database_url) or "nothing new")
    elif args.cmd == "create-firm":
        from .tenancy import create_firm_with_admin

        s = Settings()
        with psycopg.connect(s.owner_database_url) as conn:
            firm_id, user_id, token = create_firm_with_admin(conn, args.name, args.admin_email)
        print(f"firm_id={firm_id}\nuser_id={user_id}\napi_token={token}   (shown once)")
    elif args.cmd == "serve":
        import uvicorn

        # proxy_headers: behind a load balancer, request.client.host becomes the real vendor IP for rate limits.
        uvicorn.run("finledger_platform.cli:app", factory=True, host=args.host, port=args.port, proxy_headers=True)
    elif args.cmd == "worker":
        from .db import make_pool
        from .store import make_store
        from .worker import run_forever

        s = Settings()
        run_forever(make_pool(s.database_url, min_size=1, max_size=2), make_store(s), s)


if __name__ == "__main__":
    main()
