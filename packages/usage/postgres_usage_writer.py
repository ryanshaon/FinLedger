from __future__ import annotations

import psycopg

from usage.postgres_settlement import write_usage_and_reconcile
from usage.usage_writer import UsageRow, UsageWriter


class PostgresUsageWriter(UsageWriter):
    def __init__(self, conn: psycopg.Connection):
        super().__init__()
        self._conn = conn

    def log_usage(
        self,
        client_id: str,
        doc_id: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        cost_usd: float | None,
        purpose: str,
        reservation_id: str | None = None,
    ) -> UsageRow:
        row = super().log_usage(
            client_id, doc_id, model, tokens_in, tokens_out, latency_ms, cost_usd, purpose,
            reservation_id=reservation_id,
        )
        if not reservation_id:
            raise ValueError("reservation_id is required; a durable usage row must use the hold id")
        write_usage_and_reconcile(
            self._conn,
            client_id=client_id,
            document_id=doc_id,
            reservation_id=reservation_id,
            purpose=purpose,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
        )
        return row
