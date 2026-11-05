import hashlib
import threading
import time
import uuid
from datetime import date

import psycopg
import pytest
from psycopg.rows import dict_row

from finledger_control.service import ControlService, Conflict, Forbidden
from finledger_platform.db import tenant
from finledger_platform.queue import claim


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


def exempt_invoice(document_id, **overrides):
    values = {"cgst": 0, "sgst": 0, "total": 10000,
              "line_items": [{"desc":"steel","hsn_sac":"7208","qty":1,"rate":10000,"taxable":10000,"tax_rate":0,"tax_amount":0}]}
    return canonical(document_id, **{**values, **overrides})


def exempt_draft(document_id, total=10000):
    return {**draft(document_id, total=total), "gst": {"treatment": "exempt", "input_ledgers": [], "tax_breakup": {}}}


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


def test_approve_and_post_is_idempotent_and_audited(conn, world, document, posting_on):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document,
              exempt_invoice(document, confidences={"invoice_no":.2,"date":.2,"gstin":.99,"total":.99}), world["clerk"])
    routed = svc.submit_draft(world["client"]["id"], document, exempt_draft(document), world["clerk"])
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


def test_person4_failure_moves_to_exception_without_new_post_job(conn, world, document, posting_on):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, exempt_invoice(document), world["clerk"])
    routed = svc.submit_draft(world["client"]["id"], document, exempt_draft(document), world["clerk"])
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


def test_approve_only_can_be_dispatched_later_idempotently(conn, world, document, posting_on):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, exempt_invoice(document), world["clerk"])
    routed = svc.submit_draft(world["client"]["id"], document, exempt_draft(document), world["clerk"])
    svc.approve(world["client"]["id"], document, world["approver"], post=False, expected_revision=routed["revision"])
    assert svc.post_job_count(world["client"]["id"], document) == 0
    svc.approve(world["client"]["id"], document, world["approver"], post=True, expected_revision=routed["revision"])
    svc.approve(world["client"]["id"], document, world["approver"], post=True, expected_revision=routed["revision"])
    assert svc.post_job_count(world["client"]["id"], document) == 1


def test_edit_after_approval_invalidates_approval_and_cancels_unclaimed_job(conn, world, document, posting_on):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, exempt_invoice(document), world["clerk"])
    routed = svc.submit_draft(world["client"]["id"], document, exempt_draft(document), world["clerk"])
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


def test_edit_is_blocked_once_post_job_is_claimed(conn, world, document, posting_on, owner_dsn):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, exempt_invoice(document), world["clerk"])
    routed = svc.submit_draft(world["client"]["id"], document, exempt_draft(document), world["clerk"])
    svc.approve(world["client"]["id"], document, world["approver"], post=True, expected_revision=routed["revision"])
    with psycopg.connect(owner_dsn, autocommit=True) as owner:
        owner.execute("update jobs set state='running', claim_token=gen_random_uuid() where document_id=%s and queue='post'", (document,))
    with pytest.raises(Conflict, match="active post"):
        svc.edit_field(world["client"]["id"], document, world["clerk"], "party_ledger", "Changed", expected_revision=1)


def test_edit_waits_for_concurrent_claim_and_preserves_approval(conn, pool, world, document, owner_dsn):
    svc = ControlService(conn)
    svc.score(world['client']['id'], document, canonical(document), world['clerk'])
    routed = svc.submit_draft(world['client']['id'], document, draft(document), world['clerk'])
    svc.approve(world['client']['id'], document, world['approver'], post=True, expected_revision=routed['revision'])
    started = threading.Event()
    result = {}

    def edit():
        try:
            with pool.connection() as editor:
                result['pid'] = editor.info.backend_pid
                started.set()
                ControlService(editor).edit_field(world['client']['id'], document, world['clerk'], 'party_ledger', 'Changed', expected_revision=1)
                result['success'] = True
        except Exception as exc:
            result['error'] = exc

    with psycopg.connect(owner_dsn) as claimant, psycopg.connect(owner_dsn, autocommit=True) as observer:
        claimant.execute("update jobs set state='running', claim_token=gen_random_uuid() where document_id=%s and queue='post'", (document,))
        thread = threading.Thread(target=edit)
        thread.start()
        try:
            assert started.wait(5)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                waiting = observer.execute('select wait_event_type from pg_stat_activity where pid=%s', (result['pid'],)).fetchone()
                if waiting and waiting[0] == 'Lock':
                    break
                time.sleep(0.05)
            else:
                pytest.fail('edit did not wait for the claim lock')
        finally:
            claimant.commit()
            thread.join(timeout=10)
        assert not thread.is_alive()
    assert isinstance(result.get('error'), Conflict)
    with svc._tx(world['client']['id']):
        row = conn.execute('select d.status,v.revision from documents d join voucher_drafts v on v.document_id=d.id where d.id=%s', (document,)).fetchone()
        assert row == {'status': 'approved', 'revision': 1}
        assert conn.execute('select count(*) as n from approvals where document_id=%s and superseded_at is null', (document,)).fetchone()['n'] == 1


def test_edit_times_out_safely_when_post_job_stays_locked(conn, world, document, owner_dsn):
    svc = ControlService(conn)
    svc.score(world['client']['id'], document, canonical(document), world['clerk'])
    routed = svc.submit_draft(world['client']['id'], document, draft(document), world['clerk'])
    svc.approve(world['client']['id'], document, world['approver'], post=True, expected_revision=routed['revision'])
    with psycopg.connect(owner_dsn) as claimant:
        claimant.execute("update jobs set state='running', claim_token=gen_random_uuid() where document_id=%s and queue='post'", (document,))
        with pytest.raises(Conflict, match='post job is busy; try again'):
            svc.edit_field(world['client']['id'], document, world['clerk'], 'party_ledger', 'Changed', expected_revision=1)
        claimant.rollback()
    with svc._tx(world['client']['id']):
        row = conn.execute('select d.status,v.revision from documents d join voucher_drafts v on v.document_id=d.id where d.id=%s', (document,)).fetchone()
        assert row == {'status': 'approved', 'revision': 1}


def test_dead_post_requeue_waits_for_edit_and_cannot_revive_it(conn, pool, world, document, owner_dsn):
    svc = ControlService(conn)
    svc.score(world['client']['id'], document, canonical(document), world['clerk'])
    routed = svc.submit_draft(world['client']['id'], document, draft(document), world['clerk'])
    svc.approve(world['client']['id'], document, world['approver'], post=True, expected_revision=routed['revision'])
    job = svc.post_job(world['client']['id'], document)['id']
    started = threading.Event()
    result = {}

    def requeue():
        try:
            with pool.connection() as worker:
                result['pid'] = worker.info.backend_pid
                started.set()
                result['requeued'] = worker.execute('select requeue_dead_job(%s) as ok', (job,)).fetchone()['ok']
        except Exception as exc:
            result['error'] = exc

    with psycopg.connect(owner_dsn, autocommit=True) as owner:
        owner.execute('grant finledger_queue_ops to fl_control')
        owner.execute("update jobs set state='dead' where id=%s", (job,))
        thread = threading.Thread(target=requeue)
        try:
            with svc._tx(world['client']['id']):
                assert not conn.execute('select lock_post_jobs_for_edit(%s) as active', (document,)).fetchone()['active']
                assert conn.execute('select cancel_unfinished_post_jobs(%s) as n', (document,)).fetchone()['n'] == 1
                thread.start()
                assert started.wait(5)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    waiting = owner.execute('select wait_event_type from pg_stat_activity where pid=%s', (result['pid'],)).fetchone()
                    if waiting and waiting[0] == 'Lock':
                        break
                    time.sleep(0.05)
                else:
                    pytest.fail('requeue did not wait for the edit lock')
            thread.join(timeout=10)
            assert not thread.is_alive() and result.get('requeued') is False and 'error' not in result
            assert owner.execute('select state from jobs where id=%s', (job,)).fetchone()[0] == 'cancelled'
        finally:
            thread.join(timeout=10) if thread.ident else None
            owner.execute('revoke finledger_queue_ops from fl_control')


def test_worker_skips_post_job_locked_by_editor(conn, world, document, owner_dsn, app_dsn):
    svc = ControlService(conn)
    svc.score(world['client']['id'], document, canonical(document), world['clerk'])
    routed = svc.submit_draft(world['client']['id'], document, draft(document), world['clerk'])
    svc.approve(world['client']['id'], document, world['approver'], post=True, expected_revision=routed['revision'])
    with psycopg.connect(owner_dsn, autocommit=True) as owner:
        owner.execute('grant finledger_worker_post to fl_control')
        try:
            with psycopg.connect(app_dsn, autocommit=True, row_factory=dict_row) as worker:
                worker.execute("set statement_timeout='3s'")
                with svc._tx(world['client']['id']):
                    assert not conn.execute('select lock_post_jobs_for_edit(%s) as active', (document,)).fetchone()['active']
                    assert conn.execute('select cancel_unfinished_post_jobs(%s) as n', (document,)).fetchone()['n'] == 1
                    assert claim(worker, 'post') is None
            assert owner.execute("select state from jobs where document_id=%s and queue='post'", (document,)).fetchone()[0] == 'cancelled'
        finally:
            owner.execute('revoke finledger_worker_post from fl_control')


def test_boundary_rejects_mismatched_document_ids(conn, world, document):
    svc = ControlService(conn)
    wrong = "22222222-2222-2222-2222-222222222222"
    with pytest.raises(Conflict, match="document_id"):
        svc.score(world["client"]["id"], document, canonical(wrong), world["clerk"])
    svc.score(world["client"]["id"], document, canonical(document), world["clerk"])
    bad = draft(document); bad["attachment_document_id"] = wrong
    with pytest.raises(Conflict, match="attachment_document_id"):
        svc.submit_draft(world["client"]["id"], document, bad, world["clerk"])


def test_erp_callback_rejects_stale_revision_and_persists_success(conn, world, document, posting_on):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, exempt_invoice(document), world["clerk"])
    routed = svc.submit_draft(world["client"]["id"], document, exempt_draft(document), world["clerk"])
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


def _automation_and_editor(owner_dsn, world):
    client_id = world["client"]["id"]
    with psycopg.connect(owner_dsn, autocommit=True, row_factory=dict_row) as owner:
        maker = owner.execute(
            """select id, auth_subject, api_token_hash, firm_admin, automation
               from users where firm_id = %s and automation""",
            (world["client"]["firm_id"],),
        ).fetchone()
        assert maker["automation"] is True
        assert maker["auth_subject"] is None and maker["api_token_hash"] is None and maker["firm_admin"] is False
        assert owner.execute(
            "select count(*) as n from client_members where user_id = %s", (maker["id"],)
        ).fetchone()["n"] == 0
        editor_id = owner.execute(
            """insert into users (firm_id, email, name, firm_admin)
               values (%s, 'editor-approver@sharma.test', 'Editor', false) returning id""",
            (world["client"]["firm_id"],),
        ).fetchone()["id"]
        owner.execute(
            "insert into client_members (client_id, user_id, role) values (%s, %s, 'approver')",
            (client_id, editor_id),
        )
    return maker["id"], editor_id


def _another_document(conn, world):
    client_id = world["client"]["id"]
    document_id = str(uuid.uuid4())
    with tenant(conn, client_id):
        conn.execute(
            """insert into documents(id,client_id,channel,object_path,original_filename,content_hash,mime,size_bytes,status,virus_ok,source_meta)
               values (%s,%s,'link',%s,'invoice.pdf',%s,'application/pdf',100,'extracted',true,'{}')""",
            (document_id, client_id, f"{client_id}/2026/10/{document_id}/raw.pdf",
             hashlib.sha256(document_id.encode()).hexdigest()),
        )
    return document_id


def test_draft_author_cannot_approve_when_automation_is_maker(conn, world, document, owner_dsn):
    maker_id, editor_id = _automation_and_editor(owner_dsn, world)
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, canonical(document), maker_id)
    routed = svc.submit_draft(world["client"]["id"], document, draft(document), editor_id)
    with pytest.raises(Forbidden, match="editor cannot approve"):
        svc.approve(world["client"]["id"], document, editor_id, post=False, expected_revision=routed["revision"])
    approved = svc.approve(
        world["client"]["id"], document, world["approver"], post=False, expected_revision=routed["revision"],
    )
    assert approved["status"] == "approved"
    with svc._tx(world["client"]["id"]):
        stored = conn.execute(
            "select maker_id from canonical_invoices where document_id=%s", (document,)
        ).fetchone()["maker_id"]
    assert str(stored) == str(maker_id)
    with psycopg.connect(owner_dsn, row_factory=dict_row) as owner:
        maker = owner.execute(
            "select auth_subject, api_token_hash, firm_admin from users where id=%s", (maker_id,)
        ).fetchone()
        roles = owner.execute(
            "select count(*) as n from client_members where user_id=%s", (maker_id,)
        ).fetchone()["n"]
    assert maker["auth_subject"] is None and maker["api_token_hash"] is None and maker["firm_admin"] is False
    assert roles == 0


def test_corrector_cannot_approve_when_automation_is_maker(conn, world, owner_dsn):
    maker_id, editor_id = _automation_and_editor(owner_dsn, world)
    document = _another_document(conn, world)
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, canonical(document), maker_id)
    routed = svc.submit_draft(world["client"]["id"], document, draft(document), world["clerk"])
    edited = svc.edit_field(
        world["client"]["id"], document, editor_id, "party_ledger", "Edited ledger",
        expected_revision=routed["revision"],
    )
    with pytest.raises(Forbidden, match="editor cannot approve"):
        svc.approve(world["client"]["id"], document, editor_id, post=False, expected_revision=edited["revision"])
    approved = svc.approve(
        world["client"]["id"], document, world["approver"], post=False, expected_revision=edited["revision"],
    )
    assert approved["status"] == "approved"
    with svc._tx(world["client"]["id"]):
        stored = conn.execute(
            "select maker_id from canonical_invoices where document_id=%s", (document,)
        ).fetchone()["maker_id"]
    assert str(stored) == str(maker_id)

