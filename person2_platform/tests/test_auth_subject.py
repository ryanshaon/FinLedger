"""A verified Supabase subject resolves to exactly one provisioned FinLedger staff user."""

from uuid import uuid4

import psycopg
import pytest


def test_subject_lookup_requires_explicit_link_and_works_before_tenant_is_known(owner, conn, world):
    subject = uuid4()
    assert conn.execute(
        "select * from finledger_private.auth_user_by_subject(%s)", (subject,)
    ).fetchone() is None

    owner.execute("update users set auth_subject = %s where id = %s", (subject, world.admin1))
    row = conn.execute(
        "select * from finledger_private.auth_user_by_subject(%s)", (subject,)
    ).fetchone()
    assert row == {"user_id": world.admin1, "firm_id": world.firm1, "firm_admin": True}
    assert conn.execute(
        "select * from finledger_private.auth_user_by_subject(%s)", (uuid4(),)
    ).fetchone() is None


def test_subject_cannot_be_linked_to_two_users(owner, world):
    subject = uuid4()
    owner.execute("update users set auth_subject = %s where id = %s", (subject, world.admin1))
    with pytest.raises(psycopg.errors.UniqueViolation):
        owner.execute("update users set auth_subject = %s where id = %s", (subject, world.admin2))
