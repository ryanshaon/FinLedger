"""Review, correction, rejection and processing-state regressions for the release desk."""

import hashlib
import uuid
import psycopg

from fastapi.testclient import TestClient

from finledger_control.api import create_app
from finledger_control.service import ControlService
from finledger_platform.db import tenant
from finledger_platform.tenancy import create_client
from test_service import canonical, draft

SAME_ORIGIN = {"Origin": "http://testserver"}


def _ready(conn, world, document):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, canonical(document), world["clerk"])
    svc.submit_draft(world["client"]["id"], document, draft(document), world["clerk"])
    return svc


def _client(pool, token):
    app = create_app(pool, source_url=lambda path, name: "/files/signed")
    return TestClient(app), {"Authorization": f"Bearer {token}"}


def _csrf(client, headers, world, document):
    page = client.get(f"/clients/{world['client']['id']}/review/{document}", headers=headers)
    assert page.status_code == 200
    return page.cookies["finledger_csrf"]


def _count(conn, client_id, sql, args):
    with tenant(conn, client_id):
        return conn.execute(sql, args).fetchone()["n"]


def test_invalid_review_form_saves_nothing_and_hides_the_submission(pool, conn, world, document):
    _ready(conn, world, document)
    client, headers = _client(pool, world["approver_token"])
    token = _csrf(client, headers, world, document)
    response = client.post(
        f"/clients/{world['client']['id']}/review/{document}/approve",
        headers={**headers, **SAME_ORIGIN},
        data={"post": "false", "csrf_token": token, "note": "FINLEDGER_FORM_CANARY"},
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert "Nothing was saved" in response.text
    assert "FINLEDGER_FORM_CANARY" not in response.text
    assert "Success" not in response.text
    assert _count(conn, world["client"]["id"],
                  "select count(*) as n from approvals where document_id=%s", (document,)) == 0


def test_stale_revision_is_rejected(pool, conn, world, document):
    _ready(conn, world, document)
    client, headers = _client(pool, world["approver_token"])
    token = _csrf(client, headers, world, document)
    response = client.post(
        f"/clients/{world['client']['id']}/review/{document}/approve",
        headers={**headers, **SAME_ORIGIN},
        data={"revision": 99, "post": "false", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert _count(conn, world["client"]["id"],
                  "select count(*) as n from approvals where document_id=%s", (document,)) == 0


def test_duplicate_approval_does_not_double_post(pool, conn, world, document):
    _ready(conn, world, document)
    client, headers = _client(pool, world["approver_token"])
    token = _csrf(client, headers, world, document)
    url = f"/clients/{world['client']['id']}/review/{document}/approve"
    data = {"revision": 1, "post": "false", "csrf_token": token}
    first = client.post(url, headers={**headers, **SAME_ORIGIN}, data=data, follow_redirects=False)
    second = client.post(url, headers={**headers, **SAME_ORIGIN}, data=data, follow_redirects=False)
    assert first.status_code == 303 and second.status_code == 303
    assert _count(conn, world["client"]["id"],
                  "select count(*) as n from approvals where document_id=%s", (document,)) == 1
    assert _count(conn, world["client"]["id"],
                  "select count(*) as n from jobs where document_id=%s and queue='post'", (document,)) == 0


def test_edit_and_reject_forms_keep_maker_checker(pool, conn, world, document):
    _ready(conn, world, document)
    clerk, clerk_headers = _client(pool, world["clerk_token"])
    token = _csrf(clerk, clerk_headers, world, document)
    edited = clerk.post(
        f"/clients/{world['client']['id']}/review/{document}/edit",
        headers={**clerk_headers, **SAME_ORIGIN},
        data={"revision": 1, "field": "party_ledger", "value": "Ganesh Metals - AP", "csrf_token": token},
        follow_redirects=False,
    )
    assert edited.status_code == 303
    assert _count(conn, world["client"]["id"],
                  "select count(*) as n from correction_events where document_id=%s", (document,)) == 1

    approver, approver_headers = _client(pool, world["approver_token"])
    review = approver.get(f"/clients/{world['client']['id']}/review/{document}", headers=approver_headers)
    assert "Ganesh Metals - AP" in review.text
    self_approval = clerk.post(
        f"/clients/{world['client']['id']}/review/{document}/approve",
        headers={**clerk_headers, **SAME_ORIGIN},
        data={"revision": 2, "post": "true", "csrf_token": token},
        follow_redirects=False,
    )
    assert self_approval.status_code == 403
    token = review.cookies["finledger_csrf"]
    rejected = approver.post(
        f"/clients/{world['client']['id']}/review/{document}/reject",
        headers={**approver_headers, **SAME_ORIGIN},
        data={"revision": 2, "resubmit": "true", "note": "wrong entity", "csrf_token": token},
        follow_redirects=False,
    )
    assert rejected.status_code == 303
    assert rejected.headers["location"].endswith("/review?queue=exception")
    queue = approver.get(f"/clients/{world['client']['id']}/review?queue=exception", headers=approver_headers)
    assert queue.status_code == 200
    assert "Resubmit requested" in queue.text and "INV-42" in queue.text
    assert "Success" not in queue.text
    assert _count(conn, world["client"]["id"],
                  "select count(*) as n from jobs where document_id=%s and queue='post'", (document,)) == 0


def test_inbox_shows_queued_failed_budget_and_statement_states(pool, conn, world, owner_dsn):
    client_id = world["client"]["id"]
    jobs = []
    specs = [
        ("received", "invoice", None, "queued", "queued-bill.pdf"),
        ("received", "invoice", None, "dead", "failed-bill.pdf"),
        ("exception", "invoice", "ai_budget_exceeded", None, "capped-bill.pdf"),
        ("exception", "statement", "statement_not_extracted", None, "bank-statement.csv"),
        ("quarantined", "invoice", "virus: EICAR", None, "blocked.pdf"),
    ]
    with tenant(conn, client_id):
        for status, document_class, reason, extract_state, filename in specs:
            document_id = uuid.uuid4()
            conn.execute(
                """insert into documents(id,client_id,channel,object_path,original_filename,content_hash,mime,
                       size_bytes,status,status_reason,document_class,virus_ok,source_meta)
                   values (%s,%s,'portal',%s,%s,%s,'application/pdf',10,%s,%s,%s,false,'{}')""",
                (document_id, client_id, f"{client_id}/2026/10/{document_id}/raw.pdf", filename,
                 hashlib.sha256(filename.encode()).hexdigest(), status, reason, document_class),
            )
            if extract_state:
                jobs.append((client_id, document_id, extract_state))
    with psycopg.connect(owner_dsn, autocommit=True) as owner:
        for job in jobs:
            owner.execute("insert into jobs(client_id,document_id,queue,state,payload) values (%s,%s,'extract',%s,'{}'::jsonb)", job)
    client, headers = _client(pool, world["approver_token"])
    new = client.get(f"/clients/{client_id}/review?queue=new", headers=headers)
    assert new.status_code == 200
    assert "queued-bill.pdf" in new.text and "Queued for extraction" in new.text
    assert "failed-bill.pdf" in new.text and "Extraction failed" in new.text
    assert "Success" not in new.text and "Processed" not in new.text
    exception = client.get(f"/clients/{client_id}/review?queue=exception", headers=headers)
    assert "AI budget exhausted" in exception.text
    assert "Statement excluded" in exception.text
    assert "Quarantined" in exception.text and "blocked.pdf" in exception.text
    assert "capped-bill.pdf" in exception.text and "bank-statement.csv" in exception.text
    assert 'Exception<span class="count">3</span>' in exception.text


def test_approver_cannot_open_another_clients_queue(pool, conn, world):
    with conn.transaction():
        conn.execute("select set_config('app.firm_id', %s, true)", (str(world["client"]["firm_id"]),))
        other = create_client(conn, "Other Co", inbound_slug="other-co")
    client, headers = _client(pool, world["approver_token"])
    response = client.get(f"/clients/{other['id']}/review", headers=headers)
    assert response.status_code == 403
    assert "Other Co" not in response.text
