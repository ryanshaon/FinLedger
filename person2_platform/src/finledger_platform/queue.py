"""The shared queue: ingest | extract | score | map | post, one Postgres table, SKIP LOCKED leases.

Any seat's worker loop:

    job = claim(conn, "extract")                 # outside any tenant; returns None when idle
    try:
        with tenant(conn, job["client_id"]):     # all real work is RLS-scoped to the job's client
            ...
        finish(conn, job["id"], job["claim_token"], ok=True)
    except PermanentError as e:
        finish(conn, job["id"], job["claim_token"], ok=False, error=str(e), permanent=True)
    except Exception as e:
        finish(conn, job["id"], job["claim_token"], ok=False, error=repr(e))
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

QUEUES = ("ingest", "extract", "score", "map", "post")


class PermanentError(Exception):
    """Retrying will not help (corrupt file, schema violation). Job goes straight to the poison state."""


def enqueue(conn: psycopg.Connection, queue: str, client_id: UUID | str, document_id: UUID | str,
            payload: dict[str, Any] | None = None, idem_key: str = "", max_attempts: int = 5) -> int | None:
    """Idempotent: the same (queue, document_id, idem_key) is only ever enqueued once. Call inside tenant()."""
    if queue not in QUEUES:
        raise ValueError(f"unknown queue {queue!r}")
    row = conn.execute(
        """insert into jobs (client_id, document_id, queue, idem_key, payload, max_attempts)
           values (%s, %s, %s, %s, %s, %s)
           on conflict (queue, document_id, idem_key) do nothing returning id""",
        (client_id, document_id, queue, idem_key, Jsonb(payload or {}), max_attempts),
    ).fetchone()
    return row["id"] if row else None


def claim(conn: psycopg.Connection, queue: str, lease_seconds: int = 300) -> dict | None:
    return conn.execute("select * from claim_job(%s, %s)", (queue, lease_seconds)).fetchone()


def finish(conn: psycopg.Connection, job_id: int, claim_token: UUID | str, ok: bool, error: str | None = None,
           permanent: bool = False, retry_seconds: int = 30) -> str | None:
    """Returns the job's new state ('done' | 'queued' | 'dead'), or None if the lease was lost."""
    row = conn.execute(
        "select finish_job(%s, %s, %s, %s, %s, %s) as state",
        (job_id, claim_token, ok, (error or "")[:2000] or None, permanent, retry_seconds)
    ).fetchone()
    return row["state"]
