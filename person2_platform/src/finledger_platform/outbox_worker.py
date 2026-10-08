"""Deliver one tenant's queued outbox replies with the row locked until settlement.

The existing outbox has no lease or claim token. A transaction-scoped row lock is
therefore the claim; callers must use an app-role connection and an explicit client.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

import psycopg

from .db import tenant

log = logging.getLogger("finledger.outbox")
Sender = Callable[[str, str, str, str], None]
_EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")


def _line(value: object) -> str:
    """Keep untrusted filenames and rejection reasons on one plain-text line."""
    return " ".join(str(value).split())[:200]


def _message(row: dict) -> tuple[str, str]:
    payload = row["payload"]
    if not isinstance(payload, dict):
        raise ValueError("outbox payload must be an object")
    if row["template"] == "received":
        names = [_line(item.get("filename", "")) for item in payload.get("received", []) if isinstance(item, dict)]
        names = [name for name in names if name]
        body = "We received your invoice. Thank you."
        if names:
            body += "\n\nReceived: " + ", ".join(names)
        return "Invoice received", body
    if row["template"] == "resubmit":
        reasons = [f"{_line(item.get('filename', 'Attachment'))}: {_line(item.get('reason', 'could not be processed'))}"
                   for item in payload.get("rejected", []) if isinstance(item, dict)]
        body = "We could not process your invoice. Please resend it."
        if reasons:
            body += "\n\n" + "\n".join(reasons)
        return "Please resend your invoice", body
    raise ValueError("unknown outbox template")


def run_once(conn: psycopg.Connection, client_id: UUID | str, sender: Sender) -> str | None:
    """Send at most one queued reply; return sent, failed, or None when idle.

    FOR UPDATE SKIP LOCKED prevents concurrent sends. A crash after provider
    acceptance but before commit can replay: use a stable provider idempotency key.
    """
    with tenant(conn, client_id):
        row = conn.execute(
            "select id, to_addr, template, payload from outbox "
            "where state = 'queued' order by id limit 1 for update skip locked"
        ).fetchone()
        if row is None:
            return None
        try:
            subject, body = _message(row)
            sender(row["to_addr"], subject, body, f"finledger-outbox-{client_id}-{row['id']}")
        except Exception:
            log.exception("outbox delivery failed for row %s", row["id"])
            conn.execute("update outbox set state = 'failed' where id = %s and state = 'queued'", (row["id"],))
            return "failed"
        conn.execute("update outbox set state = 'sent' where id = %s and state = 'queued'", (row["id"],))
        return "sent"


def run_forever(pool, client_id: UUID | str, sender: Sender, idle_sleep: float = 1.0) -> None:
    """Drain one explicitly selected tenant using the app-role pool."""
    while True:
        with pool.connection() as conn:
            result = run_once(conn, client_id, sender)
        if result is None:
            time.sleep(idle_sleep)


class ResendSender:
    """Small HTTPS adapter for Resend POST /emails; credentials never enter logs."""

    def __init__(self, api_key: str, from_email: str, *, opener=None, timeout: float = 10.0):
        if not api_key or not re.fullmatch(r"re_[A-Za-z0-9_]+", api_key):
            raise ValueError("RESEND_API_KEY is required")
        if not from_email or not _EMAIL.fullmatch(from_email):
            raise ValueError("RESEND_FROM_EMAIL must be a bare email address")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._api_key = api_key
        self._from_email = from_email
        self._opener = opener or build_opener(_NoRedirect()).open
        self._timeout = timeout

    def __call__(self, to: str, subject: str, text: str, idempotency_key: str) -> None:
        if not _EMAIL.fullmatch(to) or not idempotency_key.startswith("finledger-outbox-"):
            raise ValueError("invalid recipient or outbox key")
        data = json.dumps({"from": self._from_email, "to": [to], "subject": subject, "text": text}).encode("utf-8")
        request = Request(
            "https://api.resend.com/emails", data=data, method="POST",
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json",
                     "Idempotency-Key": idempotency_key},
        )
        try:
            with self._opener(request, timeout=self._timeout) as response:
                if response.status < 200 or response.status >= 300:
                    raise RuntimeError("Resend rejected email")
                result = json.loads(response.read())
                if not isinstance(result, dict) or not isinstance(result.get("id"), str) or not result["id"]:
                    raise RuntimeError("Resend did not confirm email id")
        except Exception:
            raise RuntimeError("Resend delivery not confirmed") from None


class _NoRedirect(HTTPRedirectHandler):
    """Never forward the bearer token to a redirect destination."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None
