"""Connections, tenant scoping, migrations.

Every tenant query runs inside `tenant(conn, client_id)`: a transaction with app.client_id set LOCAL, so the
setting dies with the transaction and a pooled connection can never leak one client into the next request.
"""
from __future__ import annotations

from contextlib import contextmanager
from importlib import resources
from typing import Iterator
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


class UnsafeRoleError(RuntimeError):
    pass


def assert_rls_applies(conn: psycopg.Connection) -> None:
    """Refuse to serve tenants from a role that silently bypasses Row Level Security."""
    row = conn.execute(
        """select r.rolsuper, r.rolbypassrls,
                  exists (select from pg_tables t where t.tablename = 'documents'
                          and pg_has_role(current_user, t.tableowner, 'USAGE')) as owns
             from pg_roles r where r.rolname = current_user"""
    ).fetchone()
    su, bypass, owns = row["rolsuper"], row["rolbypassrls"], row["owns"]
    if su or bypass or owns:
        raise UnsafeRoleError(
            f"role {conn.info.user!r} bypasses RLS (superuser={su}, bypassrls={bypass}, owner={owns}); "
            "connect the app as a member of finledger_app"
        )


def make_pool(dsn: str, *, check_role: bool = True, **kw) -> ConnectionPool:
    pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row, "autocommit": True}, open=True, **kw)
    if check_role:
        with pool.connection() as conn:
            assert_rls_applies(conn)
    return pool


@contextmanager
def tenant(conn: psycopg.Connection, client_id: UUID | str) -> Iterator[psycopg.Connection]:
    with conn.transaction():
        conn.execute("select set_config('app.client_id', %s, true)", (str(client_id),))
        yield conn


@contextmanager
def firm(conn: psycopg.Connection, firm_id: UUID | str) -> Iterator[psycopg.Connection]:
    with conn.transaction():
        conn.execute("select set_config('app.firm_id', %s, true)", (str(firm_id),))
        yield conn


def migrate(owner_dsn: str) -> list[str]:
    """Apply migrations/*.sql in name order, once each. Run as the schema owner, never as the app role."""
    applied: list[str] = []
    files = sorted(
        (f for f in resources.files("finledger_platform.migrations").iterdir() if f.name.endswith(".sql")),
        key=lambda f: f.name,
    )
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute("create table if not exists schema_migrations (name text primary key, applied_at timestamptz not null default now())")
        done = {r[0] for r in conn.execute("select name from schema_migrations")}
        for f in files:
            if f.name in done:
                continue
            with conn.transaction():
                conn.execute(f.read_text(encoding="utf-8"))
                conn.execute("insert into schema_migrations (name) values (%s)", (f.name,))
            applied.append(f.name)
    return applied
