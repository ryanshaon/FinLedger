import threading
import uuid
from dataclasses import dataclass


class TokenCapExceededException(Exception):
    pass


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    client_id: str
    document_id: str
    estimated_tokens: int


class KillSwitch:
    def __init__(self, limit: int = 20000, enabled: bool = True):
        self.limit = limit
        self.enabled = enabled
        self._usage_by_doc = {}
        self._reserved_by_doc = {}
        self._reservations = {}
        self._lock = threading.RLock()

    @staticmethod
    def _key(client_id: str, document_id: str):
        return client_id, document_id

    def reserve(self, client_id: str, document_id: str, estimated_tokens: int) -> Reservation:
        if estimated_tokens < 0:
            raise ValueError("estimated_tokens cannot be negative")
        reservation = Reservation(str(uuid.uuid4()), client_id, document_id, estimated_tokens)
        if not self.enabled:
            return reservation
        key = self._key(client_id, document_id)
        with self._lock:
            used = self._usage_by_doc.get(key, 0)
            reserved = self._reserved_by_doc.get(key, 0)
            if used + reserved + estimated_tokens > self.limit:
                raise TokenCapExceededException(
                    f"Token cap exceeded for client {client_id}, doc {document_id}. Limit: {self.limit}, "
                    f"Used: {used}, Reserved: {reserved}, Requested: {estimated_tokens}"
                )
            self._reserved_by_doc[key] = reserved + estimated_tokens
            self._reservations[reservation.reservation_id] = reservation
        return reservation

    def reconcile(self, reservation: Reservation, actual_tokens: int):
        if actual_tokens < 0:
            raise ValueError("actual_tokens cannot be negative")
        if not self.enabled:
            return 0
        key = self._key(reservation.client_id, reservation.document_id)
        with self._lock:
            active = self._reservations.pop(reservation.reservation_id, None)
            if active is None:
                raise ValueError("Unknown or already reconciled reservation")
            self._reserved_by_doc[key] = max(0, self._reserved_by_doc.get(key, 0) - active.estimated_tokens)
            self._usage_by_doc[key] = self._usage_by_doc.get(key, 0) + actual_tokens
            return self._usage_by_doc[key]

    def release(self, reservation: Reservation):
        if not self.enabled:
            return
        key = self._key(reservation.client_id, reservation.document_id)
        with self._lock:
            active = self._reservations.pop(reservation.reservation_id, None)
            if active is not None:
                self._reserved_by_doc[key] = max(0, self._reserved_by_doc.get(key, 0) - active.estimated_tokens)

    def usage(self, client_id: str, document_id: str) -> int:
        with self._lock:
            return self._usage_by_doc.get(self._key(client_id, document_id), 0)

    def reserved(self, client_id: str, document_id: str) -> int:
        with self._lock:
            return self._reserved_by_doc.get(self._key(client_id, document_id), 0)

    def check_and_add(self, doc_id: str, tokens_in: int, tokens_out: int):
        reservation = self.reserve("", doc_id, tokens_in + tokens_out)
        return self.reconcile(reservation, tokens_in + tokens_out)
