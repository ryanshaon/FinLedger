import pytest
from rag.indexer import Indexer
from rag.retriever import Retriever
from rag.chunk_types import ChunkType

def test_retrieval_ranking():
    indexer = Indexer()
    # Populate multiple chunks
    for i in range(15):
        indexer.upsert("client1", f"chunk_{i}", ChunkType.LEDGER, f"Content {i}")
        
    # Insert specific matching chunks
    indexer.upsert("client1", "chunk_match", ChunkType.VENDOR, "Vendor 29ABCDE1234F1Z5 details")
    
    retriever = Retriever(indexer)
    results = retriever.retrieve("client1", gstin="29ABCDE1234F1Z5", vendor_name="", descriptions=[], hsn="", amount_band="", doc_type="", top_k=8)
    
    assert len(results) == 8
    # The matching chunk should be first because of the mock scoring
    assert results[0]["chunk_id"] == "chunk_match"


def test_retrieval_uses_all_dimensions_and_exposes_ranking_reason():
    indexer = Indexer()
    indexer.upsert("client1", "generic", ChunkType.LEDGER, "unrelated", {"gstin": "29ABCDE1234F1Z5"})
    indexer.upsert("client1", "specific", ChunkType.MEMORY, "Professional Fees consulting 9983 purchase_invoice",
        {"vendor_name": "Acme Advisory", "hsn": "9983", "amount": 10000, "doc_type": "purchase_invoice"})
    results = Retriever(indexer).retrieve("client1", "", "Acme Advisory", ["consulting"], "9983", "10000", "purchase_invoice")
    assert results[0]["chunk_id"] == "specific"
    assert results[0]["score"] > results[1]["score"]
    assert {"vendor_name", "description", "hsn", "amount", "doc_type"}.issubset(results[0]["ranking_reasons"])

