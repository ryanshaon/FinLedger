"""One transaction: usage insert, then llm_reconcile_tokens, for migration 012.

The model call stays outside this transaction. Reconciling without the insert drops the hold
out of the cap, because only `held` rows and usage tokens count. An unpriced call is written with
cost_usd NULL (migration 014), never as 0.
"""

from __future__ import annotations

import psycopg


def write_usage_and_reconcile(
    conn: psycopg.Connection,
    *,
    client_id: str,
    document_id: str,
    reservation_id: str,
    purpose: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    cost_usd: float | None,
) -> None:
    from finledger_platform.db import tenant

    with tenant(conn, client_id):
        conn.execute(
            """insert into llm_usage_rows
               (client_id, document_id, reservation_id, purpose, model, tokens_in, tokens_out, latency_ms, cost_usd)
               values (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s)""",
            (
                client_id,
                document_id,
                reservation_id,
                purpose,
                model,
                tokens_in,
                tokens_out,
                latency_ms,
                cost_usd,
            ),
        )
        conn.execute("select llm_reconcile_tokens(%s::uuid)", (reservation_id,))
