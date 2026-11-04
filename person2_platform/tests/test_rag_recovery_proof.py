from pathlib import Path

import psycopg
import pytest


PROOF = Path(__file__).parents[1] / 'src/finledger_platform/migrations/recovery/verify_rag_isolation.sql'


def test_rag_proof_passes_and_detects_disabled_rls(owner_dsn, app_dsn):
    sql = PROOF.read_text(encoding='utf-8')
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(sql)
        assert conn.execute("select count(*) from firms where name = 'zz-proof-rag'").fetchone()[0] == 0
        conn.execute('alter table rag_chunks disable row level security')
        try:
            with pytest.raises(psycopg.errors.RaiseException, match='RAG isolation failed'):
                conn.execute(sql)
        finally:
            conn.execute('rollback')
            conn.execute('alter table rag_chunks enable row level security')
