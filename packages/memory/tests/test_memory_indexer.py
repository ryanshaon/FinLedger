from contracts.schemas import CorrectionEvent
from memory.memory_indexer import MemoryIndexer
from rag.chunk_types import ChunkType
from rag.indexer import Indexer


def test_correction_and_posted_bill_upsert_are_idempotent_and_isolated():
    store = Indexer()
    memory = MemoryIndexer(store)
    event = CorrectionEvent(client_id="a", document_id="d1", vendor_gstin="29ABCDE1234F1Z5",
        field="ledger", old_value="Old", new_value="New", hsn="9983", description="Consulting")
    first = memory.index_correction(event)
    second = memory.index_correction(event)
    assert first == second
    assert len(store.get_namespace_chunks("a")) == 1
    assert store.get_namespace_chunks("b") == []
    posted = memory.index_posted_bill("a", "d1", "erp-1", "29ABCDE1234F1Z5", "Consulting", "9983", 100)
    assert posted["chunk_type"] == ChunkType.POSTED_BILL
    memory.index_posted_bill("a", "d1", "erp-1", "29ABCDE1234F1Z5", "Consulting", "9983", 100)
    assert len(store.get_namespace_chunks("a")) == 2

