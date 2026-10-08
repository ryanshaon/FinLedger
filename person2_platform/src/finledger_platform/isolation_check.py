"""Two-tenant Row Level Security proof against a real (e.g. hosted staging) database. Writes nothing.

Everything runs in ONE transaction on the owner connection that is always rolled back:
seed two firms and two clients, switch to `finledger_app` with SET LOCAL ROLE, then check that reads and
writes fail closed without tenant context and never cross a client or firm boundary. If the owner is not
already a member of finledger_app, the membership is granted inside the same transaction, so the rollback
removes it too.

    finledger-platform isolation-check          # uses FINLEDGER_OWNER_DATABASE_URL
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from uuid import uuid4

import psycopg
from psycopg import errors


@dataclass
class Report:
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def check(self, ok: bool, label: str) -> None:
        (self.passed if ok else self.failed).append(label)

    @property
    def ok(self) -> bool:
        return not self.failed and bool(self.passed)


class _Rollback(Exception):
    pass


def _count(conn: psycopg.Connection, sql: str, params=()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _set(conn: psycopg.Connection, client_id=None, firm_id=None) -> None:
    conn.execute("select set_config('app.client_id', %s, true), set_config('app.firm_id', %s, true)",
                 (str(client_id or ""), str(firm_id or "")))


def run(owner_dsn: str) -> Report:
    rep = Report()
    tag = "iso-" + secrets.token_hex(4)
    f1, f2, a, b = uuid4(), uuid4(), uuid4(), uuid4()
    with psycopg.connect(owner_dsn) as conn:
        try:
            with conn.transaction():
                me = conn.execute("select current_user").fetchone()[0]
                if not conn.execute("select pg_has_role(current_user, 'finledger_app', 'MEMBER')").fetchone()[0]:
                    conn.execute(f'grant finledger_app to "{me}"')

                conn.execute("insert into firms (id, name) values (%s, %s), (%s, %s)", (f1, tag + "-f1", f2, tag + "-f2"))
                conn.execute(
                    "insert into clients (id, firm_id, name, public_token, inbound_slug) values "
                    "(%s, %s, %s, %s, %s), (%s, %s, %s, %s, %s)",
                    (a, f1, tag + "-A", secrets.token_urlsafe(24), tag + "-a",
                     b, f2, tag + "-B", secrets.token_urlsafe(24), tag + "-b"))
                conn.execute("insert into vendors (client_id, name) values (%s, 'Vendor A'), (%s, 'Vendor B')", (a, b))

                conn.execute("set local role finledger_app")
                row = conn.execute("select r.rolbypassrls, r.rolsuper from pg_roles r where r.rolname = current_user").fetchone()
                rep.check(not row[0] and not row[1], "finledger_app has no BYPASSRLS/superuser")

                _set(conn)
                rep.check(_count(conn, "select count(*) from clients") == 0, "no tenant context: zero clients visible")
                rep.check(_count(conn, "select count(*) from vendors") == 0, "no tenant context: zero vendors visible")
                rep.check(_count(conn, "select count(*) from firms") == 0, "no tenant context: zero firms visible")

                _set(conn, client_id=a)
                ids = [r[0] for r in conn.execute("select id from clients")]
                rep.check(ids == [a], "client A context: sees only client A")
                rep.check(_count(conn, "select count(*) from vendors where client_id <> %s", (a,)) == 0,
                          "client A context: sees no other client's vendors")
                rep.check(conn.execute("update vendors set name = 'pwned' where client_id = %s", (b,)).rowcount == 0,
                          "client A context: cannot update client B vendors")
                rep.check(conn.execute("delete from vendors where client_id = %s", (b,)).rowcount == 0,
                          "client A context: cannot delete client B vendors")
                try:
                    with conn.transaction():
                        conn.execute("insert into vendors (client_id, name) values (%s, 'smuggled')", (b,))
                    rep.check(False, "client A context: cannot insert a vendor for client B")
                except errors.InsufficientPrivilege:
                    rep.check(True, "client A context: cannot insert a vendor for client B")

                _set(conn, firm_id=f2)
                ids = [r[0] for r in conn.execute("select id from clients")]
                rep.check(ids == [b], "firm 2 context: sees only its own client")
                rep.check(conn.execute("update clients set name = 'pwned' where id = %s", (a,)).rowcount == 0,
                          "firm 2 context: cannot update firm 1's client")
                raise _Rollback
        except _Rollback:
            pass
        rep.check(_count(conn, "select count(*) from firms where name like %s", (tag + "%",)) == 0,
                  "rollback left no synthetic rows")
    return rep
