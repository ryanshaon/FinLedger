import pytest
from unittest.mock import patch
from mapper.map_worker import MapWorker
from rag.indexer import Indexer
from rag.retriever import Retriever
from usage.usage_writer import UsageWriter
from contracts.schemas import CanonicalInvoice, Vendor

@pytest.fixture
def invoice():
    return CanonicalInvoice(
        document_id="doc-123",
        document_type="purchase_invoice",
        vendor=Vendor(name="Vendor X", gstin="29XYZ", pan="XYZ", state="KA"),
        invoice_no="INV-123",
        invoice_date="2026-09-01",
        due_date="2026-10-01",
        total=100.0,
        taxable=100.0
    )

@patch('mapper.map_worker.MapWorker._call_llm')
def test_map_worker_valid_draft(mock_call_llm, invoice):
    # Setup mock LLM response
    mock_call_llm.return_value = '''
    {
      "document_id": "doc-123",
      "date": "2026-09-01",
      "voucher_type": "purchase",
      "party_ledger": "Vendor X Ledger",
      "party_create_proposal": false,
      "lines": [{"ledger": "Purchases", "amount": 100.0, "is_debit": true}],
      "gst": {"treatment": "intra", "input_ledgers": [], "tax_breakup": {}},
      "tds": {"applicable": false, "amount": 0.0},
      "bill_wise": {"ref_type": "new_ref", "ref": "INV-123", "due_date": "2026-10-01"},
      "narration": "Being purchase from Vendor X",
      "attachment_document_id": "doc-123",
      "reasons": ["Mapped because of RAG match"]
    }
    '''
    
    indexer = Indexer()
    retriever = Retriever(indexer)
    usage = UsageWriter()
    worker = MapWorker(usage, retriever)
    
    draft = worker.map_invoice("client1", invoice, "policy")
    
    assert draft.document_id == "doc-123"
    assert draft.party_ledger == "Vendor X Ledger"
    assert draft.party_create_proposal is False
    assert "<xml>" not in mock_call_llm.return_value
    assert len(usage.logs) == 1
    assert draft.map_trace_id is not None
