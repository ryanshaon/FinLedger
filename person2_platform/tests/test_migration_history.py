"""Hosted and local migration trackers must agree before app startup."""

import psycopg

from finledger_platform.db import migrate


def test_migration_history_includes_hosted_auth_migrations_and_is_idempotent(owner_dsn):
    migrate(owner_dsn)
    with psycopg.connect(owner_dsn) as conn:
        names = {row[0] for row in conn.execute("select name from public.schema_migrations")}
    assert {"009_staff_sessions.sql", "010_mfa_session_rekey.sql",
            "011_hosted_auth_migration_history.sql"} <= names
    assert migrate(owner_dsn) == []
