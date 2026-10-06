from datetime import date, timedelta
from decimal import Decimal

from finledger_control.risk import RiskContext, score_invoice


def invoice(**overrides):
    value = {
        "document_id": "11111111-1111-1111-1111-111111111111",
        "vendor": {"name": "Shree Ganesh Metals", "gstin": "27AAACS1234A1Z2"},
        "buyer_gstin": "27AAPFU0939F1ZV",
        "invoice_no": "INV-42",
        "invoice_date": date.today().isoformat(),
        "due_date": (date.today() + timedelta(days=30)).isoformat(),
        "taxable": 100000, "cgst": 9000, "sgst": 9000, "igst": 0, "cess": 0,
        "total": 118000,
        "line_items": [{"desc": "HR Coil", "qty": 10, "rate": 10000, "taxable": 100000,
                        "tax_rate": 18, "tax_amount": 18000}],
        "confidences": {"invoice_no": .98, "date": .98, "gstin": .98, "total": .98},
        "po_number": "PO-1", "grn_number": "GRN-1", "bank_upi": None, "irn": "IRN-1",
    }
    value.update(overrides)
    return value


def context(**overrides):
    value = dict(client_gstins=("27AAPFU0939F1ZV",), vendor_exists=True, vendor_msme=False,
                 vendor_median=Decimal("110000"), po_required=False, match_mode="invoice_only")
    value.update(overrides)
    return RiskContext(**value)


def test_clean_invoice_is_low_with_field_level_pass_marks():
    result = score_invoice(invoice(), context())
    assert result.band == "low"
    assert result.score < 25
    assert any(m.field == "buyer_gstin" and m.ok for m in result.marks)
    assert all(m.field and m.check for m in result.marks)


def test_buyer_gstin_mismatch_is_high_and_cannot_be_diluted_by_llm():
    result = score_invoice(invoice(buyer_gstin="29ABCDE1234F1Z5"), context(), llm_score=0)
    assert result.band == "high"
    assert result.score >= 60
    assert any(m.check == "client_identity" and not m.ok for m in result.marks)


def test_duplicate_vendor_invoice_fy_and_amount_holds_high():
    result = score_invoice(invoice(), context(duplicate=True))
    assert result.band == "high"
    assert any(m.check == "duplicate" and not m.ok for m in result.marks)


def test_low_confidence_and_line_mismatch_create_medium_review():
    inv = invoice(total=120000, confidences={"invoice_no": .60, "date": .98, "gstin": .98, "total": .98})
    result = score_invoice(inv, context())
    assert result.band == "medium"
    assert {m.field for m in result.marks if not m.ok} >= {"invoice_no", "total"}


def test_interstate_invoice_rejects_cgst_sgst_tax_shape():
    inv = invoice(buyer_gstin="29ABCDE1234F1Z5")
    result = score_invoice(inv, context(client_gstins=("29ABCDE1234F1Z5",)))
    assert any(m.check == "gst_treatment" and not m.ok for m in result.marks)


def test_po_policy_future_date_msme_and_180_day_checks_are_marks():
    old = (date.today() - timedelta(days=181)).isoformat()
    result = score_invoice(invoice(invoice_date=old, po_number=None, due_date=(date.today()+timedelta(days=60)).isoformat()),
                           context(po_required=True, vendor_msme=True))
    checks = {m.check for m in result.marks if not m.ok}
    assert {"po_required", "msme_45_day", "itc_180_day"} <= checks


def test_llm_score_can_raise_but_never_lower_deterministic_score():
    base = score_invoice(invoice(), context())
    raised = score_invoice(invoice(), context(), llm_score=52,
                           llm_marks=[{"field": "vendor", "check": "language_anomaly", "ok": False, "note": "urgent wording"}])
    assert raised.score == 52 and raised.band == "medium"
    assert raised.score >= base.score


def test_invalid_vendor_gstin_checksum_and_master_name_mismatch_are_separate_marks():
    inv = invoice(vendor={"name":"Completely Different Trading Co","gstin":"27AAACS1234A1Z9"})
    result = score_invoice(inv, context(vendor_master_name="Shree Ganesh Metals"))
    failed = {(m.field,m.check) for m in result.marks if not m.ok}
    assert ("vendor.gstin","gstin_checksum") in failed
    assert ("vendor.name","master_name") in failed

