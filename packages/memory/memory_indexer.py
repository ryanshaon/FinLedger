import hashlib
import json

from contracts.schemas import CorrectionEvent
from rag.chunk_types import ChunkType
from rag.indexer import Indexer


class MemoryIndexer:
    def __init__(self, indexer: Indexer):
        self.indexer = indexer

    @staticmethod
    def _stable_id(prefix: str, payload: dict) -> str:
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:24]
        return f"{prefix}:{digest}"

    def index_correction(self, event: CorrectionEvent) -> dict:
        if not event.client_id:
            raise ValueError("CorrectionEvent.client_id is required for tenant isolation")
        payload = event.model_dump(mode="json")
        chunk_id = self._stable_id("correction", payload)
        content = f"Correction for {event.vendor_gstin}: {event.description} HSN {event.hsn_sac}; {event.field} changed from {event.old_ledger} to {event.new_ledger}"
        return self.indexer.upsert(event.client_id, chunk_id, ChunkType.MEMORY, content, {
            "gstin": event.vendor_gstin, "hsn": event.hsn_sac, "description": event.description,
            "document_id": event.document_id, "field": event.field,
        })

    def index_posted_bill(self, client_id: str, document_id: str, erp_id: str, vendor_gstin: str,
                          description: str, hsn: str, amount: float, doc_type: str = "purchase_invoice") -> dict:
        chunk_id = f"posted:{document_id}:{erp_id}"
        content = f"Posted bill {erp_id} vendor {vendor_gstin} {description} HSN {hsn} amount {amount} {doc_type}"
        return self.indexer.upsert(client_id, chunk_id, ChunkType.POSTED_BILL, content, {
            "document_id": document_id, "erp_id": erp_id, "gstin": vendor_gstin,
            "description": description, "hsn": hsn, "amount": amount, "doc_type": doc_type,
        })
