"""One-off synthetic map proof for the empty replacement staging database."""
import os
import sys
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'person3_control_ui/tests'))
from test_hosted_map_fixture import assert_fixture_clean, exercise_map_fixture


def main():
    if not __debug__:
        raise RuntimeError('assertions must be enabled')
    owner = os.environ['FINLEDGER_OWNER_DATABASE_URL']
    app = os.environ['FINLEDGER_MAP_DATABASE_URL']
    owner_info, app_info = conninfo_to_dict(owner), conninfo_to_dict(app)
    project = 'smnfnnwmasnsuuwrboge'
    if (owner_info.get('user') != 'postgres.' + project or app_info.get('user') != 'fl_map.' + project
            or owner_info.get('host') != app_info.get('host')
            or not owner_info.get('host', '').endswith('.pooler.supabase.com')
            or owner_info.get('port') != '5432' or app_info.get('port') != '5432'
            or owner_info.get('dbname') != 'postgres' or app_info.get('dbname') != 'postgres'):
        raise RuntimeError('unexpected database target')
    with psycopg.connect(owner, connect_timeout=15) as conn:
        if conn.execute('select count(*) from firms').fetchone()[0] != 0:
            raise RuntimeError('staging is not empty')
        assert_fixture_clean(conn)
    os.environ['FINLEDGER_ENV'] = 'development'
    os.environ['FINLEDGER_LLM_PROVIDER'] = 'mock'
    os.environ['FINLEDGER_LLM_KILL_SWITCH'] = 'true'
    for name in ('FINLEDGER_GEMINI_LIVE', 'OPENROUTER_API_KEY', 'GEMINI_API_KEY'):
        os.environ.pop(name, None)
    from usage.model_router import Settings
    if Settings.from_env().provider != 'mock':
        raise RuntimeError('mock provider required')
    try:
        exercise_map_fixture(owner, app)
    except Exception:
        print('FAIL: synthetic map proof; inspect fixture cleanup before retrying')
        raise
    print('PASS: synthetic map, correction reuse, tenant isolation, no post, cleanup')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Hosted proof failed:', type(exc).__name__)
        raise SystemExit(1) from None
