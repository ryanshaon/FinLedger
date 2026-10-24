"""score worker: settlement, maker resolution, rejections, retries and lost leases, as the RLS-bound app role."""
from __future__ import annotations

import pytest
from test_extract_worker import INVOICE, Recorder, doc_row, ingested, maker_id
from test_link import jobs

from finledger_platform import score_worker
from finledger_platform.db import tenant
from finledger_platform.extract_worker import run_once as extract_once
from finledger_platform.score_worker import ScoreRejected, run_once


class Scorer:
    """Stands in for Person 3: writes the scored state inside the worker's transaction."""

    def __init__(self, error=None, during=None):
        self.calls, self.error, self.during = [], error, during

    def __call__(self, conn, **kw):
        self.calls.append(kw)
        conn.execute("update documents set status = 'scored', updated_at = now() where id = %s", (kw["document_id"],))
        if self.during:
            self.during()
        if self.error:
            raise self.error


def extracted(world, client, conn, store, settings):
    doc_id = ingested(world, client, conn, store, settings)
    assert extract_once(conn, store, Recorder()) == "extracted"
    return doc_id


def score_job(conn, world):
    [job] = jobs(conn, world.a["id"], "score")
    return job


def events(conn, world, doc_id):
    with tenant(conn, world.a["id"]):
        return conn.execute("select action, actor_id, metadata from workflow_events where document_id = %s order by id",
                            (doc_id,)).fetchall()


def test_success_scores_as_the_database_maker_and_finishes(world, client, conn, store, settings, owner):
    doc_id = extracted(world, client, conn, store, settings)
    owner.execute("""update jobs set payload = jsonb_set(payload, '{maker_id}', '"00000000-0000-4000-8000-000000000000"')
                     where queue = 'score'""")  # a tampered payload maker is ignored
    fake = Scorer()
    assert run_once(conn, fake) == "scored"
    [call] = fake.calls
    assert call["maker_id"] == str(maker_id(owner, world.firm1)) and call["invoice"] == INVOICE
    assert call["client_id"] == str(world.a["id"]) and call["document_id"] == doc_id
    assert doc_row(conn, world, doc_id)["status"] == "scored"
    assert score_job(conn, world)["state"] == "done"
    assert run_once(conn, fake) is None


def test_replayed_job_is_skipped(world, client, conn, store, settings, owner):
    doc_id = extracted(world, client, conn, store, settings)
    assert run_once(conn, Scorer()) == "scored"
    owner.execute("update jobs set state = 'queued', run_after = now() where queue = 'score'")
    again = Scorer()
    assert run_once(conn, again) == "skipped" and again.calls == []
    assert doc_row(conn, world, doc_id)["status"] == "scored"


def test_rejected_invoice_is_a_document_exception_with_nothing_half_written(world, client, conn, store, settings, owner):
    doc_id = extracted(world, client, conn, store, settings)
    assert run_once(conn, Scorer(error=ScoreRejected())) == "exception"
    assert doc_row(conn, world, doc_id) == {"status": "exception", "status_reason": "canonical_invoice_rejected"}
    last = events(conn, world, doc_id)[-1]
    assert last["action"] == "score_exception" and last["actor_id"] == maker_id(owner, world.firm1)
    assert score_job(conn, world)["state"] == "done"


def test_payload_for_another_document_never_reaches_the_scorer(world, client, conn, store, settings, owner):
    doc_id = extracted(world, client, conn, store, settings)
    owner.execute("""update jobs set payload = jsonb_set(payload, '{document_id}', '"00000000-0000-4000-8000-000000000000"')
                     where queue = 'score'""")
    fake = Scorer()
    assert run_once(conn, fake) == "exception" and fake.calls == []
    assert doc_row(conn, world, doc_id) == {"status": "exception", "status_reason": "score_payload_rejected"}


def test_transient_failure_retries_with_a_fixed_reason(world, client, conn, store, settings):
    doc_id = extracted(world, client, conn, store, settings)
    assert run_once(conn, Scorer(error=RuntimeError("vendor GSTIN 27AAPFU0939F1ZV leaked"))) == "queued"
    job = score_job(conn, world)
    assert job["state"] == "queued" and job["last_error"] == "score processing failed"
    assert doc_row(conn, world, doc_id)["status"] == "extracted"  # the scorer's write rolled back


def test_lost_lease_rolls_back_the_score(world, client, conn, store, settings, owner):
    doc_id = extracted(world, client, conn, store, settings)
    steal = lambda: owner.execute("update jobs set claim_token = gen_random_uuid() where queue = 'score'")
    assert run_once(conn, Scorer(during=steal)) == "lease_lost"
    assert doc_row(conn, world, doc_id)["status"] == "extracted"


def test_errors_reaching_run_once_are_generic(conn, monkeypatch):
    monkeypatch.setattr(score_worker, "claim", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("dsn secret")))
    with pytest.raises(RuntimeError, match="^score worker operation failed$"):
        run_once(conn, Scorer())
