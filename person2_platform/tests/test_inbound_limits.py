"""Real ASGI boundary checks using tiny synthetic bodies and no database/network."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from finledger_platform import api


class FakeConn:
    def execute(self, *args, **kwargs):
        raise AssertionError("inbound rejection must not execute SQL")


class FakePool:
    @contextmanager
    def connection(self):
        yield FakeConn()


class UnusedStore:
    def __getattr__(self, name):
        raise AssertionError("inbound boundary must not use storage")


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(api, "MAX_EMAIL_BYTES", 8)
    settings = SimpleNamespace(
        inbound_webhook_secret=b"synthetic-test-secret",
        inbound_authserv_id="mx.example.test",
        inbound_domain="inbound.example.test",
    )
    return api.create_app(settings, FakePool(), UnusedStore())


def post_chunks(app, chunks, signature=None, content_length=None):
    """Deliver raw header bytes and count only chunks requested by the real app."""
    headers = [(b"content-type", b"message/rfc822")]
    if signature is not None:
        headers.append((b"x-finledger-signature", signature))
    if content_length is not None:
        headers.append((b"content-length", content_length))
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
        if reads >= len(chunks):
            raise AssertionError("read beyond request body")
        chunk = chunks[reads]
        reads += 1
        return {"type": "http.request", "body": chunk, "more_body": reads < len(chunks)}

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(body), reads


@pytest.fixture
def forbid_parsing(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("unauthenticated body must not be parsed")

    monkeypatch.setattr(api.email_in, "parse", fail)


# Reading the rest of an oversized request (including a lying length header)
# must fail these cases even if the final status remains 413.
@pytest.mark.parametrize("content_length", [None, b"1", b"99"], ids=["absent", "understated", "overstated"])
@pytest.mark.parametrize("chunks, expected_reads", [
    ([b"1234", b"56789", b"unread"], 2),
    ([b"123456789", b"unread"], 1),
    ([b"12345678", b"9", b"unread"], 2),
], ids=["crossing-chunk", "first-chunk", "after-exact-limit"])
def test_oversize_stops_at_first_crossing_chunk(app, forbid_parsing, content_length, chunks, expected_reads):
    status, _, reads = post_chunks(app, chunks, content_length=content_length)
    assert status == 413
    assert reads == expected_reads


# A >= size check would wrongly turn this authentication rejection into 413.
@pytest.mark.parametrize("signature", [None, b"sha256=" + b"0" * 64], ids=["missing", "wrong-digest"])
def test_exact_limit_reaches_signature_rejection(app, forbid_parsing, signature):
    status, _, reads = post_chunks(app, [b"123", b"45678"], signature)
    assert status == 401
    assert reads == 2


# Removing the format gate must fail before comparison, not merely return 401
# by comparing a malformed ASCII string that happens not to match.
@pytest.mark.parametrize("signature", [
    b"", b"bad", b"SHA256=" + b"0" * 64, b"sha256=" + b"A" * 64,
    b"sha256=" + b"0" * 63, b"sha256=" + b"0" * 65,
    b"sha256=" + b"g" * 64, b"sha256=" + b"0" * 64 + b"\n",
    b"sha256=" + b"0" * 63 + b"\xff",
], ids=["empty", "no-prefix", "upper-prefix", "upper-hex", "short", "long", "non-hex", "newline", "non-ascii"])
def test_malformed_signature_rejected_before_comparison(app, forbid_parsing, monkeypatch, signature):
    def fail(*args, **kwargs):
        raise AssertionError("malformed signature must not reach comparison")

    monkeypatch.setattr(api.hmac, "compare_digest", fail)
    status, _, _ = post_chunks(app, [b"body"], signature)
    assert status == 401


def test_non_ascii_signature_returns_401(app, forbid_parsing):
    status, _, _ = post_chunks(app, [b"body"], b"sha256=" + b"0" * 63 + b"\xff")
    assert status == 401


# Signing the original synthetic MIME bytes catches truncation, re-encoding,
# dropped chunks, and failure to accept the existing lowercase HMAC format.
@pytest.mark.parametrize("split", [False, True], ids=["single-chunk", "multiple-chunks"])
def test_valid_hmac_at_exact_limit_uses_real_parser(app, monkeypatch, split):
    raw = b"To: outside@example.test\r\nSubject: synthetic\r\n\r\nbody\xff"
    monkeypatch.setattr(api, "MAX_EMAIL_BYTES", len(raw))
    signature = ("sha256=" + hmac.new(b"synthetic-test-secret", raw, hashlib.sha256).hexdigest()).encode("ascii")
    chunks = [raw[:5], b"", raw[5:17], raw[17:]] if split else [raw]
    status, body, reads = post_chunks(app, chunks, signature)
    assert status == 200
    assert body == {"status": "dropped", "reason": "unknown recipient"}
    assert reads == len(chunks)
