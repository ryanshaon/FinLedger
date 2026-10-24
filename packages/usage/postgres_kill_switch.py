from __future__ import annotations

import uuid

import psycopg

from usage.kill_switch import KillSwitch, Reservation, TokenCapExceededException
from usage.postgres_settlement import write_usage_and_reconcile


class PostgresKillSwitch(KillSwitch):
    """Durable token reservations on Postgres (platform migration 012 functions).

    reserve, release, and settle each open a short tenant() transaction and commit it.
    The caller must not already be inside a transaction that spans the model call.
    """

    def __init__(self, conn: psycopg.Connection, limit: int = 20000, enabled: bool = True):
        super().__init__(limit=limit, enabled=enabled)
        self._conn = conn

    def reserve(self, client_id: str, document_id: str, estimated_tokens: int) -> Reservation:
        if not self.enabled:
            return Reservation(str(uuid.uuid4()), client_id, document_id, estimated_tokens)
        reservation = Reservation(str(uuid.uuid4()), client_id, document_id, estimated_tokens)
        from finledger_platform.db import tenant

        try:
            with tenant(self._conn, client_id):
                self._conn.execute(
                    "select llm_reserve_tokens(%s::uuid, %s::uuid, %s::uuid, %s, %s)",
                    (client_id, document_id, reservation.reservation_id, estimated_tokens, self.limit),
                )
        except psycopg.Error as exc:
            if "ai_budget_exceeded" in str(exc):
                raise TokenCapExceededException(
                    f"Token cap exceeded for client {client_id}, doc {document_id}. Limit: {self.limit}"
                ) from exc
            raise
        return reservation

    def reconcile(self, reservation: Reservation, actual_tokens: int):
        if not self.enabled:
            return 0
        from finledger_platform.db import tenant

        with tenant(self._conn, reservation.client_id):
            self._conn.execute("select llm_reconcile_tokens(%s::uuid)", (reservation.reservation_id,))
        return actual_tokens

    def release(self, reservation: Reservation):
        if not self.enabled:
            return
        from finledger_platform.db import tenant

        with tenant(self._conn, reservation.client_id):
            self._conn.execute("select llm_release_tokens(%s::uuid)", (reservation.reservation_id,))

    def settle_usage(
        self,
        reservation: Reservation,
        *,
        purpose: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        cost_usd: float | None,
    ) -> bool:
        """Insert the usage row and reconcile it on this hold. Both commit or both roll back."""
        if not self.enabled:
            return False
        write_usage_and_reconcile(
            self._conn,
            client_id=reservation.client_id,
            document_id=reservation.document_id,
            reservation_id=reservation.reservation_id,
            purpose=purpose,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
        )
        return True
