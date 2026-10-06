import pytest
from pydantic import ValidationError

from contracts.schemas import CanonicalInvoice, RiskMark, Vendor, VoucherDraft
from safety.risk_worker import RiskWorker
from usage.usage_writer import UsageWriter


def _objects():
    invoice = CanonicalInvoice(document_id="d1", document_type="purchase_invoice", vendor=Vendor(name="V"), total=100)
    draft = VoucherDraft(document_id="d1", date="2026-09-01", voucher_type="purchase", party_ledger="V",
        bill_wise={"ref": "I1"}, attachment_document_id="d1", map_trace_id="t1")
    return invoice, draft


def test_mock_risk_runs_and_returns_only_language_fraud_marks():
    score = RiskWorker(UsageWriter()).assess_risk("client-a", *_objects())
    assert score.document_id == "d1"
    assert all(mark.check in {"suspicious_language", "payment_instruction_anomaly", "identity_impersonation"} for mark in score.marks)


@pytest.mark.parametrize("check", ["duplicate_invoice", "gst_math", "gstin_checksum"])
def test_forbidden_deterministic_risk_checks_are_rejected(check):
    with pytest.raises(ValidationError):
        RiskMark(field="invoice", check=check, ok=False, note="forbidden")

