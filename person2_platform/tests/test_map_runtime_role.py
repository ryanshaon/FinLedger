from pathlib import Path

import psycopg


RECOVERY = Path(__file__).parents[1] / 'src/finledger_platform/migrations/recovery'


def test_map_role_provisioning_starts_disabled_and_limits_privileges(owner_dsn, app_dsn):
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        assert not conn.execute("select exists(select 1 from pg_roles where rolname = 'fl_map')").fetchone()[0]
        try:
            conn.execute((RECOVERY / 'provision_fl_map.sql').read_text(encoding='utf-8'))
            row = conn.execute("select rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb from pg_roles where rolname = 'fl_map'").fetchone()
            assert row == (False, False, False, False, False)
            assert conn.execute("select pg_has_role('fl_map', 'finledger_app', 'member') and pg_has_role('fl_map', 'finledger_worker_map', 'member')").fetchone()[0]
            assert not conn.execute("select pg_has_role('fl_map', 'finledger_worker_extract', 'member')").fetchone()[0]
            conn.execute((RECOVERY / 'enable_fl_map.sql').read_text(encoding='utf-8'))
            assert conn.execute("select rolcanlogin from pg_roles where rolname = 'fl_map'").fetchone()[0]
        finally:
            conn.execute('drop role if exists fl_map')
