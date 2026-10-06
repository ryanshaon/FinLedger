from datetime import date

import pytest

from finledger_control.service import ControlService, Conflict, Forbidden


def canonical(document_id, **overrides):
    inv = {"document_id": document_id, "document_type": "purchase_invoice",
           "vendor": {"name": "Shree Ganesh Metals", "gstin": "27AAACS1234A1Z2"},
           "buyer_gstin": "27AAPFU0939F1ZV", "invoice_no": "INV-42", "invoice_date": date.today().isoformat(),
           "due_date": None, "irn": "x", "po_number": "PO-1", "grn_number": None,
           "taxable": 10000, "cgst": 900, "sgst": 900, "igst": 0, "cess": 0, "round_off": 0, "total": 11800,
           "line_items": [{"desc":"steel","hsn_sac":"7208","qty":1,"rate":10000,"taxable":10000,"tax_rate":18,"tax_amount":1800}],
           "bank_upi": None, "confidences":{"invoice_no":.99,"date":.99,"gstin":.99,"total":.99},
           "raw_markdown_path":"x", "page_image_paths":[]}
    inv.update(overrides); return inv


def draft(document_id, total=11800):
    return {"document_id":document_id,"date":date.today().isoformat(),"voucher_type":"purchase",
            "party_ledger":"Shree Ganesh Metals","party_create_proposal":False,
            "lines":[{"ledger":"Purchase - Steel","amount":total,"is_debit":True,"cost_centre":None}],
            "gst":{"treatment":"intra","input_ledgers":[],"tax_breakup":{}},
            "tds":{"applicable":False,"section":None,"ledger":None,"amount":0},
            "bill_wise":{"ref_type":"new_ref","ref":"INV-42","due_date":None}, "narration":"",
            "attachment_document_id":document_id,"reasons":[],"map_trace_id":"22222222-2222-2222-2222-222222222222"}


def test_score_persists_marks_and_advances_to_scored(conn, world, document):
    result = ControlService(conn).score(world["client"]["id"], document, canonical(document), world["clerk"])
    assert result["band"] == "low"
    row = conn.execute("select status from documents where id=%s", (document,)).fetchone()
    assert row is None  # RLS fails closed outside tenant transaction


def test_medium_draft_creates_review_task_and_no_post_job(conn, world, document):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document,
              canonical(document, confidences={"invoice_no":.2,"date":.2,"gstin":.99,"total":.99}), world["clerk"])
    routed = svc.submit_draft(world["client"]["id"], document, draft(document), world["clerk"])
    assert routed["route"] == "review"
    assert routed["status"] == "in_review"
    assert svc.queue_counts(world["client"]["id"])["needs_review"] == 1
    assert svc.post_job_count(world["client"]["id"], document) == 0


def test_high_band_cannot_be_forced_to_person4(conn, world, document):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, canonical(document, buyer_gstin="29ABCDE1234F1Z5"), world["clerk"])
    routed = svc.submit_draft(world["client"]["id"], document, draft(document), world["clerk"])
    assert routed["route"] == "review"
    with pytest.raises(Conflict):
        svc.approve(world["client"]["id"], document, world["approver"], post=True, expected_revision=99)
    assert svc.post_job_count(world["client"]["id"], document) == 0


def test_approve_and_post_is_idempotent_and_audited(conn, world, document):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document,
              canonical(document, confidences={"invoice_no":.2,"date":.2,"gstin":.99,"total":.99}), world["clerk"])
    routed = svc.submit_draft(world["client"]["id"], document, draft(document), world["clerk"])
    out = svc.approve(world["client"]["id"], document, world["approver"], post=True,
                      expected_revision=routed["revision"])
    assert out["status"] == "approved"
    assert svc.post_job_count(world["client"]["id"], document) == 1
    again = svc.approve(world["client"]["id"], document, world["approver"], post=True,
                        expected_revision=out["revision"])
    assert again["status"] == "approved" and svc.post_job_count(world["client"]["id"], document) == 1


def test_maker_cannot_self_approve(conn, world, document):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, canonical(document), world["clerk"])
    routed = svc.submit_draft(world["client"]["id"], document, draft(document), world["clerk"])
    with pytest.raises(Forbidden, match="self-approve"):
        svc.approve(world["client"]["id"], document, world["clerk"], post=False,
                    expected_revision=routed["revision"])


def test_edit_party_ledger_writes_correction_event_and_requires_reapproval(conn, world, document):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, canonical(document), world["clerk"])
    routed = svc.submit_draft(world["client"]["id"], document, draft(document), world["clerk"])
    edited = svc.edit_field(world["client"]["id"], document, world["clerk"], "party_ledger", "Ganesh Metals - AP",
                            expected_revision=routed["revision"])
    assert edited["revision"] == routed["revision"] + 1
    event = svc.corrections(world["client"]["id"], document)[0]
    assert event["field"] == "party_ledger" and event["old_value"] == "Shree Ganesh Metals"


def test_reject_and_resubmit_never_enqueue_post(conn, world, document):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, canonical(document), world["clerk"])
    routed = svc.submit_draft(world["client"]["id"], document, draft(document), world["clerk"])
    result = svc.reject(world["client"]["id"], document, world["approver"], "wrong entity",
                        resubmit=True, expected_revision=routed["revision"])
    assert result["status"] == "resubmit"
    assert svc.post_job_count(world["client"]["id"], document) == 0


def test_person4_failure_moves_to_exception_without_new_post_job(conn, world, document):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, canonical(document), world["clerk"])
    routed = svc.submit_draft(world["client"]["id"], document, draft(document), world["clerk"])
    svc.approve(world["client"]["id"], document, world["approver"], post=True, expected_revision=routed["revision"])
    job = svc.post_job(world["client"]["id"], document)
    result = svc.erp_result(world["client"]["id"], document, ok=False, error="Tally company unavailable",
                            job_id=job["id"], revision=routed["revision"],
                            idempotency_key=f"{document}:r{routed['revision']}")
    assert result["status"] == "exception"
    assert svc.post_job_count(world["client"]["id"], document) == 1


def test_multi_line_total_over_cap_routes_to_review(conn, world, document):
    svc = ControlService(conn)
    with svc._tx(world["client"]["id"]):
        conn.execute("update clients set maker_checker=false, auto_post_cap=10000 where id=%s", (world["client"]["id"],))
    svc.score(world["client"]["id"], document, canonical(document, total=15000), world["clerk"])
    proposed = draft(document, total=5000)
    proposed["lines"].append({"ledger":"Purchase - Other","amount":10000,"is_debit":True,"cost_centre":None})
    assert svc.submit_draft(world["client"]["id"], document, proposed, world["clerk"])["route"] == "review"


def test_unreconciled_draft_total_is_rejected(conn, world, document):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, canonical(document), world["clerk"])
    with pytest.raises(Conflict, match="reconcile"):
        svc.submit_draft(world["client"]["id"], document, draft(document, total=5000), world["clerk"])


def test_approve_only_can_be_dispatched_later_idempotently(conn, world, document):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, canonical(document), world["clerk"])
    routed = svc.submit_draft(world["client"]["id"], document, draft(document), world["clerk"])
    svc.approve(world["client"]["id"], document, world["approver"], post=False, expected_revision=routed["revision"])
    assert svc.post_job_count(world["client"]["id"], document) == 0
    svc.approve(world["client"]["id"], document, world["approver"], post=True, expected_revision=routed["revision"])
    svc.approve(world["client"]["id"], document, world["approver"], post=True, expected_revision=routed["revision"])
    assert svc.post_job_count(world["client"]["id"], document) == 1


def test_edit_after_approval_invalidates_approval_and_cancels_unclaimed_job(conn, world, document):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, canonical(document), world["clerk"])
    routed = svc.submit_draft(world["client"]["id"], document, draft(document), world["clerk"])
    svc.approve(world["client"]["id"], document, world["approver"], post=True, expected_revision=routed["revision"])
    edited = svc.edit_field(world["client"]["id"], document, world["clerk"], "party_ledger", "Changed",
                            expected_revision=routed["revision"])
    detail = svc.review_detail(world["client"]["id"], document)
    assert edited["revision"] == 2 and detail["status"] == "in_review"
    with svc._tx(world["client"]["id"]):
        assert conn.execute("select count(*) n from approvals where document_id=%s and superseded_at is null", (document,)).fetchone()["n"] == 0
        assert conn.execute("select state from jobs where document_id=%s and queue='post'", (document,)).fetchone()["state"] == "cancelled"
        tasks = conn.execute("select status,draft_revision from review_tasks where document_id=%s order by id", (document,)).fetchall()
        assert tasks == [{"status":"approved","draft_revision":1},{"status":"open","draft_revision":2}]


def test_edit_is_blocked_once_post_job_is_claimed(conn, world, document):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, canonical(document), world["clerk"])
    routed = svc.submit_draft(world["client"]["id"], document, draft(document), world["clerk"])
    svc.approve(world["client"]["id"], document, world["approver"], post=True, expected_revision=routed["revision"])
    with svc._tx(world["client"]["id"]):
        conn.execute("update jobs set state='running', claim_token=gen_random_uuid() where document_id=%s and queue='post'", (document,))
    with pytest.raises(Conflict, match="active post"):
        svc.edit_field(world["client"]["id"], document, world["clerk"], "party_ledger", "Changed", expected_revision=1)


def test_boundary_rejects_mismatched_document_ids(conn, world, document):
    svc = ControlService(conn)
    wrong = "22222222-2222-2222-2222-222222222222"
    with pytest.raises(Conflict, match="document_id"):
        svc.score(world["client"]["id"], document, canonical(wrong), world["clerk"])
    svc.score(world["client"]["id"], document, canonical(document), world["clerk"])
    bad = draft(document); bad["attachment_document_id"] = wrong
    with pytest.raises(Conflict, match="attachment_document_id"):
        svc.submit_draft(world["client"]["id"], document, bad, world["clerk"])


def test_erp_callback_rejects_stale_revision_and_persists_success(conn, world, document):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, canonical(document), world["clerk"])
    routed = svc.submit_draft(world["client"]["id"], document, draft(document), world["clerk"])
    svc.approve(world["client"]["id"], document, world["approver"], post=True, expected_revision=1)
    job = svc.post_job(world["client"]["id"], document)
    with pytest.raises(Conflict, match="revision"):
        svc.erp_result(world["client"]["id"], document, ok=True, erp_id="TALLY-1", job_id=job["id"],
                       revision=2, idempotency_key=f"{document}:r2")
    result = svc.erp_result(world["client"]["id"], document, ok=True, erp_id="TALLY-1", job_id=job["id"],
                            revision=1, idempotency_key=f"{document}:r1")
    assert result["status"] == "posted"
    with svc._tx(world["client"]["id"]):
        row = conn.execute("select erp_id,result from posting_attempts where job_id=%s", (job["id"],)).fetchone()
        assert row["erp_id"] == "TALLY-1" and row["result"] == "posted"

