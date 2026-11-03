"""Local upload → review on a throwaway cluster. Mock provider only. No network and no hosted DSN."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from psycopg import connect
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from finledger_control.service import ControlService, Forbidden
from finledger_control.web import processing_label, render_inbox
from finledger_platform.api import create_app
from finledger_platform.config import Settings
from finledger_platform.db import make_pool, tenant
from finledger_platform.extract_worker import default_extract
from finledger_platform.extract_worker import run_once as extract_once
from finledger_platform.score_worker import default_score
from finledger_platform.score_worker import run_once as score_once
from finledger_platform.store import LocalStore
from finledger_platform.worker import run_once as ingest_once
from usage.provider import MockProvider
from usage.provider_errors import ProviderTransientError

JOURNEY_PASSWORD = "fl_journey_test_pw"


@pytest.fixture(scope="session")
def journey_dsn(owner_dsn, app_dsn):
    del app_dsn
    with connect(owner_dsn, autocommit=True) as conn:
        conn.execute("drop role if exists fl_journey")
        conn.execute(
            "create role fl_journey login password 'fl_journey_test_pw' in role "
            "finledger_app, finledger_worker_ingest, finledger_worker_extract, finledger_worker_score, finledger_worker_map"
        )
    info = conninfo_to_dict(owner_dsn)
    info.update(user="fl_journey", password=JOURNEY_PASSWORD)
    return make_conninfo(**info)


@pytest.fixture(scope="session")
def journey_pool(journey_dsn):
    pool = make_pool(journey_dsn, min_size=1, max_size=4)
    yield pool
    pool.close()


@pytest.fixture
def owner(owner_dsn):
    with connect(owner_dsn, autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture
def journey(journey_pool):
    with journey_pool.connection() as conn:
        yield conn


@pytest.fixture
def platform(journey_dsn, owner_dsn, journey_pool, tmp_path, monkeypatch):
    monkeypatch.setenv("FINLEDGER_LLM_PROVIDER", "mock")
    monkeypatch.delenv("FINLEDGER_ENV", raising=False)
    monkeypatch.delenv("FINLEDGER_GEMINI_LIVE", raising=False)
    monkeypatch.delenv("FINLEDGER_PER_BILL_TOKEN_CAP", raising=False)
    settings = Settings(
        database_url=journey_dsn, owner_database_url=owner_dsn, app_base_url="http://testserver",
        signing_secret=b"test-signing-secret", enc_key="",
        store="local", store_root=str(tmp_path / "objects"), s3_bucket="",
        inbound_domain="inbound.finledger.test", inbound_webhook_secret=b"test-webhook-secret",
        inbound_authserv_id="mx.finledger.test",
        virus_scanner="eicar", clamd_host="127.0.0.1", clamd_port=3310,
        max_upload_bytes=5 * 1024 * 1024, rate_per_token_hour=1000, rate_per_ip_hour=1000, render_page_cap=30,
    )
    store = LocalStore(settings.store_root, settings.signing_secret, settings.app_base_url)
    client = TestClient(create_app(settings, journey_pool, store))
    return settings, store, client


def pdf(lines: list[str]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    drawing = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in lines:
        drawing.drawString(40, y, line)
        y -= 16
    drawing.showPage()
    drawing.save()
    return buf.getvalue()


def synthetic_invoice(number="INV-2026-0042") -> bytes:
    return pdf([
        "Vendor: Shree Ganesh Metals",
        "GSTIN: 27AAACS1234A1Z2",
        "Date: 2026-09-15",
        f"Inv No: {number}",
        "| Item | HSN | Qty | Rate | Taxable | CGST | SGST |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        "| HR Coil | 7208 | 10 | 10000 | 100000 | 9000 | 9000 |",
        "Total Taxable: 100000",
        "Total: 118000",
    ])


def statement_xlsx() -> bytes:
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Date", "Invoice", "Debit", "Balance"])
    sheet.append(["2026-09-01", "INV-1", 5000, 5000])
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def send(client, token, filename, data, mime):
    response = client.post(f"/i/{token}", files=[("files", (filename, data, mime))])
    assert response.status_code == 201, response.text
    return response.json()["received"][0]["document_id"]


def ingest(conn, store, settings):
    assert ingest_once(conn, store, settings) == "prepared"


def doc(conn, client_id, document_id):
    with tenant(conn, client_id):
        return conn.execute(
            "select status, status_reason, document_class, virus_ok from documents where id = %s",
            (document_id,),
        ).fetchone()


def jobs(conn, client_id, queue):
    with tenant(conn, client_id):
        return conn.execute(
            "select id, state, attempts, last_error, payload from jobs where queue = %s order by id",
            (queue,),
        ).fetchall()


def count(conn, client_id, sql, document_id):
    with tenant(conn, client_id):
        return conn.execute(sql, (document_id,)).fetchone()["n"]


def desk(conn, client_id, document_id):
    service = ControlService(conn)
    for queue in ("new", "needs_review", "approved", "posted", "exception"):
        items = service.inbox(client_id, queue)
        matched = [item for item in items if str(item["document_id"]) == str(document_id)]
        if matched:
            html = render_inbox(items, service.queue_counts(client_id), queue, "Acme Steel")
            return processing_label(matched[0]), matched[0], html
    raise AssertionError(f"{document_id} is not on a desk queue")


def requeue(owner, document_id, queue):
    owner.execute(
        """update jobs set state = 'queued', run_after = now(), claim_token = null, locked_until = null
           where document_id = %s and queue = %s""",
        (document_id, queue),
    )


def make_due(owner):
    owner.execute("update jobs set run_after = now() where state = 'queued'")


def review_draft(document_id, total=118000):
    return {
        "document_id": document_id, "date": "2026-09-15", "voucher_type": "purchase",
        "party_ledger": "Shree Ganesh Metals", "party_create_proposal": False,
        "lines": [{"ledger": "Purchases", "amount": total, "is_debit": True, "cost_centre": None}],
        "gst": {"treatment": "intra", "input_ledgers": [], "tax_breakup": {}},
        "tds": {"applicable": False, "section": None, "ledger": None, "amount": 0},
        "bill_wise": {"ref_type": "new_ref", "ref": "INV-2026-0042", "due_date": None},
        "narration": "Synthetic purchase", "attachment_document_id": document_id,
        "reasons": ["synthetic review draft"], "map_trace_id": "22222222-2222-4222-8222-222222222222",
    }


def spy_complete(monkeypatch):
    calls = []
    original = MockProvider.complete

    def spy(self, purpose, model, client_id, document_id, payload):
        calls.append(purpose)
        return original(self, purpose, model, client_id, document_id, payload)

    monkeypatch.setattr(MockProvider, "complete", spy)
    return calls


def test_happy_path_reaches_human_approval_and_replay_does_not_double_write(
    world, platform, journey, owner, monkeypatch,
):
    settings, store, client = platform
    cid = world["client"]["id"]
    calls = spy_complete(monkeypatch)
    doc_id = send(client, world["client"]["public_token"], "invoice.pdf", synthetic_invoice(), "application/pdf")
    ingest(journey, store, settings)
    label, row, html = desk(journey, cid, doc_id)
    assert row["status"] == "received" and label == "Queued for extraction"
    assert "Success" not in html and "Extracted" not in html and "Scored" not in html

    assert extract_once(journey, store, default_extract(journey)) == "extracted"
    assert calls == ["extract"]
    assert doc(journey, cid, doc_id)["status"] == "extracted"
    assert count(journey, cid, "select count(*) as n from llm_usage_rows where document_id = %s", doc_id) == 1
    assert len(jobs(journey, cid, "score")) == 1

    requeue(owner, doc_id, "extract")
    assert extract_once(journey, store, default_extract(journey)) == "skipped"
    assert calls == ["extract"]
    assert len(jobs(journey, cid, "score")) == 1
    assert count(journey, cid, "select count(*) as n from llm_usage_rows where document_id = %s", doc_id) == 1

    assert score_once(journey, default_score()) == "scored"
    label, row, html = desk(journey, cid, doc_id)
    assert row["status"] == "scored" and label == "Scored" and "Scored" in html
    assert count(journey, cid, "select count(*) as n from risk_assessments where document_id = %s", doc_id) == 1
    revision = count(journey, cid, "select revision as n from canonical_invoices where document_id = %s", doc_id)

    requeue(owner, doc_id, "score")
    assert score_once(journey, default_score()) == "skipped"
    assert count(journey, cid, "select count(*) as n from risk_assessments where document_id = %s", doc_id) == 1
    assert count(journey, cid, "select revision as n from canonical_invoices where document_id = %s", doc_id) == revision

    service = ControlService(journey)
    routed = service.submit_draft(cid, doc_id, review_draft(doc_id), world["clerk"])
    edited = service.edit_field(
        cid, doc_id, world["clerk"], "party_ledger", "Ganesh Metals - AP", expected_revision=routed["revision"],
    )
    with pytest.raises(Forbidden):
        service.approve(cid, doc_id, world["clerk"], post=False, expected_revision=edited["revision"])
    approved = service.approve(
        cid, doc_id, world["approver"], post=False, expected_revision=edited["revision"],
    )
    assert approved["status"] == "approved"
    assert doc(journey, cid, doc_id)["status"] == "approved"
    assert count(journey, cid, "select count(*) as n from correction_events where document_id = %s", doc_id) == 1
    assert count(journey, cid, "select count(*) as n from approvals where document_id = %s", doc_id) == 1
    label, row, _html = desk(journey, cid, doc_id)
    assert row["status"] == "approved" and label == "Approved"

    requeue(owner, doc_id, "extract")
    requeue(owner, doc_id, "score")
    assert extract_once(journey, store, default_extract(journey)) == "skipped"
    assert score_once(journey, default_score()) == "skipped"
    assert calls == ["extract"]
    assert count(journey, cid, "select count(*) as n from llm_usage_rows where document_id = %s", doc_id) == 1
    assert count(journey, cid, "select count(*) as n from risk_assessments where document_id = %s", doc_id) == 1
    assert count(journey, cid, "select count(*) as n from correction_events where document_id = %s", doc_id) == 1
    assert count(journey, cid, "select revision as n from canonical_invoices where document_id = %s", doc_id) == revision


def test_staff_upload_all_consumers_correction_and_second_reviewer(world, platform, journey, journey_pool):
    from finledger_control.api import create_app as control_app
    from finledger_platform.map_worker import default_map, run_once as map_once
    from test_invites import Sessions

    settings, store, _ = platform
    cid = world["client"]["id"]

    def login(role):
        browser = TestClient(control_app(
            journey_pool, local_files=store,
            source_url=lambda path, name: store.signed_url(path, 300, name),
            browser_sessions=Sessions(world[role], world["client"]["firm_id"], False, "aal2"),
        ))
        csrf = browser.get("/api/csrf").json()["csrf_token"]
        assert browser.post("/api/login", headers={"Origin": "http://testserver"},
                            json={"email": f"{role}@synthetic.test", "password": "pw", "csrf_token": csrf}).status_code == 200
        return browser, csrf

    clerk, token = login("clerk")

    def upload_and_process(number):
        raw = synthetic_invoice(number)
        response = clerk.post(f"/api/clients/{cid}/documents", content=raw,
                              headers={"Origin": "http://testserver", "X-CSRF-Token": token,
                                       "X-Filename": "invoice.pdf", "Content-Type": "application/pdf"})
        assert response.status_code == 201
        did = response.json()["received"][0]["document_id"]
        ingest(journey, store, settings)
        assert extract_once(journey, store, default_extract(journey)) == "extracted"
        assert score_once(journey, default_score()) == "scored"
        assert map_once(journey, default_map(journey)) == "mapped"
        detail = clerk.get(f"/api/clients/{cid}/invoices/{did}").json()["invoice"]
        assert detail["status"] == "in_review" and detail["source_url"]
        assert detail["invoice"]["invoice_no"] == number
        with tenant(journey, cid):
            stored = journey.execute("select object_path from documents where id=%s", (did,)).fetchone()
            assert store.get(stored["object_path"]) == raw
        return did, detail

    did, detail = upload_and_process("MVP-001")
    saved = clerk.post(f"/api/clients/{cid}/invoices/{did}/edit",
                       headers={"Origin": "http://testserver"},
                       json={"csrf_token": token, "revision": detail["revision"],
                             "field": "party_ledger", "value": "Ganesh Metals - AP"})
    assert saved.status_code == 200
    denied = clerk.post(f"/api/clients/{cid}/invoices/{did}/approve",
                        headers={"Origin": "http://testserver"},
                        json={"csrf_token": token, "revision": saved.json()["revision"], "post": False})
    assert denied.status_code == 403
    approver, approve_token = login("approver")
    approved = approver.post(f"/api/clients/{cid}/invoices/{did}/approve",
                             headers={"Origin": "http://testserver"},
                             json={"csrf_token": approve_token, "revision": saved.json()["revision"], "post": False})
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert count(journey, cid, "select count(*) as n from approvals where document_id=%s", did) == 1
    assert count(journey, cid, "select count(*) as n from jobs where document_id=%s and queue='post'", did) == 0
    next_id, next_detail = upload_and_process("MVP-002")
    assert next_id != did and next_detail["draft"]["party_ledger"] == "Ganesh Metals - AP"


def test_lost_lease_does_not_settle_a_late_extract_or_score(world, platform, journey, owner):
    settings, store, client = platform
    cid = world["client"]["id"]
    doc_id = send(client, world["client"]["public_token"], "invoice.pdf", synthetic_invoice(), "application/pdf")
    ingest(journey, store, settings)
    extract = default_extract(journey)

    def late_extract(**kwargs):
        owner.execute("update jobs set claim_token = gen_random_uuid() where document_id = %s and queue = 'extract'", (doc_id,))
        return extract(**kwargs)

    assert extract_once(journey, store, late_extract) == "lease_lost"
    assert doc(journey, cid, doc_id)["status"] == "received"
    assert jobs(journey, cid, "score") == []
    assert count(journey, cid, "select count(*) as n from canonical_invoices where document_id = %s", doc_id) == 0

    owner.execute(
        """update jobs set state = 'queued', run_after = now(), claim_token = null, locked_until = null,
                  attempts = 0 where document_id = %s and queue = 'extract'""",
        (doc_id,),
    )
    assert extract_once(journey, store, default_extract(journey)) == "extracted"
    score = default_score()

    def late_score(conn, **kwargs):
        owner.execute("update jobs set claim_token = gen_random_uuid() where document_id = %s and queue = 'score'", (doc_id,))
        return score(conn, **kwargs)

    assert score_once(journey, late_score) == "lease_lost"
    assert doc(journey, cid, doc_id)["status"] == "extracted"
    assert count(journey, cid, "select count(*) as n from risk_assessments where document_id = %s", doc_id) == 0
    assert count(journey, cid, "select count(*) as n from canonical_invoices where document_id = %s", doc_id) == 0


def test_schema_invalid_invoice_is_rejected_once(world, platform, journey, owner):
    settings, store, client = platform
    cid = world["client"]["id"]
    doc_id = send(
        client, world["client"]["public_token"], "blank.pdf",
        pdf(["This document has no invoice fields and is not a statement."]), "application/pdf",
    )
    ingest(journey, store, settings)
    assert extract_once(journey, store, default_extract(journey)) == "extracted"
    assert score_once(journey, default_score()) == "exception"
    assert doc(journey, cid, doc_id) == {
        "status": "exception", "status_reason": "canonical_invoice_rejected",
        "document_class": "invoice", "virus_ok": True,
    }
    job = jobs(journey, cid, "score")[0]
    assert job["state"] == "done" and job["last_error"] is None
    assert count(journey, cid, "select count(*) as n from canonical_invoices where document_id = %s", doc_id) == 0
    assert score_once(journey, default_score()) is None

    requeue(owner, doc_id, "score")
    assert score_once(journey, default_score()) == "skipped"
    assert doc(journey, cid, doc_id)["status_reason"] == "canonical_invoice_rejected"
    assert count(journey, cid, "select count(*) as n from canonical_invoices where document_id = %s", doc_id) == 0


def test_scanner_failure_stays_unscanned_and_can_retry(world, platform, journey, owner):
    settings, store, client = platform
    cid = world["client"]["id"]
    object.__setattr__(settings, "virus_scanner", "clamd")
    object.__setattr__(settings, "clamd_port", 1)
    doc_id = send(client, world["client"]["public_token"], "invoice.pdf", synthetic_invoice(), "application/pdf")
    assert ingest_once(journey, store, settings) == "queued"
    assert doc(journey, cid, doc_id)["virus_ok"] is None
    assert doc(journey, cid, doc_id)["status"] == "received"
    assert jobs(journey, cid, "extract") == []
    job = jobs(journey, cid, "ingest")[0]
    assert job["state"] == "queued" and job["last_error"] == "virus scanner unavailable"
    label, _row, html = desk(journey, cid, doc_id)
    assert label == "Queued for extraction" and "Success" not in html

    object.__setattr__(settings, "virus_scanner", "eicar")
    make_due(owner)
    assert ingest_once(journey, store, settings) == "prepared"
    assert doc(journey, cid, doc_id)["virus_ok"] is True
    assert len(jobs(journey, cid, "extract")) == 1


def test_budget_exhaustion_is_a_permanent_exception(world, platform, journey, monkeypatch):
    settings, store, client = platform
    cid = world["client"]["id"]
    generations = []
    original = MockProvider._generate

    def generate(self, purpose, document_id, payload):
        generations.append(purpose)
        return original(self, purpose, document_id, payload)

    monkeypatch.setattr(MockProvider, "_generate", generate)
    monkeypatch.setenv("FINLEDGER_PER_BILL_TOKEN_CAP", "1")
    doc_id = send(client, world["client"]["public_token"], "invoice.pdf", synthetic_invoice(), "application/pdf")
    ingest(journey, store, settings)
    assert extract_once(journey, store, default_extract(journey)) == "exception"
    assert generations == []
    assert doc(journey, cid, doc_id)["status_reason"] == "ai_budget_exceeded"
    assert jobs(journey, cid, "extract")[0]["state"] == "done"
    assert jobs(journey, cid, "score") == []
    assert extract_once(journey, store, default_extract(journey)) is None
    label, row, html = desk(journey, cid, doc_id)
    assert row["status"] == "exception" and label == "AI budget exhausted" and "AI budget exhausted" in html


def test_provider_timeouts_retry_then_go_dead_after_five(world, platform, journey, owner, monkeypatch):
    settings, store, client = platform
    cid = world["client"]["id"]
    calls = []

    def timeout(self, purpose, model, client_id, document_id, payload):
        del self, purpose, model, client_id, document_id, payload
        calls.append("timeout")
        raise ProviderTransientError()

    monkeypatch.setattr(MockProvider, "complete", timeout)
    doc_id = send(client, world["client"]["public_token"], "invoice.pdf", synthetic_invoice(), "application/pdf")
    ingest(journey, store, settings)
    outcomes = []
    for _ in range(5):
        outcomes.append(extract_once(journey, store, default_extract(journey)))
        make_due(owner)
    assert outcomes == ["queued", "queued", "queued", "queued", "dead"]
    assert calls == ["timeout"] * 5
    job = jobs(journey, cid, "extract")[0]
    assert job["state"] == "dead" and job["attempts"] == 5 and job["last_error"] == "provider_transient"
    assert doc(journey, cid, doc_id)["status"] == "received"
    assert jobs(journey, cid, "score") == []
    label, row, _html = desk(journey, cid, doc_id)
    assert row["status"] == "received" and label == "Extraction failed"
    assert extract_once(journey, store, default_extract(journey)) is None


def test_statement_and_foreign_object_key_never_call_the_model(world, platform, journey, owner, monkeypatch):
    settings, store, client = platform
    cid = world["client"]["id"]
    calls = spy_complete(monkeypatch)
    statement_id = send(
        client, world["client"]["public_token"], "statement.xlsx", statement_xlsx(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    invoice_id = send(client, world["client"]["public_token"], "invoice.pdf", synthetic_invoice(), "application/pdf")
    assert ingest_once(journey, store, settings) == "prepared"
    assert ingest_once(journey, store, settings) == "prepared"
    assert doc(journey, cid, statement_id)["document_class"] == "statement"

    assert extract_once(journey, store, default_extract(journey)) == "exception"
    assert calls == []
    assert doc(journey, cid, statement_id)["status_reason"] == "statement_not_extracted"
    label, _row, html = desk(journey, cid, statement_id)
    assert label == "Statement excluded" and "Statement excluded" in html
    assert jobs(journey, cid, "score") == []

    owner.execute(
        "update jobs set payload = jsonb_set(payload, %s, %s) where document_id = %s and queue = 'extract'",
        (["raw_markdown_path"], Jsonb("00000000-0000-0000-0000-000000000099/2026/10/markdown.md"), invoice_id),
    )
    assert extract_once(journey, store, default_extract(journey)) == "exception"
    assert calls == []
    assert doc(journey, cid, invoice_id)["status_reason"] == "object_key_rejected"
    assert jobs(journey, cid, "score") == []
