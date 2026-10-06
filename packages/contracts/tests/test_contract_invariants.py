import pytest
from pydantic import ValidationError

from contracts.schemas import CanonicalInvoice, Confidences, RiskScore, Vendor, VoucherDraft


def test_confidence_and_risk_bounds():
    with pytest.raises(ValidationError):
        Confidences(total=1.1)
    with pytest.raises(ValidationError):
        RiskScore(document_id="d", score=101, band="high", marks=[])


def test_contract_enums_and_id_consistency():
    with pytest.raises(ValidationError):
        CanonicalInvoice(document_id="d", document_type="random", vendor=Vendor())
    with pytest.raises(ValidationError):
        VoucherDraft(document_id="d", date="2026-01-01", voucher_type="purchase", party_ledger="V",
            bill_wise={"ref": "1"}, attachment_document_id="other", map_trace_id="t")

