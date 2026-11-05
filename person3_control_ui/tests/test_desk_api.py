"""The desk JSON API calls ControlService. It does not score or approve on its own."""
import hashlib

import psycopg
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from finledger_control.api import create_app
from finledger_control.service import ControlService
from finledger_platform.browser_sessions import StaffSession
from finledger_platform.db import tenant
from finledger_platform.staff_invites import StaffInvitations
from test_invites import FakeAdmin, Sessions, admin_sessions
from test_service import canonical, draft, exempt_draft, exempt_invoice

SAME_ORIGIN = {"Origin": "http://testserver"}


def test_desk_queue_save_and_send_back(pool, conn, world, document):
    service = ControlService(conn)
    service.score(world["client"]["id"], document,
                  canonical(document, confidences={"invoice_no": .2, "date": .2, "gstin": .99, "total": .99}),
                  world["clerk"])
    service.submit_draft(world["client"]["id"], document, draft(document), world["clerk"])
    client_id = world["client"]["id"]
    clerk = TestClient(create_app(pool, source_url=lambda path, name: "/files/signed"))
    approver = TestClient(create_app(pool, source_url=lambda path, name: "/files/signed"))
    clerk_headers = {"Authorization": f"Bearer {world['clerk_token']}", **SAME_ORIGIN}
    approver_headers = {"Authorization": f"Bearer {world['approver_token']}", **SAME_ORIGIN}

    queue = approver.get(f"/api/clients/{client_id}/queue?queue=needs_review", headers=approver_headers)
    assert queue.status_code == 200
    body = queue.json()
    assert body["client"]["name"] == "Acme Steel"
    assert body["items"][0]["invoice_no"] == "INV-42"
    assert body["items"][0]["state_label"] == "Needs review"
    assert body["items"][0]["vendor_name"] == "Shree Ganesh Metals"
    empty = approver.get(f"/api/clients/{client_id}/queue?queue=posted", headers=approver_headers)
    assert empty.status_code == 200 and empty.json()["items"] == []

    invoice = clerk.get(f"/api/clients/{client_id}/invoices/{document}", headers=clerk_headers)
    assert invoice.status_code == 200
    token = invoice.json()["csrf_token"]
    mapped = invoice.json()["invoice"]["draft"]
    assert mapped["party_ledger"] == "Shree Ganesh Metals"
    assert mapped["lines"][0]["ledger"] == "Purchase - Steel"
    assert mapped["lines"][0]["amount"] in (11800, 11800.0, "11800", "11800.0", "11800.00")
    saved = clerk.post(
        f"/api/clients/{client_id}/invoices/{document}/edit",
        headers=clerk_headers,
        json={"csrf_token": token, "revision": 1, "field": "party_ledger", "value": "Ganesh Metals"},
    )
    assert saved.status_code == 200 and saved.json()["revision"] == 2

    review = approver.get(f"/api/clients/{client_id}/invoices/{document}", headers=approver_headers)
    token = review.json()["csrf_token"]
    rejected = approver.post(
        f"/api/clients/{client_id}/invoices/{document}/approve",
        headers={**approver_headers, "Origin": "https://evil.test"},
        json={"csrf_token": token, "revision": 2, "post": True},
    )
    assert rejected.status_code == 403
    sent = approver.post(
        f"/api/clients/{client_id}/invoices/{document}/reject",
        headers=approver_headers,
        json={"csrf_token": token, "revision": 2, "resubmit": True},
    )
    assert sent.status_code == 200 and sent.json()["status"] == "resubmit"
    again = approver.get(f"/api/clients/{client_id}/queue?queue=exception", headers=approver_headers)
    labels = [item["state_label"] for item in again.json()["items"]]
    assert "Resubmit requested" in labels
    with tenant(conn, client_id):
        assert conn.execute("select count(*) as n from jobs where document_id=%s and queue='post'", (document,)).fetchone()["n"] == 0
        assert conn.execute("select count(*) as n from correction_events where document_id=%s", (document,)).fetchone()["n"] == 1


def test_desk_approve_and_post_uses_the_service(pool, conn, world, document, posting_on):
    service = ControlService(conn)
    service.score(world["client"]["id"], document,
                  exempt_invoice(document, confidences={"invoice_no": .2, "date": .2, "gstin": .99, "total": .99}),
                  world["clerk"])
    routed = service.submit_draft(world["client"]["id"], document, exempt_draft(document), world["clerk"])
    client_id = world["client"]["id"]
    approver = TestClient(create_app(pool, source_url=lambda path, name: "/files/signed"))
    headers = {"Authorization": f"Bearer {world['approver_token']}", **SAME_ORIGIN}
    token = approver.get(f"/api/clients/{client_id}/invoices/{document}", headers=headers).json()["csrf_token"]
    clerk = TestClient(create_app(pool, source_url=lambda path, name: "/files/signed"))
    clerk_headers = {"Authorization": f"Bearer {world['clerk_token']}", **SAME_ORIGIN}
    clerk_token = clerk.get(f"/api/clients/{client_id}/invoices/{document}", headers=clerk_headers).json()["csrf_token"]
    blocked = clerk.post(
        f"/api/clients/{client_id}/invoices/{document}/approve",
        headers=clerk_headers,
        json={"csrf_token": clerk_token, "revision": routed["revision"], "post": True},
    )
    assert blocked.status_code == 403
    approved = approver.post(
        f"/api/clients/{client_id}/invoices/{document}/approve",
        headers=headers,
        json={"csrf_token": token, "revision": routed["revision"], "post": True},
    )
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert service.post_job_count(client_id, document) == 1


def _signed_in(pool, sessions):
    if not hasattr(sessions, "sign_out"):
        sessions.sign_out = lambda conn, cookie: None
    client = TestClient(create_app(pool, browser_sessions=sessions, staff_invitations=StaffInvitations(FakeAdmin())))
    token = client.get("/api/csrf").json()["csrf_token"]
    response = client.post("/api/login", headers=SAME_ORIGIN,
                           json={"email": "person@sharma.test", "password": "pw", "csrf_token": token})
    assert response.status_code == 200
    return client


def test_desk_login_lists_clients_and_hides_staff_from_non_admins(pool, world):
    approver = _signed_in(pool, Sessions(world["approver"], world["client"]["firm_id"], False, "aal2"))
    session = approver.get("/api/session")
    assert session.status_code == 200
    assert session.json()["firm_admin"] is False and session.json()["aal"] == "aal2"
    clients = approver.get("/api/clients")
    assert clients.status_code == 200
    assert clients.json()["clients"] == [{
        "id": str(world["client"]["id"]), "name": "Acme Steel", "gstin": "27AAPFU0939F1ZV",
    }]
    assert approver.get("/api/staff").status_code == 403
    logged_out = approver.post("/api/logout", headers=SAME_ORIGIN, json={"csrf_token": session.json()["csrf_token"]})
    assert logged_out.status_code == 200
    assert approver.get("/api/session").status_code == 401

    unsigned = TestClient(create_app(pool, browser_sessions=Sessions(world["approver"], world["client"]["firm_id"], False, "aal2")))
    token = unsigned.get("/api/csrf").json()["csrf_token"]
    forged = unsigned.post("/api/login", headers={"Origin": "https://evil.test"},
                           json={"email": "person@sharma.test", "password": "pw", "csrf_token": token})
    assert forged.status_code == 403
    snow = unsigned.post("/api/login", headers=SAME_ORIGIN,
                         json={"email": "person@sharma.test", "password": "pw", "csrf_token": "\u2603"})
    assert snow.status_code == 403


def test_password_session_without_totp_opens_the_desk(pool, world):
    sessions = Sessions(world["approver"], world["client"]["firm_id"], False, "aal1")
    sessions.who = StaffSession(world["approver"], world["client"]["firm_id"], False, "aal1", mfa_required=False)
    client = _signed_in(pool, sessions)
    session = client.get("/api/session")
    assert session.status_code == 200
    assert session.json()["aal"] == "aal1"
    assert session.json()["mfa_required"] is False
    assert client.get("/api/clients").status_code == 200


def test_desk_aal1_cannot_list_clients(pool, world):
    client = _signed_in(pool, admin_sessions(world, "aal1"))
    session = client.get("/api/session")
    assert session.json()["aal"] == "aal1"
    blocked = client.get("/api/clients")
    assert blocked.status_code == 403 and blocked.json()["detail"]["mfa_required"] is True
    staff = client.get("/api/staff")
    assert staff.status_code == 403 and staff.json()["detail"]["mfa_required"] is True


def test_desk_admin_invite_uses_the_staff_service(pool, owner_dsn, world):
    fake = FakeAdmin()
    client = TestClient(create_app(pool, browser_sessions=admin_sessions(world), staff_invitations=StaffInvitations(fake)))
    token = client.get("/api/csrf").json()["csrf_token"]
    assert client.post("/api/login", headers=SAME_ORIGIN,
                       json={"email": "admin@sharma.test", "password": "pw", "csrf_token": token}).status_code == 200
    page = client.get("/api/staff")
    assert page.status_code == 200
    assert any(row["name"] == "Acme Steel" for row in page.json()["clients"])
    sent = client.post("/api/staff/invite", headers=SAME_ORIGIN, json={
        "csrf_token": page.json()["csrf_token"],
        "email": "New.Clerk@Sharma.test",
        "name": "Neha",
        "firm_admin": False,
        "memberships": [{"client_id": str(world["client"]["id"]), "role": "ap_clerk"}],
    })
    assert sent.status_code == 201
    assert sent.json()["notice"] == "Invitation sent to new.clerk@sharma.test."
    assert fake.invited == ["new.clerk@sharma.test"]
    with psycopg.connect(owner_dsn) as db:
        row = db.execute("select firm_admin from users where email = %s", ("new.clerk@sharma.test",)).fetchone()
    assert row[0] is False


def test_reconciliation_matches_invoice_and_amount_and_leaves_the_rest_unmatched(pool, conn, world, document):
    service = ControlService(conn)
    client_id = world["client"]["id"]
    service.score(client_id, document,
                  canonical(document, confidences={"invoice_no": .2, "date": .2, "gstin": .99, "total": .99}),
                  world["clerk"])
    service.submit_draft(client_id, document, draft(document), world["clerk"])
    statement_id = "22222222-2222-2222-2222-222222222222"
    bank_lines = [
        {"date": "2026-10-06", "narration": "NEFT INV-42", "reference": "", "debit": "11800.00", "credit": None},
        {"date": "2026-10-05", "narration": "NEFT salary", "reference": "", "debit": None, "credit": "250000.00"},
    ]
    with tenant(conn, client_id):
        conn.execute(
            """insert into documents(id, client_id, channel, object_path, original_filename, content_hash,
                   mime, size_bytes, status, virus_ok, document_class, source_meta)
               values (%s,%s,'portal',%s,'october-bank-statement.xlsx',%s,
                   'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 100,
                   'exception', true, 'statement', %s)""",
            (statement_id, client_id, f"{client_id}/2026/10/{statement_id}/raw.xlsx",
             hashlib.sha256(b"statement").hexdigest(), Jsonb({"bank_lines": bank_lines, "uploaded_by": "october-set"})),
        )
    approver = TestClient(create_app(pool, source_url=lambda path, name: "/files/signed"))
    headers = {"Authorization": f"Bearer {world['approver_token']}", **SAME_ORIGIN}
    response = approver.get(f"/api/clients/{client_id}/reconciliation", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["client"]["name"] == "Acme Steel"
    by_narration = {line["narration"]: line for line in body["lines"]}
    assert by_narration["NEFT INV-42"]["status"] == "matched"
    assert by_narration["NEFT INV-42"]["invoice_no"] == "INV-42"
    assert by_narration["NEFT INV-42"]["document_id"] == document
    assert by_narration["NEFT salary"]["status"] == "unmatched"
    assert by_narration["NEFT salary"]["document_id"] is None
    bills = {bill["invoice_no"]: bill["status"] for bill in body["bills"]}
    assert bills["INV-42"] == "matched"
