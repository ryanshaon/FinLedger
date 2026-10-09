"""Worker-owned diagnostics must not echo exception payloads or chained tracebacks."""
import logging
import traceback

import psycopg
import pytest

from conftest import invoice_pdf
from test_link import jobs, send
from test_outbox_worker import queue_reply, state

from finledger_platform import outbox_worker, prep, queue, virus, worker
from finledger_platform.db import tenant

CANARY = "synthetic-invoice-and-credential-canary-4499"


def fail_with_payload(error_type):
    def fail(*args, **kwargs):
        try:
            raise ValueError(CANARY)
        except ValueError as cause:
            error = error_type(f"provider rejected {CANARY}")
            error.add_note(CANARY)
            raise error from cause
    return fail


@pytest.mark.parametrize("error_type,outcome,reason", [
    (RuntimeError, "queued", "ingestion processing failed"),
    (FileNotFoundError, "queued", "ingestion I/O failed"),
    (psycopg.OperationalError, "queued", "ingestion database operation failed"),
    (virus.ScannerError, "queued", "virus scanner unavailable"),
    (queue.PermanentError, "dead", "permanent ingestion failure"),
])
def test_ingest_diagnostics_do_not_echo_failure_payload(
    world, client, conn, store, settings, monkeypatch, caplog, error_type, outcome, reason,
):
    send(client, world.a["public_token"], ("invoice.pdf", invoice_pdf(), "application/pdf"))
    monkeypatch.setattr(store, "get", fail_with_payload(error_type))
    with caplog.at_level(logging.ERROR, logger="finledger.ingest"):
        assert worker.run_once(conn, store, settings) == outcome
    [job] = jobs(conn, world.a["id"], "ingest")
    assert job["state"] == outcome and job["last_error"] == reason
    assert CANARY not in job["last_error"]
    assert CANARY not in caplog.text
    assert all(record.exc_info is None for record in caplog.records if record.name == "finledger.ingest")
    assert jobs(conn, world.a["id"], "extract") == []
    if outcome == "queued":
        assert worker.run_once(conn, store, settings) is None  # backoff still applies


def test_scanner_failure_leaves_document_unscanned(world, client, conn, store, settings, monkeypatch, caplog):
    result = send(client, world.a["public_token"], ("invoice.pdf", invoice_pdf(), "application/pdf"))
    document_id = result.json()["received"][0]["document_id"]
    monkeypatch.setattr(virus, "scan", fail_with_payload(virus.ScannerError))
    assert worker.run_once(conn, store, settings) == "queued"
    assert worker.run_once(conn, store, settings) is None
    with tenant(conn, world.a["id"]):
        document = conn.execute("select status, virus_ok, prepared_at from documents where id = %s", (document_id,)).fetchone()
    assert document == {"status": "received", "virus_ok": None, "prepared_at": None}
    [job] = jobs(conn, world.a["id"], "ingest")
    assert job["last_error"] == "virus scanner unavailable"
    assert CANARY not in caplog.text and jobs(conn, world.a["id"], "extract") == []


@pytest.mark.parametrize("error_type", [RuntimeError, queue.PermanentError])
def test_ingest_settlement_failure_does_not_chain_payload_and_lease_recovers(
    world, client, conn, store, settings, owner, monkeypatch, caplog, error_type,
):
    send(client, world.a["public_token"], ("invoice.pdf", invoice_pdf(), "application/pdf"))

    def fail_settlement(*args, **kwargs):
        conn.execute("select %s::integer", (CANARY,))  # real server error containing the synthetic payload

    with monkeypatch.context() as patch:
        patch.setattr(store, "get", fail_with_payload(error_type))
        patch.setattr(worker, "finish", fail_settlement)
        with pytest.raises(Exception) as caught:
            worker.run_once(conn, store, settings)
    assert CANARY not in "".join(traceback.format_exception(caught.value))
    assert isinstance(caught.value, RuntimeError) and str(caught.value) == "ingest worker operation failed"
    assert CANARY not in caplog.text
    [job] = jobs(conn, world.a["id"], "ingest")
    assert job["state"] == "running" and job["last_error"] is None
    assert worker.run_once(conn, store, settings) is None
    owner.execute("update jobs set locked_until = now() - interval '1 second' where id = %s", (job["id"],))
    assert worker.run_once(conn, store, settings) == "prepared"
    [job] = jobs(conn, world.a["id"], "ingest")
    assert job["state"] == "done" and job["attempts"] == 2


def test_unreadable_document_diagnostic_does_not_echo_failure_payload(
    world, client, conn, store, settings, monkeypatch, caplog,
):
    result = send(client, world.a["public_token"], ("invoice.pdf", invoice_pdf(), "application/pdf"))
    document_id = result.json()["received"][0]["document_id"]
    monkeypatch.setattr(prep, "prepare", fail_with_payload(queue.PermanentError))
    assert worker.run_once(conn, store, settings) == "exception"
    with tenant(conn, world.a["id"]):
        document = conn.execute("select status, status_reason from documents where id = %s", (document_id,)).fetchone()
    assert document["status"] == "exception"
    assert document["status_reason"] == "unreadable PDF"
    assert CANARY not in document["status_reason"] and CANARY not in caplog.text
    [job] = jobs(conn, world.a["id"], "ingest")
    assert job["state"] == "done" and job["last_error"] is None
    assert jobs(conn, world.a["id"], "extract") == []


def test_outbox_diagnostics_do_not_echo_failure_payload(world, conn, caplog):
    row_id = queue_reply(conn, world.a["id"])
    with caplog.at_level(logging.ERROR, logger="finledger.outbox"):
        assert outbox_worker.run_once(conn, world.a["id"], fail_with_payload(RuntimeError)) == "failed"
    assert state(conn, world.a["id"], row_id) == "failed"
    assert outbox_worker.run_once(conn, world.a["id"], lambda *args: pytest.fail("failed reply repeated")) is None
    assert CANARY not in caplog.text
    assert any(record.name == "finledger.outbox" for record in caplog.records)
    assert all(record.exc_info is None for record in caplog.records if record.name == "finledger.outbox")


def test_outbox_settlement_failure_does_not_chain_payload_and_rolls_back(world, conn, caplog):
    row_id = queue_reply(conn, world.a["id"])

    class FailedWriteConnection:
        def __getattr__(self, name):
            return getattr(conn, name)

        def execute(self, query, *args, **kwargs):
            if query.startswith("update outbox set state = 'failed'"):
                return conn.execute("select %s::integer", (CANARY,))
            return conn.execute(query, *args, **kwargs)

    with pytest.raises(Exception) as caught:
        outbox_worker.run_once(FailedWriteConnection(), world.a["id"], fail_with_payload(RuntimeError))
    assert CANARY not in "".join(traceback.format_exception(caught.value)) and CANARY not in caplog.text
    assert isinstance(caught.value, RuntimeError) and str(caught.value) == "outbox worker operation failed"
    assert state(conn, world.a["id"], row_id) == "queued"  # the failed settlement transaction was rolled back
    assert outbox_worker.run_once(conn, world.a["id"], lambda *args: None) == "sent"
