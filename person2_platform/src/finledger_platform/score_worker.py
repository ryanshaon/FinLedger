"""score_worker: claim `score` -> Person 3 risk scoring -> job settled in the same transaction.

Outcomes per job:
    already past 'extracted' (replay)   -> job done, nothing written ("skipped")
    payload names another tenant/doc    -> document 'exception' (score_payload_rejected), scorer never called
    invoice rejected by the scorer      -> document 'exception' (canonical_invoice_rejected)
    store/DB/other errors               -> job retries with backoff, then 'dead' (requeue_dead_job replays it)
    ok                                  -> document 'scored' with its risk assessment, written by Person 3

The maker is resolved from the database (automation_maker_id), never trusted from the payload. Scoring and
finish_job share one tenant transaction, so a worker whose lease expired cannot land a result. Risk and review
rules stay in Person 3; this module only runs the queue.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

from .db import tenant
from .queue import claim, finish

log = logging.getLogger("finledger.score")

PAYLOAD_REJECTED = "score_payload_rejected"
INVOICE_REJECTED = "canonical_invoice_rejected"

# score(conn, client_id=, document_id=, invoice=, maker_id=) writes the scored state inside the caller's
# tenant transaction, or raises ScoreRejected when the invoice can never be scored.
ScoreFn = Callable[..., Any]


class ScoreRejected(ValueError):
    """The canonical invoice failed Person 3's boundary; retrying cannot help."""


class LeaseLost(RuntimeError):
    """Another worker owns the job now; this worker's result must not be settled."""


def _retry_reason(error: Exception) -> str:
    """Only fixed diagnostic categories may reach jobs.last_error or logs."""
    if isinstance(error, psycopg.Error):
        return "score database operation failed"
    return "score processing failed"


def _payload_ok(job: dict) -> bool:
    p = job["payload"]
    return (str(p.get("client_id")) == str(job["client_id"]) and str(p.get("document_id")) == str(job["document_id"])
            and isinstance(p.get("canonical_invoice"), dict))


def _exception(conn: psycopg.Connection, client_id, doc_id, maker, reason: str) -> None:
    conn.execute("""update documents set status = 'exception', status_reason = %s, updated_at = now()
                    where id = %s and status = 'extracted'""", (reason, doc_id))
    conn.execute("""insert into workflow_events (client_id, document_id, from_status, to_status, action, actor_id, metadata)
                    values (%s, %s, 'extracted', 'exception', 'score_exception', %s, %s)""",
                 (client_id, doc_id, maker, Jsonb({"reason": reason})))


def process_score(conn: psycopg.Connection, score: ScoreFn, job: dict) -> str:
    client_id, doc_id = str(job["client_id"]), str(job["document_id"])
    with tenant(conn, client_id):
        doc = conn.execute("select status from documents where id = %s for update", (doc_id,)).fetchone()
        if doc is None:
            outcome = "dead"
        elif doc["status"] != "extracted":
            outcome = "skipped"
        else:
            maker = conn.execute("select automation_maker_id(%s) as id", (client_id,)).fetchone()["id"]
            if not _payload_ok(job):
                _exception(conn, client_id, doc_id, maker, PAYLOAD_REJECTED)
                outcome = "exception"
            else:
                try:
                    with conn.transaction():  # savepoint: a rejected invoice leaves nothing half-written
                        score(conn, client_id=client_id, document_id=doc_id,
                              invoice=job["payload"]["canonical_invoice"], maker_id=str(maker))
                    outcome = "scored"
                except ScoreRejected:
                    _exception(conn, client_id, doc_id, maker, INVOICE_REJECTED)
                    outcome = "exception"
        if outcome == "dead":
            state = finish(conn, job["id"], job["claim_token"], ok=False, error="document row missing", permanent=True)
        else:
            state = finish(conn, job["id"], job["claim_token"], ok=True)
        if state is None:
            raise LeaseLost()  # rolls back the scored state with the transaction
    return outcome


def run_once(conn: psycopg.Connection, score: ScoreFn, lease_seconds: int = 300) -> str | None:
    """Keep claim, processing and settlement failures safe for the worker's stderr."""
    try:
        return _run_once(conn, score, lease_seconds)
    except Exception:
        raise RuntimeError("score worker operation failed") from None


def _run_once(conn: psycopg.Connection, score: ScoreFn, lease_seconds: int) -> str | None:
    job = claim(conn, "score", lease_seconds)
    if job is None:
        return None
    try:
        return process_score(conn, score, job)
    except LeaseLost:
        log.warning("score job %s lost its lease; result discarded", job["id"])
        return "lease_lost"
    except Exception as e:
        reason = _retry_reason(e)
        log.error("score job %s failed: %s", job["id"], reason)
        return finish(conn, job["id"], job["claim_token"], ok=False, error=reason) or "lease_lost"


def default_score() -> ScoreFn:
    """Person 3's ControlService.score as the automation maker; its boundary rejections become ScoreRejected."""
    from finledger_control.service import Conflict, ControlService

    def score(conn, *, client_id, document_id, invoice, maker_id):
        try:
            ControlService(conn).score(client_id, document_id, invoice, maker_id)
        except Conflict:
            # The status was checked under the row lock, so a Conflict here is the invoice boundary.
            # Its message can quote invoice fields: never log or store it.
            raise ScoreRejected() from None

    return score


def run_forever(pool, score: ScoreFn, idle_sleep: float = 1.0) -> None:
    log.info("score worker started")
    while True:
        with pool.connection() as conn:
            did = run_once(conn, score)
        if did is None:
            time.sleep(idle_sleep)
