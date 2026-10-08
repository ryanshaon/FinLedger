"""Outbox delivery through the app role and real tenant RLS."""
from __future__ import annotations

import json
import pytest
from psycopg.types.json import Jsonb

from finledger_platform.db import tenant
from finledger_platform.cli import main
from finledger_platform.outbox_worker import ResendSender, _NoRedirect, run_once


def queue_reply(conn, client_id, template="received", payload=None):
    with tenant(conn, client_id):
        return conn.execute(
            "insert into outbox (client_id, to_addr, template, payload) "
            "values (%s, %s, %s, %s) returning id",
            (client_id, "vendor@example.test", template, Jsonb(payload or {})),
        ).fetchone()["id"]


def state(conn, client_id, row_id):
    with tenant(conn, client_id):
        return conn.execute("select state from outbox where id = %s", (row_id,)).fetchone()["state"]


def test_sends_queued_reply_once_and_marks_sent(world, conn):
    row_id = queue_reply(conn, world.a["id"], payload={"subject": "Invoice", "received": [{"filename": "bill.pdf"}]})
    calls = []

    def sender(to, subject, text, idempotency_key):
        calls.append((to, subject, text, idempotency_key))

    assert run_once(conn, world.a["id"], sender) == "sent"
    assert run_once(conn, world.a["id"], sender) is None
    assert len(calls) == 1
    assert calls[0][0] == "vendor@example.test"
    assert "bill.pdf" in calls[0][2]
    assert calls[0][3] == f"finledger-outbox-{world.a['id']}-{row_id}"
    assert state(conn, world.a["id"], row_id) == "sent"


def test_locked_row_is_not_claimed_by_second_worker(world, conn, pool):
    row_id = queue_reply(conn, world.a["id"])
    other_calls = []

    def sender(*args):
        with pool.connection() as other:
            assert run_once(other, world.a["id"], lambda *a: other_calls.append(a)) is None

    assert run_once(conn, world.a["id"], sender) == "sent"
    assert other_calls == []
    assert state(conn, world.a["id"], row_id) == "sent"


def test_sender_failure_marks_failed_without_repeating(world, conn):
    row_id = queue_reply(conn, world.a["id"])

    def sender(*args):
        raise RuntimeError("provider unavailable")

    assert run_once(conn, world.a["id"], sender) == "failed"
    assert state(conn, world.a["id"], row_id) == "failed"
    assert run_once(conn, world.a["id"], sender) is None


def test_claim_is_scoped_to_requested_client(world, conn):
    row_id = queue_reply(conn, world.b["id"])
    assert run_once(conn, world.a["id"], lambda *args: pytest.fail("wrong tenant")) is None
    assert state(conn, world.b["id"], row_id) == "queued"


def test_resubmit_renders_rejection_without_echoing_untrusted_subject(world, conn):
    queue_reply(conn, world.a["id"], "resubmit", {"subject": "<script>x</script>",
                "rejected": [{"filename": "bad.exe", "reason": "unsupported file type"}]})
    calls = []
    assert run_once(conn, world.a["id"], lambda *args: calls.append(args)) == "sent"
    assert "bad.exe" in calls[0][2]
    assert "unsupported file type" in calls[0][2]
    assert "<script>" not in calls[0][1]


def test_resend_sender_uses_tls_auth_and_stable_idempotency_key():
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def read(self):
                return b'{"id":"email-123"}'

        return Response()

    sender = ResendSender("re_test", "billing@example.test", opener=opener)
    sender("vendor@example.test", "Received", "Thank you", "finledger-outbox-12")
    request, timeout = requests[0]
    assert request.full_url == "https://api.resend.com/emails"
    assert request.get_header("Authorization") == "Bearer re_test"
    assert request.get_header("Idempotency-key") == "finledger-outbox-12"
    assert json.loads(request.data) == {"from": "billing@example.test", "to": ["vendor@example.test"],
                                        "subject": "Received", "text": "Thank you"}
    assert timeout > 0


def test_resend_sender_requires_credentials():
    with pytest.raises(ValueError):
        ResendSender("", "billing@example.test")
    with pytest.raises(ValueError):
        ResendSender("re_test", "")


def test_resend_adapter_refuses_cross_origin_redirect():
    assert _NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://other.example/email") is None


def test_cli_requires_explicit_tenant_and_sender_credentials(monkeypatch):
    with pytest.raises(SystemExit):
        main(["outbox-worker"])
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_FROM_EMAIL", raising=False)
    with pytest.raises(ValueError, match="RESEND_API_KEY"):
        main(["outbox-worker", "--client-id", "00000000-0000-0000-0000-000000000001"])
