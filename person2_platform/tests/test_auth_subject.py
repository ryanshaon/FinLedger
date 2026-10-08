"""008: Supabase Auth subject -> staff user lookup is private, narrow, and explicit-link only."""
from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from finledger_platform import tenancy


def test_lookup_resolves_only_explicitly_linked_subject(owner, conn):
    firm_id, user_id, _ = tenancy.create_firm_with_admin(owner, "Sharma & Co CAs", "admin@sharma.test")
    sub = uuid4()
    lookup = "select * from finledger_private.auth_user_by_subject(%s)"
    assert conn.execute(lookup, (sub,)).fetchall() == []  # not linked yet: nothing, no email fallback
    owner.execute("update users set auth_subject = %s where id = %s", (sub, user_id))
    assert conn.execute(lookup, (sub,)).fetchall() == [{"user_id": user_id, "firm_id": firm_id, "firm_admin": True}]
    assert conn.execute(lookup, (uuid4(),)).fetchall() == []


def test_subject_links_to_at_most_one_user(owner):
    _, u1, _ = tenancy.create_firm_with_admin(owner, "Firm One", "a@one.test")
    _, u2, _ = tenancy.create_firm_with_admin(owner, "Firm Two", "b@two.test")
    sub = uuid4()
    owner.execute("update users set auth_subject = %s where id = %s", (sub, u1))
    with pytest.raises(psycopg.errors.UniqueViolation):
        owner.execute("update users set auth_subject = %s where id = %s", (sub, u2))


def test_lookup_is_private_and_pinned(owner_dsn, app_dsn):
    with psycopg.connect(owner_dsn) as c:
        cfg, public_exec = c.execute(
            """select p.proconfig, has_function_privilege('public', p.oid, 'EXECUTE')
                 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                where n.nspname = 'finledger_private' and p.proname = 'auth_user_by_subject'""").fetchone()
        assert cfg == ["search_path=pg_catalog"]
        assert public_exec is False
        assert c.execute("select has_schema_privilege('public', 'finledger_private', 'USAGE')").fetchone()[0] is False
