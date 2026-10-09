"""Browser invite and first-login routes. Real invite service and RLS; Supabase Auth is always a fake."""
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from finledger_control.api import create_app
from finledger_control.cli import staff_invitations_from_env
from finledger_platform.browser_sessions import SessionDenied, StaffSession
from finledger_platform.staff_invites import StaffInvitations
from finledger_platform.tenancy import create_client, create_firm_with_admin

COOKIE = "invite-test-cookie-with-forty-characters-min"
SAME_ORIGIN = {"Origin": "http://testserver"}


class FakeAdmin:
    def __init__(self):
        self.invited = []

    def invite(self, email):
        self.invited.append(email)
        return uuid4()

    def delete_user(self, subject):
        raise AssertionError("no compensation expected")


class Sessions:
    def __init__(self, user_id, firm_id, firm_admin, aal):
        self.who = StaffSession(user_id, firm_id, firm_admin, aal)
        self.accepted = []
        self.deny_accept = False

    def sign_in(self, conn, email, password):
        return COOKIE

    def resolve(self, conn, cookie):
        assert cookie == COOKIE
        return self.who

    def accept_invite(self, conn, token_hash, password):
        if self.deny_accept:
            raise SessionDenied("invitation invalid or expired")
        self.accepted.append((token_hash, password))


def signed_in(pool, sessions, invitations):
    client = TestClient(create_app(pool, browser_sessions=sessions, staff_invitations=invitations))
    csrf = client.get("/login").cookies["fl_login_csrf"]
    client.post("/login", data={"email": "x@sharma.test", "password": "pw", "csrf_token": csrf},
                headers=SAME_ORIGIN, follow_redirects=False)
    return client, csrf


def admin_sessions(world, aal="aal2"):
    return Sessions(world["admin"], world["client"]["firm_id"], True, aal)


def row(owner_dsn, email):
    with psycopg.connect(owner_dsn) as c:
        return c.execute("select id, auth_subject, firm_admin from users where email = %s", (email,)).fetchone()


def test_admin_with_mfa_invites_and_binds_subject(pool, owner_dsn, world):
    fake = FakeAdmin()
    client, csrf = signed_in(pool, admin_sessions(world), StaffInvitations(fake))
    page = client.get("/admin/staff")
    assert page.status_code == 200 and "Acme Steel" in page.text and "Approver" in page.text
    sent = client.post("/admin/staff/invite", headers=SAME_ORIGIN, data={
        "csrf_token": csrf, "email": "New.Clerk@Sharma.test", "name": "Neha",
        "membership": [f"{world['client']['id']}:ap_clerk"]})
    assert sent.status_code == 201 and "Invitation sent to new.clerk@sharma.test" in sent.text
    assert fake.invited == ["new.clerk@sharma.test"]
    user_id, subject, is_admin = row(owner_dsn, "new.clerk@sharma.test")
    assert subject is not None and is_admin is False
    with psycopg.connect(owner_dsn) as c:
        assert c.execute("select role from client_members where user_id = %s", (user_id,)).fetchall() == [("ap_clerk",)]


def test_aal1_admin_is_sent_to_mfa_and_nothing_is_invited(pool, owner_dsn, world):
    fake = FakeAdmin()
    client, csrf = signed_in(pool, admin_sessions(world, "aal1"), StaffInvitations(fake))
    page = client.get("/admin/staff", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/mfa"
    post = client.post("/admin/staff/invite", headers=SAME_ORIGIN, follow_redirects=False,
                       data={"csrf_token": csrf, "email": "x@sharma.test"})
    assert post.status_code == 303 and post.headers["location"] == "/mfa"
    assert fake.invited == [] and row(owner_dsn, "x@sharma.test") is None


def test_non_admin_and_forged_requests_are_denied(pool, owner_dsn, world):
    fake = FakeAdmin()
    approver = Sessions(world["approver"], world["client"]["firm_id"], False, "aal2")
    client, csrf = signed_in(pool, approver, StaffInvitations(fake))
    assert client.get("/admin/staff").status_code == 403
    assert client.post("/admin/staff/invite", headers=SAME_ORIGIN,
                       data={"csrf_token": csrf, "email": "x@sharma.test"}).status_code == 403

    client, csrf = signed_in(pool, admin_sessions(world), StaffInvitations(fake))
    assert client.post("/admin/staff/invite", headers={"Origin": "https://evil.test"},
                       data={"csrf_token": csrf, "email": "x@sharma.test"}).status_code == 403
    assert client.post("/admin/staff/invite", headers=SAME_ORIGIN,
                       data={"csrf_token": "wrong", "email": "x@sharma.test"}).status_code == 403
    assert fake.invited == [] and row(owner_dsn, "x@sharma.test") is None


def test_cross_firm_client_and_bad_roles_are_rejected(pool, owner_dsn, world):
    with psycopg.connect(owner_dsn, autocommit=True) as owner:
        other_firm, _, _ = create_firm_with_admin(owner, "Iyer Associates", "admin@iyer.test")
        with owner.transaction():
            owner.execute("select set_config('app.firm_id', %s, true)", (str(other_firm),))
            other_client = owner.execute("insert into clients (firm_id, name, public_token, inbound_slug) "
                                         "values (%s, 'Bharat', 'tok-bharat', 'bharat') returning id",
                                         (other_firm,)).fetchone()[0]
    fake = FakeAdmin()
    client, csrf = signed_in(pool, admin_sessions(world), StaffInvitations(fake))
    for membership in (f"{other_client}:approver", f"{world['client']['id']}:firm_admin", "not-a-uuid:payer"):
        response = client.post("/admin/staff/invite", headers=SAME_ORIGIN,
                               data={"csrf_token": csrf, "email": "x@sharma.test", "membership": [membership]})
        assert response.status_code == 422, membership
    assert "Bharat" not in client.get("/admin/staff").text
    assert fake.invited == [] and row(owner_dsn, "x@sharma.test") is None


def test_duplicate_invite_reports_conflict_without_resending(pool, world):
    fake = FakeAdmin()
    client, csrf = signed_in(pool, admin_sessions(world), StaffInvitations(fake))
    data = {"csrf_token": csrf, "email": "approver@sharma.test", "membership": [f"{world['client']['id']}:payer"]}
    response = client.post("/admin/staff/invite", headers=SAME_ORIGIN, data=data)
    assert response.status_code == 409 and fake.invited == []
    with pool.connection() as conn, conn.transaction():  # the existing approver's roles are untouched
        conn.execute("select set_config('app.firm_id', %s, true)", (str(world["client"]["firm_id"]),))
        assert conn.execute("select role from client_members where user_id = %s",
                            (world["approver"],)).fetchall() == [{"role": "approver"}]


def test_invite_page_reports_missing_configuration(pool, world, monkeypatch):
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    assert staff_invitations_from_env() is None
    client, csrf = signed_in(pool, admin_sessions(world), None)
    response = client.post("/admin/staff/invite", headers=SAME_ORIGIN,
                           data={"csrf_token": csrf, "email": "x@sharma.test"})
    assert response.status_code == 503 and "not configured" in response.text


def test_accept_invite_get_never_redeems_and_post_sets_password(pool, world):
    sessions = admin_sessions(world)
    client = TestClient(create_app(pool, browser_sessions=sessions))
    assert client.get("/auth/accept?type=signup&token_hash=abc").status_code == 400
    page = client.get("/auth/accept?type=invite&token_hash=hash-0123456789abcdef")
    assert page.status_code == 200 and "Set your password" in page.text and sessions.accepted == []
    csrf = page.cookies["fl_login_csrf"]
    form = {"csrf_token": csrf, "token_hash": "hash-0123456789abcdef"}

    assert client.post("/auth/accept", headers={"Origin": "https://evil.test"},
                       data={**form, "password": "x" * 12, "password_confirm": "x" * 12}).status_code == 403
    short = client.post("/auth/accept", headers=SAME_ORIGIN, data={**form, "password": "short", "password_confirm": "short"})
    assert short.status_code == 400 and "12 characters" in short.text
    mismatch = client.post("/auth/accept", headers=SAME_ORIGIN,
                           data={**form, "password": "a" * 12, "password_confirm": "b" * 12})
    assert mismatch.status_code == 400 and sessions.accepted == []

    done = client.post("/auth/accept", headers=SAME_ORIGIN, follow_redirects=False,
                       data={**form, "password": "correct horse battery", "password_confirm": "correct horse battery"})
    assert done.status_code == 303 and done.headers["location"] == "/login"
    assert sessions.accepted == [("hash-0123456789abcdef", "correct horse battery")]
    assert "fl_dev_session" not in done.cookies  # first login never creates a session; MFA comes after sign-in

    sessions.deny_accept = True
    denied = client.post("/auth/accept", headers=SAME_ORIGIN,
                         data={**form, "password": "correct horse battery", "password_confirm": "correct horse battery"})
    assert denied.status_code == 400 and "invite you again" in denied.text
