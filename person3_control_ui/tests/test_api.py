from fastapi.testclient import TestClient

from finledger_control.api import create_app
from finledger_control.service import ControlService
from test_service import canonical, draft


def test_review_inbox_and_detail_render_through_authenticated_api(pool, conn, world, document):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document,
              canonical(document, confidences={"invoice_no":.2,"date":.2,"gstin":.99,"total":.99}), world["clerk"])
    svc.submit_draft(world["client"]["id"], document, draft(document), world["clerk"])
    app = create_app(pool, source_url=lambda path, name: "/files/signed")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {world['approver_token']}"}
    inbox = client.get(f"/clients/{world['client']['id']}/review", headers=headers)
    assert inbox.status_code == 200 and "INV-42" in inbox.text
    page = client.get(f"/clients/{world['client']['id']}/review/{document}", headers=headers)
    assert page.status_code == 200 and "Approve &amp; post" in page.text


def test_api_rejects_user_without_client_membership(pool, conn, world, document):
    app = create_app(pool, source_url=lambda path, name: "/files/signed")
    client = TestClient(app)
    response = client.get(f"/clients/{world['client']['id']}/review", headers={"Authorization":"Bearer bad"})
    assert response.status_code == 401


def test_mutating_forms_require_same_origin_and_csrf_token(pool, conn, world, document):
    svc = ControlService(conn)
    svc.score(world["client"]["id"], document, canonical(document), world["clerk"])
    svc.submit_draft(world["client"]["id"], document, draft(document), world["clerk"])
    client = TestClient(create_app(pool))
    headers = {"Authorization": f"Bearer {world['approver_token']}"}
    page = client.get(f"/clients/{world['client']['id']}/review/{document}", headers=headers)
    token = page.cookies["finledger_csrf"]
    rejected = client.post(f"/clients/{world['client']['id']}/review/{document}/approve",
                           headers={**headers, "Origin":"https://evil.test"},
                           data={"revision":1,"post":"false","csrf_token":token})
    assert rejected.status_code == 403
    accepted = client.post(f"/clients/{world['client']['id']}/review/{document}/approve",
                           headers={**headers, "Origin":"http://testserver"},
                           data={"revision":1,"post":"false","csrf_token":token}, follow_redirects=False)
    assert accepted.status_code == 303

