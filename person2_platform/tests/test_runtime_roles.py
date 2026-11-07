from pathlib import Path

import psycopg


def test_runtime_roles_start_disabled_and_have_only_their_worker_role(owner_dsn, app_dsn):
    roles = {'fl_api': None, 'fl_control': None, 'fl_ingest': 'ingest',
             'fl_score': 'score', 'fl_outbox': None}
    script = Path(__file__).parents[1] / 'src/finledger_platform/migrations/recovery/provision_runtime_roles.sql'
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        assert not conn.execute('select exists(select 1 from pg_roles where rolname = any(%s))', (list(roles),)).fetchone()[0]
        created = False
        try:
            conn.execute(script.read_text(encoding='utf-8'))
            created = True
            for role, worker in roles.items():
                flags = conn.execute('select rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication from pg_roles where rolname=%s', (role,)).fetchone()
                assert flags == (False, False, False, False, False, False)
                assert conn.execute("select pg_has_role(%s, 'finledger_app', 'member')", (role,)).fetchone()[0]
                for queue in ('ingest', 'extract', 'score', 'map', 'post'):
                    member = conn.execute('select pg_has_role(%s, %s, %s)', (role, 'finledger_worker_' + queue, 'member')).fetchone()[0]
                    assert member == (queue == worker)
                assert not conn.execute("select pg_has_role(%s, 'finledger_queue_ops', 'member')", (role,)).fetchone()[0]
        finally:
            if created:
                for role in roles:
                    conn.execute('drop role if exists ' + role)
