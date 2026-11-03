"""Staff intake keeps the existing storage, scanner and tenant boundaries."""
import hashlib

import pytest
from fastapi.testclient import TestClient

from finledger_control.api import create_app
from finledger_control.service import ControlService
from finledger_platform.db import tenant
from finledger_platform.store import LocalStore
from test_desk_api import SAME_ORIGIN
from test_invites import Sessions
from test_service import canonical, draft

PDF = b"%PDF-1.4\nSynthetic invoice\n%%EOF"


def staff_client(pool, world, tmp_path, role="clerk", **options):
    store = LocalStore(str(tmp_path / "objects"), b"synthetic-signing-secret", "http://testserver")
    sessions = Sessions(world[role], world["client"]["firm_id"], role == "admin", "aal2")
    client = TestClient(create_app(pool, local_files=store, browser_sessions=sessions, **options))
    csrf = client.get("/api/csrf").json()["csrf_token"]
    assert client.post("/api/login", headers=SAME_ORIGIN,
                       json={"email": "staff@example.test", "password": "pw", "csrf_token": csrf}).status_code == 200
    return client, store, {**SAME_ORIGIN, "X-CSRF-Token": csrf, "X-Filename": "invoice.pdf",
                           "Content-Type": "application/pdf"}


@pytest.mark.parametrize("role", ["clerk", "admin"])
def test_clerk_upload_preserves_original_and_enqueues_ingest(pool, conn, world, tmp_path, role):
    client, store, headers = staff_client(pool, world, tmp_path, role)
    cid = world["client"]["id"]
    response = client.post(f"/api/clients/{cid}/documents", headers=headers, content=PDF)
    assert response.status_code == 201
    body = response.json()
    assert not body["rejected"] and len(body["received"]) == 1
    did = body["received"][0]["document_id"]
    with tenant(conn, cid):
        row = conn.execute("select * from documents where id=%s", (did,)).fetchone()
        assert row["channel"] == "portal" and row["status"] == "received" and row["virus_ok"] is None
        assert row["source_meta"]["uploaded_by"] == str(world[role])
        assert row["content_hash"] == hashlib.sha256(PDF).hexdigest()
        assert store.get(row["object_path"]) == PDF
        assert conn.execute("select queue from jobs where document_id=%s", (did,)).fetchone()["queue"] == "ingest"
    queue = client.get(f"/api/clients/{cid}/queue?queue=new").json()
    assert ("ap_clerk" if role == "clerk" else "firm_admin") in queue["roles"] and queue["csrf_token"]
    assert queue["max_upload_bytes"] == 25 * 1024 * 1024


@pytest.mark.parametrize("role", ["approver", "payer"])
def test_upload_requires_clerk_or_admin(pool, conn, world, tmp_path, role):
    client, _, headers = staff_client(pool, world, tmp_path, role)
    assert client.post(f"/api/clients/{world['client']['id']}/documents", headers=headers, content=PDF).status_code == 403
    with tenant(conn, world["client"]["id"]):
        assert conn.execute("select count(*) as n from documents").fetchone()["n"] == 0


def test_upload_rejects_csrf_and_foreign_company(pool, world, tmp_path):
    client, _, headers = staff_client(pool, world, tmp_path)
    cid = world["client"]["id"]
    assert client.post(f"/api/clients/{cid}/documents", headers={**headers, "X-CSRF-Token": "wrong"}, content=PDF).status_code == 403
    assert client.post(f"/api/clients/{cid}/documents", headers={**headers, "Origin": "https://evil.test"}, content=PDF).status_code == 403
    assert client.post("/api/clients/99999999-9999-4999-8999-999999999999/documents", headers=headers, content=PDF).status_code == 403


def test_upload_has_a_streaming_size_limit_and_refuses_non_invoice_types(pool, conn, world, tmp_path):
    client, _, headers = staff_client(pool, world, tmp_path, max_upload_bytes=64)
    cid = world["client"]["id"]
    assert client.post(f"/api/clients/{cid}/documents", headers=headers, content=PDF + b"x" * 64).status_code == 413
    assert client.post(f"/api/clients/{cid}/documents", headers=headers,
                       content=iter([PDF, b"x" * 64])).status_code == 413
    assert client.post(f"/api/clients/{cid}/documents", headers=headers, content=b"not an invoice").status_code == 422
    with tenant(conn, cid):
        assert conn.execute("select count(*) as n from documents").fetchone()["n"] == 0


def test_original_can_open_before_mapping_without_enabling_edits(pool, world, document):
    client = TestClient(create_app(pool, source_url=lambda path, name: "/files/signed"))
    headers = {"Authorization": f"Bearer {world['clerk_token']}"}
    response = client.get(f"/api/clients/{world['client']['id']}/invoices/{document}", headers=headers)
    assert response.status_code == 200
    detail = response.json()["invoice"]
    assert detail["source_url"] == "/files/signed"
    assert detail["invoice"] == {} and detail["draft"] == {} and detail["revision"] == 0
    assert detail["can_edit"] is False and detail["can_approve"] is False


def test_legacy_review_does_not_offer_actions_before_a_draft_exists(pool, world, document, tmp_path, monkeypatch):
    monkeypatch.setenv("FINLEDGER_DESK_DIST", str(tmp_path / "not-built"))
    client = TestClient(create_app(pool))
    response = client.get(f"/clients/{world['client']['id']}/review/{document}",
                          headers={"Authorization": f"Bearer {world['clerk_token']}"})
    assert response.status_code == 404


def test_unscanned_original_is_not_signed(pool, conn, world, document):
    cid = world["client"]["id"]
    with tenant(conn, cid):
        conn.execute("update documents set status='quarantined',virus_ok=false where id=%s", (document,))
    client = TestClient(create_app(pool, source_url=lambda path, name: pytest.fail("unsafe original was signed")))
    response = client.get(f"/api/clients/{cid}/invoices/{document}",
                          headers={"Authorization": f"Bearer {world['clerk_token']}"})
    assert response.status_code == 200 and response.json()["invoice"]["source_url"] == ""


def test_approver_who_edits_gets_an_explicit_second_reviewer_hint(pool, conn, world, document):
    cid = world["client"]["id"]
    service = ControlService(conn)
    service.score(cid, document, canonical(document), world["clerk"])
    service.submit_draft(cid, document, draft(document), world["clerk"])
    client = TestClient(create_app(pool))
    headers = {"Authorization": f"Bearer {world['approver_token']}"}
    url = f"/api/clients/{cid}/invoices/{document}"
    assert client.get(url, headers=headers).json()["invoice"]["can_approve"] is True
    service.edit_field(cid, document, world["approver"], "party_ledger", "Revised creditors", expected_revision=1)
    detail = client.get(url, headers=headers).json()["invoice"]
    assert detail["can_edit"] is True and detail["can_approve"] is False
    assert "another reviewer" in detail["approval_note"].lower()
