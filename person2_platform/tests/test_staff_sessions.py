"""Server-owned browser sessions: expiry and token storage are enforced in PostgreSQL."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

import psycopg
import pytest


def _digest(value: bytes) -> bytes:
    return sha256(value).digest()


def _start(conn, user_id, digest, ciphertext=b"encrypted-token-pair"):
    return conn.execute(
        "select finledger_private.staff_session_start(%s, %s, %s, %s)",
        (digest, user_id, ciphertext, datetime.now(timezone.utc) + timedelta(minutes=10)),
    )


def test_session_is_private_and_only_available_by_cookie_digest(owner, conn, world):
    digest = _digest(b"first-browser-cookie")
    _start(conn, world.admin1, digest)
    row = conn.execute("select * from finledger_private.staff_session_touch(%s)", (digest,)).fetchone()
    assert row["user_id"] == world.admin1
    assert row["credential_ciphertext"] == b"encrypted-token-pair"
    assert row["version"] == 1
    assert conn.execute("select * from finledger_private.staff_session_touch(%s)", (_digest(b"wrong"),)).fetchone() is None

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select * from finledger_private.staff_sessions")
    assert owner.execute(
        "select has_table_privilege('fl_app', 'finledger_private.staff_sessions', 'SELECT')"
    ).fetchone()[0] is False


def test_session_expiry_and_revocation(owner, conn, world):
    digest = _digest(b"revocable")
    _start(conn, world.admin1, digest)
    conn.execute("select finledger_private.staff_session_revoke(%s)", (digest,))
    assert conn.execute("select * from finledger_private.staff_session_touch(%s)", (digest,)).fetchone() is None

    expired = _digest(b"expired")
    _start(conn, world.admin1, expired)
    owner.execute(
        "update finledger_private.staff_sessions set idle_expires_at = now() - interval '1 second' where cookie_hash = %s",
        (expired,),
    )
    assert conn.execute("select * from finledger_private.staff_session_touch(%s)", (expired,)).fetchone() is None

    absolute = _digest(b"absolute")
    _start(conn, world.admin1, absolute)
    owner.execute(
        "update finledger_private.staff_sessions set absolute_expires_at = now() - interval '1 second' where cookie_hash = %s",
        (absolute,),
    )
    assert conn.execute("select * from finledger_private.staff_session_touch(%s)", (absolute,)).fetchone() is None


def test_refresh_is_compare_and_swap_and_cannot_resurrect_expired_session(owner, conn, world):
    digest = _digest(b"rotation")
    _start(conn, world.admin1, digest)
    future = datetime.now(timezone.utc) + timedelta(minutes=15)
    result = conn.execute(
        "select finledger_private.staff_session_rotate(%s, %s, %s, %s) as ok",
        (digest, 1, b"new-ciphertext", future),
    ).fetchone()
    assert result["ok"] is True
    assert conn.execute(
        "select finledger_private.staff_session_rotate(%s, %s, %s, %s) as ok",
        (digest, 1, b"stale-ciphertext", future),
    ).fetchone()["ok"] is False
    row = conn.execute("select * from finledger_private.staff_session_touch(%s)", (digest,)).fetchone()
    assert row["credential_ciphertext"] == b"new-ciphertext"
    assert row["version"] == 2

    owner.execute(
        "update finledger_private.staff_sessions set absolute_expires_at = now() - interval '1 second' where cookie_hash = %s",
        (digest,),
    )
    assert conn.execute(
        "select finledger_private.staff_session_rotate(%s, %s, %s, %s) as ok",
        (digest, 2, b"resurrection", future),
    ).fetchone()["ok"] is False


def test_session_start_rejects_bad_hash_and_unknown_user(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        _start(conn, uuid4(), b"short")
