"""Week 3: queue reliability, retries, virus scanner outages, poison queue."""
from __future__ import annotations

import socket
import struct
import threading

import pytest
from conftest import auth, invoice_pdf
from test_link import jobs, send

from finledger_platform import queue, virus
from finledger_platform.db import tenant
from finledger_platform.worker import run_once


def make_due(owner):
    owner.execute("update jobs set run_after = now() where state = 'queued'")


def test_scanner_outage_retries_then_poisons(world, client, conn, store, settings, owner):
    object.__setattr__(settings, "virus_scanner", "clamd")
    object.__setattr__(settings, "clamd_port", 1)  # nothing listens here
    doc = send(client, world.a["public_token"], ("a.pdf", invoice_pdf(), "application/pdf")).json()["received"][0]
    owner.execute("update jobs set max_attempts = 3")

    states = []
    for _ in range(3):
        states.append(run_once(conn, store, settings))
        make_due(owner)
    assert states == ["queued", "queued", "dead"]
    [job] = jobs(conn, world.a["id"], "ingest")
    assert job["state"] == "dead" and "clamd unreachable" in job["last_error"] and job["attempts"] == 3
    assert jobs(conn, world.a["id"], "extract") == []
    with tenant(conn, world.a["id"]):
        d = conn.execute("select virus_ok, status from documents where id = %s", (doc["document_id"],)).fetchone()
    assert d["virus_ok"] is None and d["status"] == "received"  # unscanned, never waved through

    # Ops view + requeue once the scanner is back.
    dead = client.get(f"/clients/{world.a['id']}/jobs", headers=auth(world.token1)).json()
    assert [j["id"] for j in dead] == [job["id"]]
    assert client.post(f"/clients/{world.a['id']}/jobs/{job['id']}/requeue", headers=auth(world.token1)).json()["ok"]
    object.__setattr__(settings, "virus_scanner", "eicar")
    assert run_once(conn, store, settings) == "prepared"
    assert len(jobs(conn, world.a["id"], "extract")) == 1
    # other firm cannot requeue it
    assert client.post(f"/clients/{world.a['id']}/jobs/{job['id']}/requeue",
                       headers=auth(world.token2)).status_code == 404


def test_backoff_delays_retry(world, client, conn, store, settings):
    object.__setattr__(settings, "virus_scanner", "clamd")
    object.__setattr__(settings, "clamd_port", 1)
    send(client, world.a["public_token"], ("a.pdf", invoice_pdf(), "application/pdf"))
    assert run_once(conn, store, settings) == "queued"
    assert run_once(conn, store, settings) is None  # not due yet


def test_crashed_worker_lease_is_reclaimed(world, client, conn, owner):
    send(client, world.a["public_token"], ("a.pdf", invoice_pdf(), "application/pdf"))
    job = queue.claim(conn, "ingest", lease_seconds=60)
    assert queue.claim(conn, "ingest") is None  # leased
    owner.execute("update jobs set locked_until = now() - interval '1 second'")
    again = queue.claim(conn, "ingest")
    assert again["id"] == job["id"] and again["attempts"] == 2
    assert queue.finish(conn, job["id"], again["claim_token"], ok=True) == "done"


def test_lease_expiry_on_last_attempt_goes_dead(world, client, conn, owner):
    send(client, world.a["public_token"], ("a.pdf", invoice_pdf(), "application/pdf"))
    owner.execute("update jobs set max_attempts = 1")
    queue.claim(conn, "ingest")
    owner.execute("update jobs set locked_until = now() - interval '1 second'")
    assert queue.claim(conn, "ingest") is None
    [job] = jobs(conn, world.a["id"], "ingest")
    assert job["state"] == "dead"


def test_finish_after_lost_lease_is_noop(world, client, conn):
    send(client, world.a["public_token"], ("a.pdf", invoice_pdf(), "application/pdf"))
    job = queue.claim(conn, "ingest")
    assert queue.finish(conn, job["id"], job["claim_token"], ok=True) == "done"
    assert queue.finish(conn, job["id"], job["claim_token"], ok=False, error="late") is None


def test_wrong_claim_token_cannot_finish_job(world, client, conn):
    send(client, world.a["public_token"], ("a.pdf", invoice_pdf(), "application/pdf"))
    job = queue.claim(conn, "ingest")
    assert queue.finish(conn, job["id"], "00000000-0000-0000-0000-000000000000", ok=True) is None
    assert queue.finish(conn, job["id"], job["claim_token"], ok=True) == "done"


def test_enqueue_is_idempotent_and_validated(world, client, conn):
    doc = send(client, world.a["public_token"], ("a.pdf", invoice_pdf(), "application/pdf")).json()["received"][0]
    with tenant(conn, world.a["id"]):
        first = queue.enqueue(conn, "score", world.a["id"], doc["document_id"])
        second = queue.enqueue(conn, "score", world.a["id"], doc["document_id"])
        rev2 = queue.enqueue(conn, "score", world.a["id"], doc["document_id"], idem_key="rev2")
        with pytest.raises(ValueError):
            queue.enqueue(conn, "payments", world.a["id"], doc["document_id"])
    assert first and second is None and rev2


def test_other_seats_claim_across_clients(world, client, conn, store, settings):
    """claim_job works without a tenant (definer), then work is scoped by tenant(job.client_id)."""
    send(client, world.a["public_token"], ("a.pdf", invoice_pdf(), "application/pdf"))
    send(client, world.b["public_token"], ("b.pdf", invoice_pdf(), "application/pdf"))
    while run_once(conn, store, settings):
        pass
    got = {queue.claim(conn, "extract")["client_id"], queue.claim(conn, "extract")["client_id"]}
    assert got == {world.a["id"], world.b["id"]}


def test_clamd_protocol():
    """Talk INSTREAM to a fake clamd and parse OK / FOUND."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(2)
    port = srv.getsockname()[1]

    def serve():
        for _ in range(2):
            c, _ = srv.accept()
            with c:
                buf = b""
                while not buf.endswith(struct.pack(">I", 0)):
                    buf += c.recv(65536)
                c.sendall(b"stream: Eicar-Signature FOUND\0" if virus.EICAR in buf else b"stream: OK\0")

    threading.Thread(target=serve, daemon=True).start()
    assert virus.clamd_scan(b"clean bytes" * 10000, "127.0.0.1", port) is None
    assert virus.clamd_scan(virus.EICAR, "127.0.0.1", port) == "Eicar-Signature"
    srv.close()
    with pytest.raises(virus.ScannerError):
        virus.clamd_scan(b"x", "127.0.0.1", 1, timeout=1)
