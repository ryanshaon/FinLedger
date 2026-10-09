"""Inbound ASGI requests must authenticate before borrowing a DB connection."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from finledger_platform import api
from finledger_platform.intake import Receipt

SECRET = b"synthetic-inbound-pool-secret"
CLIENT_ID = "11111111-1111-1111-1111-111111111111"


def message(recipient="invoices.acme@inbound.example.test", *, trusted=True, attached=False):
    auth = b"Authentication-Results: mx.example.test; spf=pass\r\n" if trusted else b""
    body = (b'MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary="synthetic"\r\n'
            b'\r\n--synthetic\r\nContent-Type: text/plain\r\n\r\nbody\r\n'
            b'--synthetic\r\nContent-Type: application/pdf\r\n'
            b'Content-Disposition: attachment; filename="synthetic.pdf"\r\n'
            b'\r\nsynthetic bytes\r\n--synthetic--\r\n') if attached else b"\r\nbody"
    return (b"From: supplier@example.test\r\nTo: " + recipient.encode() + b"\r\n"
            b"Subject: synthetic\r\nMessage-ID: <synthetic@example.test>\r\n" + auth + body)


def signature(raw):
    return ("sha256=" + hmac.new(SECRET, raw, hashlib.sha256).hexdigest()).encode()


class CountedPool:
    def __init__(self):
        self.active = self.acquired = self.returned = 0
        self.events = []
        self.client_id = CLIENT_ID
        self.fail_at = None
        self.outbox = []

    def record(self, name):
        self.events.append((name, threading.get_ident()))

    @contextmanager
    def connection(self):
        self.record("acquire")
        self.active += 1
        self.acquired += 1
        try:
            yield FakeConn(self)
        finally:
            self.record("release")
            self.active -= 1
            self.returned += 1


class FakeConn:
    autocommit = True

    def __init__(self, pool):
        self.pool = pool
        self.in_transaction = False

    @contextmanager
    def transaction(self):
        self.pool.record("transaction")
        self.in_transaction = True
        try:
            yield
        except Exception:
            self.pool.record("rollback")
            raise
        else:
            self.pool.record("commit")
        finally:
            self.in_transaction = False

    def execute(self, sql, params):
        if sql == "select client_by_inbound_slug(%s) as id":
            assert self.autocommit and not self.in_transaction
            assert params == ("acme",)
            self.pool.record("lookup")
            return SimpleNamespace(fetchone=lambda: {"id": self.pool.client_id})
        if sql == "select set_config('app.client_id', %s, true)":
            assert self.in_transaction and params == (CLIENT_ID,)
            self.pool.record("tenant")
            return None
        if "insert into outbox" in sql:
            assert self.in_transaction
            self.pool.record("outbox")
            if self.pool.fail_at == "outbox":
                raise RuntimeError("synthetic outbox failure")
            self.pool.outbox.append(params)
            return None
        raise AssertionError(f"unexpected SQL: {sql}")


@pytest.fixture
def boundary(monkeypatch):
    pool = CountedPool()
    settings = SimpleNamespace(
        inbound_webhook_secret=SECRET, inbound_authserv_id="mx.example.test",
        inbound_domain="inbound.example.test",
        inbound_address=lambda slug: f"invoices.{slug}@inbound.example.test",
    )
    store = object()
    receipt = Receipt(received=[{"document_id": "synthetic-document"}])
    intake_calls = []
    original_parse = api.email_in.parse

    def parse(raw, authserv_id):
        parsed = original_parse(raw, authserv_id)
        pool.record("parsed")
        return parsed

    def accept(conn, actual_store, cid, channel, attachments, meta, **kwargs):
        assert actual_store is store and cid == CLIENT_ID and channel == "email"
        assert conn.in_transaction and pool.active == 1
        pool.record("intake")
        if pool.fail_at == "intake":
            raise RuntimeError("synthetic intake failure")
        intake_calls.append((attachments, meta, kwargs))
        return receipt

    monkeypatch.setattr(api.email_in, "parse", parse)
    monkeypatch.setattr(api.intake, "accept", accept)
    return SimpleNamespace(app=api.create_app(settings, pool, store), pool=pool,
                           receipt=receipt, intake_calls=intake_calls)


async def post(app, chunks, signed=None, *, gate=None, waiting=None):
    """Call the real ASGI app and dependency machinery, with no HTTP client."""
    headers = [(b"content-type", b"message/rfc822")]
    if signed is not None:
        headers.append((b"x-finledger-signature", signed))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/inbound/email",
        "raw_path": b"/inbound/email", "query_string": b"", "headers": headers,
        "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
    }
    reads = 0
    messages = []

    async def receive():
        nonlocal reads
        if gate is not None and reads == 1:
            waiting.set()
            await gate.wait()
        assert reads < len(chunks), "read beyond supplied body"
        chunk = chunks[reads]
        reads += 1
        return {"type": "http.request", "body": chunk, "more_body": reads < len(chunks)}

    async def send(value):
        messages.append(value)

    await asyncio.wait_for(app(scope, receive, send), timeout=3)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(body), reads


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("kind", ["missing", "malformed", "wrong", "oversize"])
def test_rejected_request_never_borrows_connection(boundary, monkeypatch, kind):
    monkeypatch.setattr(api, "MAX_EMAIL_BYTES", 8)
    signed = {"missing": None, "malformed": b"bad",
              "wrong": b"sha256=" + b"0" * 64, "oversize": None}[kind]
    chunks = [b"1234", b"56789", b"unread"] if kind == "oversize" else [b"body"]
    status, _, reads = run(post(boundary.app, chunks, signed))
    assert status == (413 if kind == "oversize" else 401)
    assert reads == (2 if kind == "oversize" else 1)
    assert boundary.pool.acquired == boundary.pool.returned == boundary.pool.active == 0
    assert boundary.pool.events == []


@pytest.mark.parametrize("signed", [None, b"bad"], ids=["missing", "malformed"])
def test_paused_unauthenticated_stream_holds_no_connection(boundary, signed):
    async def scenario():
        gate, waiting = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(post(boundary.app, [b"one", b"two"], signed,
                                        gate=gate, waiting=waiting))
        try:
            await asyncio.wait_for(waiting.wait(), timeout=2)
            assert not task.done()
            assert boundary.pool.acquired == boundary.pool.active == 0
        finally:
            gate.set()
            result = await task
        assert result[0] == 401
        assert boundary.pool.acquired == boundary.pool.returned == 0

    run(scenario())


def test_authenticated_message_without_slug_never_borrows_connection(boundary):
    raw = message("outside@example.test")
    status, body, _ = run(post(boundary.app, [raw], signature(raw)))
    assert status == 200 and body == {"status": "dropped", "reason": "unknown recipient"}
    assert boundary.pool.acquired == boundary.pool.returned == 0
    assert [name for name, _ in boundary.pool.events] == ["parsed"]


def assert_worker_lifecycle(pool):
    events = pool.events
    names = [name for name, _ in events]
    assert names.index("parsed") < names.index("acquire")
    assert names[-1] == "release"
    worker_ids = {thread_id for name, thread_id in events if name != "parsed"}
    assert len(worker_ids) == 1 and threading.get_ident() not in worker_ids
    assert pool.acquired == pool.returned == 1 and pool.active == 0


def test_authenticated_unknown_slug_returns_connection_without_transaction(boundary):
    boundary.pool.client_id = None
    raw = message()
    status, body, _ = run(post(boundary.app, [raw], signature(raw)))
    assert status == 200 and body == {"status": "dropped", "reason": "unknown recipient"}
    assert_worker_lifecycle(boundary.pool)
    assert [name for name, _ in boundary.pool.events] == ["parsed", "acquire", "lookup", "release"]
    assert not boundary.intake_calls and not boundary.pool.outbox


@pytest.mark.parametrize("kind", ["accepted", "replayed", "phishing", "rejected"])
def test_authenticated_intake_and_outbox_share_worker_transaction(boundary, kind):
    if kind == "replayed":
        boundary.receipt.received[0]["replayed"] = True
    if kind == "rejected":
        boundary.receipt.received.clear()
    raw = message(trusted=kind != "phishing", attached=kind != "rejected")
    status, body, _ = run(post(boundary.app, [raw[:5], raw[5:]], signature(raw)))
    assert status == 200 and body["status"] == ("rejected" if kind == "rejected" else "accepted")
    assert_worker_lifecycle(boundary.pool)
    names = [name for name, _ in boundary.pool.events]
    assert names.index("lookup") < names.index("transaction") < names.index("tenant") < names.index("intake")
    assert names[-2:] == ["commit", "release"]
    assert len(boundary.intake_calls) == 1
    attachments, meta, kwargs = boundary.intake_calls[0]
    assert attachments == ([] if kind == "rejected" else [("synthetic.pdf", b"synthetic bytes")])
    assert meta["from"] == "supplier@example.test"
    assert meta["recipient"] == "invoices.acme@inbound.example.test"
    assert kwargs["intake_key"].startswith("email:")
    assert kwargs["phish_flag"] == (kind == "phishing")
    if kind in ("accepted", "rejected"):
        assert names.index("intake") < names.index("outbox") < names.index("commit")
        assert len(boundary.pool.outbox) == 1
        cid, doc_id, to_addr, template, payload = boundary.pool.outbox[0]
        assert cid == CLIENT_ID and to_addr == "supplier@example.test"
        assert doc_id == (None if kind == "rejected" else "synthetic-document")
        assert template == ("resubmit" if kind == "rejected" else "received")
        assert payload.obj["subject"] == "synthetic"
        assert payload.obj["rejected"] == ([{"filename": "", "reason": "no invoice attached"}]
                                           if kind == "rejected" else [])
    else:
        assert "outbox" not in names and not boundary.pool.outbox


@pytest.mark.parametrize("fail_at", ["intake", "outbox"])
def test_processing_failure_rolls_back_and_returns_connection(boundary, fail_at):
    boundary.pool.fail_at = fail_at
    raw = message()
    with pytest.raises(RuntimeError, match=f"synthetic {fail_at} failure"):
        run(post(boundary.app, [raw], signature(raw)))
    assert_worker_lifecycle(boundary.pool)
    assert [name for name, _ in boundary.pool.events][-2:] == ["rollback", "release"]
