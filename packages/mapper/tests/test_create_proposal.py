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
        document_id="doc-999",
        document_type="purchase_invoice",
        vendor=Vendor(name="Unknown Vendor", gstin="UNKNWN123", pan="UNKN", state="KA"),
        invoice_no="INV-999",
        invoice_date="2026-09-01",
        total=100.0,
        taxable=100.0
    )

@patch('mapper.map_worker.MapWorker._call_llm')
def test_map_worker_create_proposal(mock_call_llm, invoice):
    # Setup mock LLM response returning party_create_proposal = true
    mock_call_llm.return_value = '''
    {
      "document_id": "doc-999",
      "date": "2026-09-01",
      "voucher_type": "purchase",
      "party_ledger": "Unknown Vendor (Proposed)",
      "party_create_proposal": true,
      "lines": [{"ledger": "Purchases", "amount": 100.0, "is_debit": true}],
      "gst": {"treatment": "intra", "input_ledgers": [], "tax_breakup": {}},
      "tds": {"applicable": false, "amount": 0.0},
      "bill_wise": {"ref_type": "new_ref", "ref": "INV-999", "due_date": "2026-09-01"},
      "narration": "Being purchase from Unknown Vendor",
      "attachment_document_id": "doc-999",
      "reasons": ["Vendor not found in RAG chunks. Proposing creation."]
    }
    '''
    
    indexer = Indexer()
    retriever = Retriever(indexer)
    usage = UsageWriter()
    worker = MapWorker(usage, retriever)
    
    draft = worker.map_invoice("client1", invoice, "policy")
    
    assert draft.party_create_proposal is True
    assert "Proposed" in draft.party_ledger
