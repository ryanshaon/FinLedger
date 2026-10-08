from fastapi.testclient import TestClient

from finledger_control.api import create_app
from finledger_control.cli import browser_sessions_from_env
from finledger_control.service import ControlService
from finledger_platform.browser_sessions import StaffSession
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


def test_browser_login_cookie_review_and_logout(pool, world):
    class Sessions:
        signed_out = False

        def sign_in(self, conn, email, password):
            assert email == "approver@sharma.test"
            return "test-cookie-with-forty-characters-minimum-123"

        def resolve(self, conn, cookie):
            assert cookie == "test-cookie-with-forty-characters-minimum-123"
            return StaffSession(world["approver"], world["client"]["firm_id"], False, "aal2")

        def sign_out(self, conn, cookie):
            self.signed_out = True

    sessions = Sessions()
    client = TestClient(create_app(pool, browser_sessions=sessions))
    protected = client.get(f"/clients/{world['client']['id']}/review", follow_redirects=False)
    assert protected.status_code == 303 and protected.headers["location"] == "/login"
    page = client.get("/login")
    assert page.status_code == 200 and "Password" in page.text
    csrf = page.cookies["fl_login_csrf"]
    bad = client.post("/login", data={"email": "approver@sharma.test", "password": "pw", "csrf_token": csrf},
                      headers={"Origin": "https://evil.test"}, follow_redirects=False)
    assert bad.status_code == 403
    login = client.post("/login", data={"email": "approver@sharma.test", "password": "pw", "csrf_token": csrf},
                        headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert login.status_code == 303 and "fl_dev_session" in login.cookies
    home = client.get("/")
    assert home.status_code == 200 and world["client"]["name"] in home.text
    inbox = client.get(f"/clients/{world['client']['id']}/review")
    assert inbox.status_code == 200
    logout = client.post("/logout", data={"csrf_token": csrf}, headers={"Origin": "http://testserver"},
                         follow_redirects=False)
    assert logout.status_code == 303 and sessions.signed_out


def test_staging_and_production_refuse_missing_browser_auth_config(monkeypatch):
    import pytest

    for key in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "FINLEDGER_ENC_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FINLEDGER_ENV", "production")
    with pytest.raises(RuntimeError):
        browser_sessions_from_env()
    monkeypatch.setenv("FINLEDGER_ENV", "staging")
    with pytest.raises(RuntimeError):
        browser_sessions_from_env()


def test_approver_must_complete_totp_before_review(pool, world):
    class Sessions:
        aal = "aal1"

        def sign_in(self, conn, email, password):
            return "test-cookie-with-forty-characters-minimum-456"

        def resolve(self, conn, cookie):
            return StaffSession(world["approver"], world["client"]["firm_id"], False, self.aal)

        def mfa_factors(self, conn, cookie):
            return ["11111111-1111-1111-1111-111111111111"]

        def mfa_verify(self, conn, cookie, factor_id, code):
            assert code == "123456"
            self.aal = "aal2"
            return "new-cookie-with-forty-characters-minimum-789"

    client = TestClient(create_app(pool, browser_sessions=Sessions()))
    csrf = client.get("/login").cookies["fl_login_csrf"]
    client.post("/login", data={"email": "approver@sharma.test", "password": "pw", "csrf_token": csrf},
                headers={"Origin": "http://testserver"})
    review = client.get(f"/clients/{world['client']['id']}/review", follow_redirects=False)
    assert review.status_code == 303 and review.headers["location"] == "/mfa"
    page = client.get("/mfa")
    assert page.status_code == 200 and "Authenticator code" in page.text
    verified = client.post("/mfa/verify", data={"csrf_token": csrf,
                           "factor_id": "11111111-1111-1111-1111-111111111111", "code": "123456"},
                           headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert verified.status_code == 303
    assert verified.cookies["fl_dev_session"] == "new-cookie-with-forty-characters-minimum-789"
    assert client.get(f"/clients/{world['client']['id']}/review").status_code == 200


def test_staging_login_csrf_cookie_is_secure_even_behind_http_proxy(pool, monkeypatch):
    monkeypatch.setenv("FINLEDGER_ENV", "staging")
    page = TestClient(create_app(pool, browser_sessions=object())).get("/login")
    assert "Secure" in page.headers["set-cookie"]


def test_firm_admin_cannot_list_clients_before_mfa(pool, world):
    class Sessions:
        def sign_in(self, conn, email, password):
            return "admin-cookie-with-forty-characters-minimum"

        def resolve(self, conn, cookie):
            return StaffSession(world["admin"], world["client"]["firm_id"], True, "aal1")

    client = TestClient(create_app(pool, browser_sessions=Sessions()))
    csrf = client.get("/login").cookies["fl_login_csrf"]
    client.post("/login", data={"email": "admin@sharma.test", "password": "pw", "csrf_token": csrf},
                headers={"Origin": "http://testserver"}, follow_redirects=False)
    page = client.get("/", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/mfa"

