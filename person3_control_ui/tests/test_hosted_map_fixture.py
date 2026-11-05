import json
import importlib.util
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from finledger_platform.db import assert_rls_applies, tenant
from finledger_platform.map_worker import default_map, process_map


ROOT = Path(__file__).resolve().parents[2]
RECOVERY = ROOT / 'person2_platform/src/finledger_platform/migrations/recovery'
FIRM = '00000000-0000-4000-8000-00000000fa10'
CLIENTS = ('00000000-0000-4000-8000-00000000ca10', '00000000-0000-4000-8000-00000000cb10')
DOCS = ('00000000-0000-4000-8000-00000000da10', '00000000-0000-4000-8000-00000000db10')


def assert_fixture_clean(owner):
    for table in ('rag_chunks', 'llm_usage_rows', 'llm_token_reservations', 'jobs', 'voucher_drafts', 'workflow_events', 'correction_events', 'documents', 'document_assets', 'canonical_invoices', 'risk_assessments', 'review_tasks', 'approvals', 'posting_attempts'):
        assert owner.execute(f'select count(*) from {table} where client_id = any(%s::uuid[])', (list(CLIENTS),)).fetchone()[0] == 0, table
    assert owner.execute('select count(*) from clients where firm_id=%s', (FIRM,)).fetchone()[0] == 0
    assert owner.execute('select count(*) from users where firm_id=%s', (FIRM,)).fetchone()[0] == 0
    assert owner.execute('select count(*) from firms where id=%s', (FIRM,)).fetchone()[0] == 0


def exercise_map_fixture(owner_dsn, app_dsn):
    with psycopg.connect(owner_dsn, autocommit=True) as owner:
        owner.execute((RECOVERY / 'hosted_map_seed.sql').read_text(encoding='utf-8'))
        try:
            with psycopg.connect(app_dsn, row_factory=dict_row) as conn:
                assert_rls_applies(conn)
                assert conn.execute("select pg_has_role(current_user,'finledger_worker_map','member') as allowed").fetchone()['allowed']
                for client_id, document_id in zip(CLIENTS, DOCS):
                    with tenant(conn, client_id):
                        other_doc = DOCS[1] if document_id == DOCS[0] else DOCS[0]
                        assert not conn.execute('select 1 from documents where id=%s', (other_doc,)).fetchone()
                        assert not conn.execute('select 1 from jobs where document_id=%s', (other_doc,)).fetchone()
                        job = conn.execute("select * from jobs where queue='map' and client_id=%s and document_id=%s and state='running'", (client_id, document_id)).fetchone()
                    assert job is not None
                    assert process_map(conn, default_map(conn), job) == 'mapped'
                    with tenant(conn, client_id):
                        row = conn.execute('select payload from voucher_drafts where document_id=%s', (job['document_id'],)).fetchone()
                        trace = conn.execute("select metadata from workflow_events where document_id=%s and action='map_trace'", (job['document_id'],)).fetchone()
                        assert row is not None and trace is not None
                        assert not conn.execute("select 1 from jobs where document_id=%s and queue='post'", (document_id,)).fetchone()
                        if client_id == CLIENTS[0]:
                            assert row['payload']['party_ledger'] == 'Corrected synthetic ledger'
                            assert trace['metadata']['party_ledger_from_memory'] == 'Corrected synthetic ledger'
                        else:
                            assert row['payload']['party_ledger'] == 'Synthetic Vendor (Proposed)'
                            assert trace['metadata']['party_ledger_from_memory'] is None
                            assert 'Corrected synthetic ledger' not in json.dumps((row, trace), default=str)
        finally:
            owner.execute((RECOVERY / 'hosted_map_cleanup.sql').read_text(encoding='utf-8'))
            assert_fixture_clean(owner)


def test_synthetic_map_fixture_and_cleanup(owner_dsn, app_dsn, monkeypatch):
    monkeypatch.setenv('FINLEDGER_ENV', 'development')
    monkeypatch.setenv('FINLEDGER_LLM_PROVIDER', 'mock')
    monkeypatch.delenv('FINLEDGER_GEMINI_LIVE', raising=False)
    exercise_map_fixture(owner_dsn, app_dsn)


def test_cleanup_when_mapping_fails(owner_dsn, app_dsn, monkeypatch):
    monkeypatch.setenv('FINLEDGER_ENV', 'development')
    monkeypatch.setenv('FINLEDGER_LLM_PROVIDER', 'mock')
    monkeypatch.delenv('FINLEDGER_GEMINI_LIVE', raising=False)
    def fail(*args):
        raise RuntimeError('synthetic mapping failure')
    monkeypatch.setitem(globals(), 'process_map', fail)
    with pytest.raises(RuntimeError, match='synthetic mapping failure'):
        exercise_map_fixture(owner_dsn, app_dsn)
    with psycopg.connect(owner_dsn) as owner:
        assert owner.execute('select count(*) from firms where id=%s', (FIRM,)).fetchone()[0] == 0


def test_cleanup_refuses_unexpected_document(owner_dsn, app_dsn):
    extra = '00000000-0000-4000-8000-00000000de10'
    with psycopg.connect(owner_dsn, autocommit=True) as owner:
        owner.execute((RECOVERY / 'hosted_map_seed.sql').read_text(encoding='utf-8'))
        try:
            owner.execute("insert into documents(id,client_id,channel,object_path,content_hash,mime,size_bytes) values (%s,%s,'portal',%s,repeat('e',64),'application/pdf',1)", (extra, CLIENTS[0], CLIENTS[0] + '/unexpected.pdf'))
            with pytest.raises(psycopg.errors.RaiseException, match='unexpected fixture state'):
                owner.execute((RECOVERY / 'hosted_map_cleanup.sql').read_text(encoding='utf-8'))
            owner.execute('rollback')
            assert owner.execute('select count(*) from documents where id=%s', (extra,)).fetchone()[0] == 1
        finally:
            owner.execute('rollback')
            owner.execute('delete from documents where id=%s', (extra,))
            owner.execute((RECOVERY / 'hosted_map_cleanup.sql').read_text(encoding='utf-8'))


def test_hosted_runner_rejects_wrong_target_before_connecting(monkeypatch):
    spec = importlib.util.spec_from_file_location('hosted_map_proof', ROOT / 'scripts/hosted_map_proof.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setenv('FINLEDGER_OWNER_DATABASE_URL', 'postgresql://postgres.wrong:fake@wrong.pooler.supabase.com:5432/postgres')
    monkeypatch.setenv('FINLEDGER_MAP_DATABASE_URL', 'postgresql://fl_map.wrong:fake@wrong.pooler.supabase.com:5432/postgres')
    def unexpected_connect(*args, **kwargs):
        raise AssertionError('must not connect')
    monkeypatch.setattr(runner.psycopg, 'connect', unexpected_connect)
    with pytest.raises(RuntimeError, match='unexpected database target'):
        runner.main()
