from contracts.schemas import BillWise, CorrectionEvent


def test_bill_wise_allows_missing_due_date_from_extraction():
    bill = BillWise(ref="INV-001", due_date=None)
    assert bill.due_date is None


def test_correction_event_accepts_person3_review_payload():
    event = CorrectionEvent(
        client_id="client-1",
        document_id="doc-1",
        vendor_gstin="29ABCDE1234F1Z5",
        field="ledger",
        old_value="Office Expenses",
        new_value="Professional Fees",
        hsn="9983",
        description="Consulting services",
        actor_id="reviewer-1",
        created_at="2026-10-06T00:00:00Z",
    )
    assert event.hsn_sac == "9983"
    assert event.old_ledger == "Office Expenses"
    assert event.new_ledger == "Professional Fees"
