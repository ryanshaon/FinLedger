"""Phase 1 gates and the agent routes, on a real database with real RLS. Nothing here talks to Tally."""
import pytest
from fastapi.testclient import TestClient

from finledger_control.api import create_app as create_control_app
from finledger_control.service import Conflict, ControlService, Forbidden
from finledger_control.three_way import PHASE1_REASONS, phase1_posting_block
from finledger_platform.api import create_app as create_platform_app
from finledger_platform.config import Settings
from finledger_platform.db import tenant
from finledger_platform.store import LocalStore
from finledger_platform.tenancy import pair_erp_agent, update_policy
from test_service import canonical, draft, exempt_draft, exempt_invoice

GUID = "7a5f42d4-ec1d-4aab-9992-e2e217bcb773"
ERP_ID = f"{GUID}-00000009"
SAME_ORIGIN = {"Origin": "http://testserver"}


def review(svc, world, document, invoice=None, proposed=None):
    cid = world["client"]["id"]
    svc.score(cid, document, invoice or exempt_invoice(document), world["clerk"])
    return svc.submit_draft(cid, document, proposed or exempt_draft(document), world["clerk"])


def ready_job(conn, world, document):
    svc = ControlService(conn)
    routed = review(svc, world, document)
    svc.approve(world["client"]["id"], document, world["approver"], post=True, expected_revision=routed["revision"])
    return svc


@pytest.fixture
def agent(pool, world, posting_on, tmp_path):
    """The platform app with this client's agent token. Pairing resets the switch, so the switch is set again after it."""
    cid = world["client"]["id"]
    settings = Settings(database_url="unused", owner_database_url="unused", app_base_url="http://testserver",
                        signing_secret=b"s", enc_key="", store="local", store_root=str(tmp_path), s3_bucket="",
                        inbound_domain="i.test", inbound_webhook_secret=b"w", inbound_authserv_id="m.test",
                        virus_scanner="eicar", clamd_host="127.0.0.1", clamd_port=3310, max_upload_bytes=1000,
                        rate_per_token_hour=1000, rate_per_ip_hour=1000, render_page_cap=30)
    with pool.connection() as c, tenant(c, cid):
        token = pair_erp_agent(c, cid, "tally", "FinLedger Live Test 1")
        c.execute("update erp_links set company_guid=%s,posting_enabled_at=now(),posting_enabled_by=%s where client_id=%s",
                  (GUID, world["admin"], cid))
    client = TestClient(create_platform_app(settings, pool, LocalStore(str(tmp_path), b"s", "http://testserver")))
    client.headers["Authorization"] = f"Bearer {token}"
    return client


def claim(agent):
    response = agent.post("/agent/jobs/claim", json={"lease_seconds": 300})
    assert response.status_code == 200, response.text
    return response.json()


def result(job, outcome, **extra):
    body = {"claim_token": job["claim_token"], "revision": job["payload"]["revision"],
            "idempotency_key": job["payload"]["idempotency_key"], "outcome": outcome}
    return {**body, **extra}


# ---------- the gate ----------

@pytest.mark.parametrize("change,code", [
    ({"enabled": False}, "posting_not_enabled"),
    ({"human": False}, "not_human_approved"),
    ({"invoice": {"cgst": 1}}, "tax_on_invoice"),
    ({"invoice": {"igst": "0.01"}}, "tax_on_invoice"),
    ({"invoice": {"cess": None}}, "tax_on_invoice"),
    ({"draft": {"gst": {"treatment": "intra", "input_ledgers": [], "tax_breakup": {}}}}, "gst_not_exempt"),
    ({"draft": {"gst": {"treatment": "exempt", "input_ledgers": ["Input CGST"], "tax_breakup": {}}}}, "gst_not_exempt"),
    ({"draft": {"tds": {"applicable": True, "section": None, "ledger": None, "amount": 0}}}, "tds_present"),
    ({"draft": {"party_create_proposal": True}}, "new_vendor_ledger"),
    ({"draft": {"voucher_type": "journal"}}, "not_purchase"),
    ({"invoice": {"round_off": "0.40"}}, "round_off_present"),
])
def test_each_gate_reason_blocks(change, code):
    document = "11111111-1111-1111-1111-111111111111"
    invoice = {**exempt_invoice(document), **change.get("invoice", {})}
    proposed = {**exempt_draft(document), **change.get("draft", {})}
    assert phase1_posting_block(proposed, invoice, posting_enabled=change.get("enabled", True),
                                human_approved=change.get("human", True)) == code
    assert code in PHASE1_REASONS


def test_a_clean_exempt_bill_passes_the_gate():
    document = "11111111-1111-1111-1111-111111111111"
    assert phase1_posting_block(exempt_draft(document), exempt_invoice(document),
                                posting_enabled=True, human_approved=True) is None


def test_a_taxed_bill_is_approved_with_no_job_and_a_reason(conn, world, document, posting_on):
    svc = ControlService(conn)
    routed = review(svc, world, document, canonical(document), draft(document))
    out = svc.approve(world["client"]["id"], document, world["approver"], post=True, expected_revision=routed["revision"])
    assert out["status"] == "approved" and out["posting_blocked"] is True
    assert out["posting_reason"] == PHASE1_REASONS["tax_on_invoice"]
    assert svc.post_job_count(world["client"]["id"], document) == 0


def test_a_draft_marked_exempt_on_a_taxed_invoice_is_blocked(conn, world, document, posting_on):
    svc = ControlService(conn)
    routed = review(svc, world, document, canonical(document), exempt_draft(document, total=11800))
    out = svc.approve(world["client"]["id"], document, world["approver"], post=True, expected_revision=routed["revision"])
    assert out["posting_reason"] == PHASE1_REASONS["tax_on_invoice"]
    assert svc.post_job_count(world["client"]["id"], document) == 0


def test_posting_off_means_no_job(conn, world, document):
    svc = ControlService(conn)
    routed = review(svc, world, document)
    out = svc.approve(world["client"]["id"], document, world["approver"], post=True, expected_revision=routed["revision"])
    assert out["posting_reason"] == PHASE1_REASONS["posting_not_enabled"]
    assert svc.post_job_count(world["client"]["id"], document) == 0


def test_policy_clear_approves_but_never_creates_a_post_job(conn, world, document, posting_on):
    cid = world["client"]["id"]
    with tenant(conn, cid):
        update_policy(conn, cid, {"maker_checker": False, "auto_post_cap": 25000})
    svc = ControlService(conn)
    assert review(svc, world, document)["route"] == "policy_clear"
    assert svc.post_job_count(cid, document) == 0
    # a later replay by an approver can't post it either: a policy clearance is not a human approval
    out = svc.approve(cid, document, world["approver"], post=True, expected_revision=1)
    assert out["posting_reason"] == PHASE1_REASONS["not_human_approved"]
    assert svc.post_job_count(cid, document) == 0


def test_maker_editor_and_payer_cannot_approve(conn, world, document, posting_on):
    svc = ControlService(conn)
    cid = world["client"]["id"]
    review(svc, world, document)
    edited = svc.edit_field(cid, document, world["approver"], "narration", "checked", expected_revision=1)
    for actor in (world["clerk"], world["approver"], world["payer"]):
        with pytest.raises(Forbidden):
            svc.approve(cid, document, actor, post=True, expected_revision=edited["revision"])
    assert svc.post_job_count(cid, document) == 0


# ---------- claim ----------

def test_claim_returns_everything_the_connector_needs(conn, world, document, agent):
    ready_job(conn, world, document)
    job = claim(agent)
    assert job["payload"]["document_id"] == document and job["company"] == {"name": "FinLedger Live Test 1", "guid": GUID}
    assert job["attempt"]["result"] == "pending" and job["approvals"][0]["kind"] == "human"
    assert float(job["invoice"]["cgst"]) == 0 and float(job["invoice"]["total"]) == 10000
    assert agent.post("/agent/jobs/claim", json={}).status_code == 204


def test_a_bad_token_gets_401(agent):
    agent.headers["Authorization"] = "Bearer nope"
    assert agent.post("/agent/jobs/claim", json={}).status_code == 401


def test_switch_off_means_nothing_is_claimed(conn, world, document, agent):
    svc = ready_job(conn, world, document)
    svc.disable_posting(world["client"]["id"], world["admin"])
    assert agent.post("/agent/jobs/claim", json={}).status_code == 204


def test_an_expired_lease_is_never_offered_again(conn, world, document, agent, owner_dsn):
    import psycopg
    ready_job(conn, world, document)
    job = claim(agent)
    with psycopg.connect(owner_dsn, autocommit=True) as owner:
        owner.execute("update jobs set locked_until=now()-interval '1 second' where id=%s", (job["job_id"],))
    assert agent.post("/agent/jobs/claim", json={}).status_code == 204
    detail = ControlService(conn).posting_job(world["client"]["id"], document)
    assert detail["job_state"] == "needs_reconciliation"
    # the agent's late report still counts, because the token was kept
    done = agent.post(f"/agent/jobs/{job['job_id']}/result", json=result(job, "posted", erp_id=ERP_ID))
    assert done.status_code == 200 and done.json()["job_state"] == "done"


def test_repairing_turns_the_switch_off(conn, world, document, agent):
    ready_job(conn, world, document)
    cid = world["client"]["id"]
    with tenant(conn, cid):
        token = pair_erp_agent(conn, cid, "tally", "FinLedger Live Test 2")
        row = conn.execute("select company_guid,posting_enabled_at from erp_links where client_id=%s", (cid,)).fetchone()
    assert row == {"company_guid": None, "posting_enabled_at": None}
    agent.headers["Authorization"] = f"Bearer {token}"
    assert agent.post("/agent/jobs/claim", json={}).status_code == 204


# ---------- result ----------

def test_posted_settles_everything_and_an_identical_repeat_is_the_same(conn, world, document, agent):
    ready_job(conn, world, document)
    job = claim(agent)
    body = result(job, "posted", erp_id=ERP_ID, request_sha256="a" * 64, response_sha256="b" * 64)
    first = agent.post(f"/agent/jobs/{job['job_id']}/result", json=body)
    assert first.status_code == 200
    assert first.json() == {"document_status": "posted", "attempt_result": "posted", "job_state": "done"}
    assert agent.post(f"/agent/jobs/{job['job_id']}/result", json=body).json() == first.json()


def test_posted_without_an_erp_id_is_422(conn, world, document, agent):
    ready_job(conn, world, document)
    job = claim(agent)
    assert agent.post(f"/agent/jobs/{job['job_id']}/result", json=result(job, "posted")).status_code == 422


def test_a_conflicting_repeat_is_refused_and_recorded(conn, world, document, agent):
    ready_job(conn, world, document)
    job = claim(agent)
    agent.post(f"/agent/jobs/{job['job_id']}/result", json=result(job, "posted", erp_id=ERP_ID))
    conflict = agent.post(f"/agent/jobs/{job['job_id']}/result", json=result(job, "failed", error="tally_rejected: x"))
    assert conflict.status_code == 409
    with tenant(conn, world["client"]["id"]):
        assert conn.execute("select count(*) n from workflow_events where action='erp_result_conflict'").fetchone()["n"] == 1
        assert conn.execute("select result from posting_attempts").fetchone()["result"] == "posted"


def test_a_wrong_token_and_another_clients_job_are_refused(conn, world, document, agent):
    ready_job(conn, world, document)
    job = claim(agent)
    wrong = result(job, "posted", erp_id=ERP_ID, claim_token="99999999-9999-4999-8999-999999999999")
    assert agent.post(f"/agent/jobs/{job['job_id']}/result", json=wrong).status_code == 409
    assert agent.post("/agent/jobs/999999/result", json=result(job, "failed", error="x")).status_code == 404


def test_uncertain_parks_the_job_and_blocks_edits(conn, world, document, agent):
    svc = ready_job(conn, world, document)
    job = claim(agent)
    done = agent.post(f"/agent/jobs/{job['job_id']}/result", json=result(job, "uncertain", error="outcome_uncertain: x"))
    assert done.json() == {"document_status": "exception", "attempt_result": "outcome_uncertain",
                           "job_state": "needs_reconciliation"}
    with pytest.raises(Conflict):
        svc.edit_field(world["client"]["id"], document, world["clerk"], "narration", "new", expected_revision=1)
    with pytest.raises(Conflict):
        svc.return_to_review(world["client"]["id"], document, world["approver"], "try again", expected_revision=1)


def test_release_requeues_only_a_running_job_with_its_token(conn, world, document, agent):
    ready_job(conn, world, document)
    job = claim(agent)
    body = {"claim_token": job["claim_token"], "reason": "tally_unavailable"}
    assert agent.post(f"/agent/jobs/{job['job_id']}/release", json=body).json() == {"job_state": "queued"}
    assert agent.post(f"/agent/jobs/{job['job_id']}/release", json=body).status_code == 409
    assert agent.post("/agent/jobs/claim", json={}).status_code == 204  # not before the 60-second wait


# ---------- after the outcome ----------

def test_a_clear_rejection_returns_to_review_as_a_new_revision(conn, world, document, agent):
    svc = ready_job(conn, world, document)
    cid = world["client"]["id"]
    job = claim(agent)
    agent.post(f"/agent/jobs/{job['job_id']}/result", json=result(job, "failed", error="tally_rejected: no ledger"))
    with pytest.raises(Forbidden):
        svc.return_to_review(cid, document, world["clerk"], "fixed the ledger", expected_revision=1)
    assert svc.return_to_review(cid, document, world["approver"], "fixed the ledger", expected_revision=1) == {
        "status": "in_review", "revision": 2}
    with tenant(conn, cid):
        assert conn.execute("select count(*) n from approvals where superseded_at is null").fetchone()["n"] == 0
    svc.approve(cid, document, world["approver"], post=True, expected_revision=2)
    assert svc.post_job_count(cid, document) == 2
    assert claim(agent)["payload"]["idempotency_key"] == f"{document}:r2"


def uncertain_bill(conn, world, document, agent):
    svc = ready_job(conn, world, document)
    job = claim(agent)
    agent.post(f"/agent/jobs/{job['job_id']}/result", json=result(job, "uncertain", error="outcome_uncertain: x"))
    return svc


def test_reconcile_posted_settles_with_the_tally_id(conn, world, document, agent):
    svc = uncertain_bill(conn, world, document, agent)
    cid = world["client"]["id"]
    with pytest.raises(Conflict):
        svc.reconcile_post(cid, document, world["admin"], decision="posted", erp_id="other-1", note="seen in Day Book")
    out = svc.reconcile_post(cid, document, world["admin"], decision="posted", erp_id=ERP_ID, note="seen in Day Book")
    assert out == {"status": "posted", "reconcile_decision": "posted"}
    post = svc.posting_job(cid, document)
    assert (post["attempt_result"], post["job_state"], post["erp_id"]) == ("posted", "done", ERP_ID)
    with pytest.raises(Conflict):
        svc.reconcile_post(cid, document, world["admin"], decision="absent", erp_id=None, note="changed my mind")


def test_reconcile_absent_stays_in_exception_and_cannot_post_again(conn, world, document, agent):
    svc = uncertain_bill(conn, world, document, agent)
    cid = world["client"]["id"]
    out = svc.reconcile_post(cid, document, world["admin"], decision="absent", erp_id=None, note="not in Day Book")
    assert out["status"] == "exception"
    post = svc.posting_job(cid, document)
    assert (post["attempt_result"], post["job_state"]) == ("outcome_uncertain", "dead")
    with pytest.raises(Conflict):
        svc.return_to_review(cid, document, world["approver"], "try again", expected_revision=1)
    assert agent.post("/agent/jobs/claim", json={}).status_code == 204


def test_a_dead_post_job_cannot_be_requeued(conn, world, document, agent, owner_dsn):
    import psycopg
    ready_job(conn, world, document)
    job = claim(agent)
    agent.post(f"/agent/jobs/{job['job_id']}/result", json=result(job, "failed", error="tally_rejected: x"))
    with psycopg.connect(owner_dsn, autocommit=True) as owner:
        assert owner.execute("select requeue_dead_job(%s)", (job["job_id"],)).fetchone()[0] is False


def test_the_shared_claim_refuses_the_post_queue(conn):
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException, match="claim_post_job"):
        conn.execute("select * from claim_job('post', 300)")
    conn.rollback()


# ---------- desk routes ----------

@pytest.fixture
def desk(pool, world, monkeypatch):
    monkeypatch.setenv("FINLEDGER_POSTING_OWNER_USER_ID", str(world["approver"]))
    return TestClient(create_control_app(pool, source_url=lambda path, name: "/files/signed"))


def headers(token):
    return {"Authorization": f"Bearer {token}", **SAME_ORIGIN}


def csrf(desk, world, token):
    return desk.get(f"/api/clients/{world['client']['id']}/posting", headers=headers(token)).json()["csrf_token"]


def test_only_the_named_owner_who_is_a_firm_admin_can_enable(pool, conn, world, desk):
    cid = world["client"]["id"]
    with tenant(conn, cid):
        pair_erp_agent(conn, cid, "tally", "FinLedger Live Test 1")
    token = csrf(desk, world, world["clerk_token"])
    body = {"csrf_token": token, "company_guid": GUID, "confirm_company_name": "FinLedger Live Test 1"}
    assert desk.post(f"/api/clients/{cid}/posting/enable", headers=headers(world["clerk_token"]), json=body).status_code == 403
    # the approver is named as owner here but is not a firm admin, so being named is not enough either
    token = csrf(desk, world, world["approver_token"])
    denied = desk.post(f"/api/clients/{cid}/posting/enable", headers=headers(world["approver_token"]), json={**body, "csrf_token": token})
    assert denied.status_code == 403


def test_owner_unset_refuses_everyone(conn, world, desk, monkeypatch):
    monkeypatch.delenv("FINLEDGER_POSTING_OWNER_USER_ID")
    panel = desk.get(f"/api/clients/{world['client']['id']}/posting", headers=headers(world["approver_token"])).json()
    assert panel["can_change"] is False and panel["enabled"] is False


def test_enable_only_for_a_disposable_company_name(conn, world, document):
    svc = ControlService(conn)
    cid = world["client"]["id"]
    with tenant(conn, cid):
        pair_erp_agent(conn, cid, "tally", "Sharma Client Books")
    with pytest.raises(Conflict, match="FinLedger Live Test"):
        svc.enable_posting(cid, world["admin"], company_guid=GUID, confirm_company_name="Sharma Client Books")
    with tenant(conn, cid):
        pair_erp_agent(conn, cid, "tally", "FinLedger Live Test 3")
    with pytest.raises(Conflict, match="doesn't match"):
        svc.enable_posting(cid, world["admin"], company_guid=GUID, confirm_company_name="FinLedger Live Test 9")
    assert svc.enable_posting(cid, world["admin"], company_guid=GUID,
                              confirm_company_name="FinLedger Live Test 3")["company_guid"] == GUID
    svc.disable_posting(cid, world["admin"])
    with tenant(conn, cid):
        history = conn.execute("select posting_switch_history h from erp_links where client_id=%s", (cid,)).fetchone()["h"]
    assert [entry["action"] for entry in history] == ["pair_reset", "enable", "disable"]


def test_invoice_detail_shows_the_block_and_the_job(conn, world, document, posting_on, desk):
    svc = ControlService(conn)
    cid = world["client"]["id"]
    routed = review(svc, world, document, canonical(document), draft(document))
    page = desk.get(f"/api/clients/{cid}/invoices/{document}", headers=headers(world["approver_token"])).json()
    assert page["posting"] == {"blocked": True, "reason": PHASE1_REASONS["tax_on_invoice"], "code": "tax_on_invoice"}
    assert page["posting_job"] is None and routed["revision"] == 1


def test_a_non_owner_cannot_reconcile(conn, world, document, agent, desk, monkeypatch):
    uncertain_bill(conn, world, document, agent)
    monkeypatch.setenv("FINLEDGER_POSTING_OWNER_USER_ID", "someone-else")
    cid = world["client"]["id"]
    page = desk.get(f"/api/clients/{cid}/invoices/{document}", headers=headers(world["approver_token"])).json()
    assert page["posting_job"]["can_reconcile"] is False and page["posting_job"]["attempt_result"] == "outcome_uncertain"
    denied = desk.post(f"/api/clients/{cid}/invoices/{document}/reconcile", headers=headers(world["approver_token"]),
                       json={"csrf_token": page["csrf_token"], "decision": "absent", "note": "not in Day Book"})
    assert denied.status_code == 403
