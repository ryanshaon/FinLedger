from finledger_control.web import render_inbox, render_review


def detail():
    return {"document_id":"11111111-1111-1111-1111-111111111111","filename":"INV-42.pdf","status":"in_review",
            "received_at":"2026-10-05T10:00:00+05:30","revision":2,"source_url":"/signed/source",
            "invoice":{"invoice_no":"INV-42","invoice_date":"2026-10-05","vendor":{"name":"Shree & Ganesh","gstin":"27AAACS1234A1Z2"},
                       "buyer_gstin":"27AAPFU0939F1ZV","po_number":"PO-1","total":11800,
                       "confidences":{"invoice_no":.98,"date":.91,"gstin":.99,"total":.95}},
            "risk":{"score":41,"band":"medium","marks":[{"field":"total","check":"lines_mismatch","ok":False,"note":"header mismatch"}]},
            "draft":{"party_ledger":"Shree <script>alert(1)</script>","lines":[{"ledger":"Purchase - Steel","amount":11800}],
                     "gst":{"treatment":"intra"},"tds":{"applicable":False},"bill_wise":{"ref":"INV-42","due_date":None},
                     "reasons":["mapped from vendor memory"]},"ai_paragraph":"The mapping follows prior steel purchases."}


def test_review_screen_is_three_column_accessible_and_escapes_values():
    html = render_review(detail(), roles={"approver"})
    assert html.count('class="review-column') == 3
    assert 'aria-label="Source document"' in html
    assert "Approve &amp; post" in html and "Approve draft only" in html
    assert "&lt;script&gt;" in html and "<script>alert(1)</script>" not in html
    assert "header mismatch" in html and "Medium · 41" in html
    assert "@media (max-width: 980px)" in html and ":focus-visible" in html


def test_clerk_does_not_receive_approval_controls():
    html = render_review(detail(), roles={"ap_clerk"})
    assert "Approve &amp; post" not in html
    assert "Save edit" in html


def test_inbox_has_required_queues_and_clear_empty_state():
    html = render_inbox([], {"new":0,"needs_review":0,"approved":0,"posted":0,"exception":0}, "needs_review", "Acme Steel")
    for label in ("New", "Needs review", "Approved", "Posted", "Exception"):
        assert label in html
    assert "No bills need review" in html

