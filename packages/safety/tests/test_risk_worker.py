import pytest
from unittest.mock import patch
from safety.risk_worker import RiskWorker
from usage.usage_writer import UsageWriter
from contracts.schemas import CanonicalInvoice, VoucherDraft, Vendor

@pytest.fixture
def invoice_and_draft():
    inv = CanonicalInvoice(
        document_id="doc-risk-1",
        document_type="purchase_invoice",
        vendor=Vendor(name="V1", gstin="29V1", pan="V1", state="KA"),
        invoice_no="INV-1",
        invoice_date="2026-09-01",
        total=100.0,
        taxable=100.0
    )
    draft = VoucherDraft(
        document_id="doc-risk-1",
        date="2026-09-01",
        voucher_type="purchase",
        party_ledger="V1",
        lines=[],
        gst={"treatment": "intra", "input_ledgers": [], "tax_breakup": {}},
        tds={"applicable": False, "amount": 0.0},
        bill_wise={"ref_type": "new_ref", "ref": "INV-1", "due_date": "2026-10-01"},
        narration="Test",
        map_trace_id="123",
        attachment_document_id="doc-risk-1"
    )
    return inv, draft

@patch('safety.risk_worker.RiskWorker._call_llm')
def test_risk_worker(mock_call_llm, invoice_and_draft):
    inv, draft = invoice_and_draft
    
    mock_call_llm.return_value = '''
    {
      "document_id": "doc-risk-1",
      "score": 80,
      "band": "high",
      "marks": [
        {
          "field": "total",
          "check": "historical_amount_anomaly",
          "ok": false,
          "note": "High value compared to historical norms"
        }
      ]
    }
    '''
    
    writer = UsageWriter()
    worker = RiskWorker(writer)
    
    score = worker.assess_risk("client1", inv, draft)
    
    assert score.score == 80
    assert score.band == "high"
    assert "historical_amount_anomaly" in score.marks[0].check
    assert len(writer.logs) == 1
