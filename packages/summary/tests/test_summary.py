import pytest
from unittest.mock import patch
from summary.summary_worker import SummaryWorker
from usage.usage_writer import UsageWriter
from contracts.schemas import CanonicalInvoice, VoucherDraft, Vendor, RiskScore, RiskMark

@pytest.fixture
def inv_draft_risk():
    inv = CanonicalInvoice(
        document_id="doc-sum-1",
        document_type="purchase_invoice",
        vendor=Vendor(name="V1", gstin="29V1", pan="V1", state="KA"),
        invoice_no="INV-1",
        invoice_date="2026-09-01",
        total=100.0,
        taxable=100.0
    )
    draft = VoucherDraft(
        document_id="doc-sum-1",
        date="2026-09-01",
        voucher_type="purchase",
        party_ledger="V1",
        lines=[],
        gst={"treatment": "intra", "input_ledgers": [], "tax_breakup": {}},
        tds={"applicable": False, "amount": 0.0},
        bill_wise={"ref_type": "new_ref", "ref": "INV-1", "due_date": "2026-10-01"},
        narration="Test",
        map_trace_id="123",
        attachment_document_id="doc-sum-1"
    )
    risk = RiskScore(
        document_id="doc-sum-1",
        score=0,
        band="low",
        marks=[RiskMark(field="narration", check="suspicious_language", ok=True, note="Looks good")]
    )
    return inv, draft, risk

@patch('summary.summary_worker.SummaryWorker._call_llm')
def test_summary_worker(mock_call_llm, inv_draft_risk):
    inv, draft, risk = inv_draft_risk
    
    mock_call_llm.return_value = "Everything mapped fine."
    
    writer = UsageWriter()
    worker = SummaryWorker(writer)
    
    result = worker.generate_summary("client1", inv, draft, risk)
    
    lines = result.splitlines()
    assert 4 <= len(lines) <= 6
    assert any("V1" in line for line in lines)
    assert any("100" in line for line in lines)
    assert any("intra" in line.lower() for line in lines)
    assert any("low" in line.lower() for line in lines)
    assert any("V1" in line and "ledger" in line.lower() for line in lines)
    assert len(writer.logs) == 1


def test_summary_runs_without_monkeypatch(inv_draft_risk):
    result = SummaryWorker(UsageWriter()).generate_summary("client1", *inv_draft_risk)
    assert len(result.splitlines()) == 5

