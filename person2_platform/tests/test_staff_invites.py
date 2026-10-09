"""Staff invitations against the app role and real RLS; Supabase Auth is always a fake here."""

from uuid import uuid4

import pytest

from finledger_platform.browser_sessions import StaffSession
from finledger_platform.staff_invites import (InviteConflict, InviteDenied, InviteInvalid, InviteUnavailable,
                                              StaffInvitations, SupabaseAdminClient, normalize_email)


class FakeAdmin:
    def __init__(self, fail_invite=False, fail_delete=False, subject=None, during_invite=None):
        self.invited, self.deleted = [], []
        self.fail_invite, self.fail_delete = fail_invite, fail_delete
        self.subject, self.during_invite = subject, during_invite

    def invite(self, email):
        self.invited.append(email)
        if self.fail_invite:
            raise InviteUnavailable("invitation could not be sent")
        if self.during_invite:
            self.during_invite()
        return self.subject or uuid4()

    def delete_user(self, subject):
        if self.fail_delete:
            raise InviteUnavailable("could not remove unlinked Auth user")
        self.deleted.append(subject)


def admin(world, aal="aal2"):
    return StaffSession(world.admin1, world.firm1, True, aal)


def user_row(owner, email):
    return owner.execute("select id, firm_id, firm_admin, auth_subject, api_token_hash from users where email = %s",
                         (email,)).fetchone()


def members(owner, user_id):
    return sorted((str(c), r) for c, r in owner.execute(
        "select client_id, role from client_members where user_id = %s", (user_id,)).fetchall())


def test_success_binds_exactly_one_subject_to_the_new_row(owner, conn, world):
    fake = FakeAdmin()
    result = StaffInvitations(fake).invite(conn, admin(world), "  New.Approver@Sharma.TEST ", name="Asha",
                                           memberships=[(world.a["id"], "approver"), (world.c["id"], "payer")])
    assert fake.invited == ["new.approver@sharma.test"]
    row = user_row(owner, "new.approver@sharma.test")
    assert row[0] == result.user_id and row[1] == world.firm1 and row[2] is False
    assert row[3] == result.auth_subject and row[4] is None  # no bearer token for invited people
    assert result.resent is False
    assert members(owner, result.user_id) == sorted([(str(world.a["id"]), "approver"), (str(world.c["id"]), "payer")])
    resolved = conn.execute("select * from finledger_private.auth_user_by_subject(%s)", (result.auth_subject,)).fetchone()
    assert resolved == {"user_id": result.user_id, "firm_id": world.firm1, "firm_admin": False}


@pytest.mark.parametrize("who", ["aal1_admin", "aal2_clerk", "stale_clerk_claiming_admin"])
def test_unauthorized_callers_are_denied_before_any_side_effect(owner, conn, world, who):
    actor = {"aal1_admin": StaffSession(world.admin1, world.firm1, True, "aal1"),
             "aal2_clerk": StaffSession(world.clerk, world.firm1, False, "aal2"),
             "stale_clerk_claiming_admin": StaffSession(world.clerk, world.firm1, True, "aal2")}[who]
    fake = FakeAdmin()
    with pytest.raises(InviteDenied):
        StaffInvitations(fake).invite(conn, actor, "x@sharma.test", memberships=[(world.a["id"], "ap_clerk")])
    assert fake.invited == [] and user_row(owner, "x@sharma.test") is None


def test_cross_firm_clients_and_unknown_roles_are_rejected(owner, conn, world):
    fake = FakeAdmin()
    invites = StaffInvitations(fake)
    with pytest.raises(InviteInvalid):
        invites.invite(conn, admin(world), "x@sharma.test", memberships=[(world.b["id"], "approver")])
    with pytest.raises(InviteInvalid):
        invites.invite(conn, admin(world), "x@sharma.test", memberships=[(uuid4(), "approver")])
    for role in ("firm_admin", "owner", "service_role", ""):
        with pytest.raises(InviteInvalid):
            invites.invite(conn, admin(world), "x@sharma.test", memberships=[(world.a["id"], role)])
    # Firm 2's admin cannot reach firm 1's client either.
    firm2_admin = StaffSession(world.admin2, world.firm2, True, "aal2")
    with pytest.raises(InviteInvalid):
        invites.invite(conn, firm2_admin, "y@iyer.test", memberships=[(world.a["id"], "payer")])
    assert fake.invited == [] and user_row(owner, "x@sharma.test") is None and user_row(owner, "y@iyer.test") is None


def test_duplicates_are_safe_and_do_not_reveal_other_firms(owner, conn, world):
    fake = FakeAdmin()
    invites = StaffInvitations(fake)
    first = invites.invite(conn, admin(world), "dup@sharma.test", memberships=[(world.a["id"], "ap_clerk")])
    with pytest.raises(InviteConflict):  # a linked person's grants never change through the invite form
        invites.invite(conn, admin(world), "dup@sharma.test", memberships=[(world.a["id"], "payer")])
    with pytest.raises(InviteConflict):
        invites.invite(conn, admin(world), "dup@sharma.test", firm_admin=True)
    with pytest.raises(InviteConflict) as other_firm:
        invites.invite(conn, admin(world), "admin@iyer.test")
    assert "iyer" not in str(other_firm.value).lower()
    assert fake.invited == ["dup@sharma.test"]
    assert user_row(owner, "dup@sharma.test")[3] == first.auth_subject
    assert members(owner, first.user_id) == [(str(world.a["id"]), "ap_clerk")]
    assert user_row(owner, "admin@iyer.test")[1] == world.firm2


def test_resend_for_linked_user_requires_same_subject_and_changes_nothing(owner, conn, world):
    first = StaffInvitations(FakeAdmin()).invite(conn, admin(world), "late@sharma.test",
                                                 memberships=[(world.a["id"], "approver")])
    again = StaffInvitations(FakeAdmin(subject=first.auth_subject)).invite(conn, admin(world), "late@sharma.test")
    assert again == type(first)(first.user_id, first.auth_subject, True)

    logs, other = [], FakeAdmin()
    with pytest.raises(InviteUnavailable):
        StaffInvitations(other, logger=logs.append).invite(conn, admin(world), "late@sharma.test")
    assert other.deleted == [] and len(logs) == 1 and "late@" not in logs[0]
    assert user_row(owner, "late@sharma.test")[3] == first.auth_subject
    assert members(owner, first.user_id) == [(str(world.a["id"]), "approver")]

    with pytest.raises(InviteUnavailable):  # e.g. Supabase refuses: the person already accepted
        StaffInvitations(FakeAdmin(fail_invite=True)).invite(conn, admin(world), "late@sharma.test")
    assert user_row(owner, "late@sharma.test")[3] == first.auth_subject


def test_auth_failure_leaves_only_an_unlinked_row_and_retry_completes_it(owner, conn, world):
    with pytest.raises(InviteUnavailable):
        StaffInvitations(FakeAdmin(fail_invite=True)).invite(
            conn, admin(world), "retry@sharma.test", memberships=[(world.a["id"], "payer"), (world.c["id"], "payer")])
    pending = user_row(owner, "retry@sharma.test")
    assert pending is not None and pending[3] is None  # cannot sign in: no subject, no token
    assert pending[4] is None

    result = StaffInvitations(FakeAdmin()).invite(conn, admin(world), "retry@sharma.test", name="Ravi",
                                                  memberships=[(world.a["id"], "approver")])
    assert result.resent is True and result.user_id == pending[0]
    assert user_row(owner, "retry@sharma.test")[3] == result.auth_subject
    assert members(owner, result.user_id) == [(str(world.a["id"]), "approver")]  # replaced, not accumulated


def test_subject_already_linked_elsewhere_fails_closed_without_deleting_live_identity(owner, conn, world):
    live = uuid4()
    owner.execute("update users set auth_subject = %s where id = %s", (live, world.admin1))
    fake = FakeAdmin(subject=live)
    with pytest.raises(InviteUnavailable):
        StaffInvitations(fake).invite(conn, admin(world), "clash@sharma.test")
    assert fake.deleted == []  # never delete an identity that is linked to someone
    assert user_row(owner, "clash@sharma.test")[3] is None
    assert conn.execute("select user_id from finledger_private.auth_user_by_subject(%s)",
                        (live,)).fetchone()["user_id"] == world.admin1


def test_concurrent_link_loses_compare_and_set_and_new_auth_user_is_removed(owner, conn, world):
    racer = uuid4()
    new_subject = uuid4()

    def someone_else_links_first():
        owner.execute("update users set auth_subject = %s where email = 'race@sharma.test'", (racer,))

    fake = FakeAdmin(subject=new_subject, during_invite=someone_else_links_first)
    with pytest.raises(InviteUnavailable):
        StaffInvitations(fake).invite(conn, admin(world), "race@sharma.test")
    assert fake.deleted == [new_subject]
    assert user_row(owner, "race@sharma.test")[3] == racer


def test_failed_compensation_is_logged_without_email_and_still_grants_nothing(owner, conn, world):
    racer, new_subject, logs = uuid4(), uuid4(), []

    def someone_else_links_first():
        owner.execute("update users set auth_subject = %s where email = 'orphan@sharma.test'", (racer,))

    fake = FakeAdmin(subject=new_subject, fail_delete=True, during_invite=someone_else_links_first)
    with pytest.raises(InviteUnavailable):
        StaffInvitations(fake, logger=logs.append).invite(conn, admin(world), "orphan@sharma.test")
    assert len(logs) == 1 and str(new_subject) in logs[0] and "orphan@" not in logs[0]
    assert conn.execute("select 1 from finledger_private.auth_user_by_subject(%s)", (new_subject,)).fetchone() is None


@pytest.mark.parametrize("bad", ["", "no-at-sign", "a@b", "two@@x.test", "sp ace@x.test", "x@-bad.test",
                                 ("a" * 65) + "@x.test"])
def test_email_validation(bad):
    with pytest.raises(InviteInvalid):
        normalize_email(bad)


def test_admin_client_sends_secret_server_side_only_and_hides_failures():
    calls = []
    subject = uuid4()

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return {"id": str(subject), "email": body["email"]} if method == "POST" else {}

    client = SupabaseAdminClient("https://proj.supabase.co", "sb_secret_test", transport=transport)
    assert "sb_secret_test" not in repr(client)
    assert client.invite("a@x.test") == subject
    client.delete_user(subject)
    assert calls[0][:2] == ("POST", "https://proj.supabase.co/auth/v1/invite")
    assert calls[0][2] == {"apikey": "sb_secret_test", "Authorization": "Bearer sb_secret_test"}
    assert calls[0][3] == {"email": "a@x.test"}
    assert calls[1][:2] == ("DELETE", f"https://proj.supabase.co/auth/v1/admin/users/{subject}")

    def broken(method, url, headers, body):
        raise RuntimeError("upstream said: sb_secret_test leaked a@x.test")

    failing = SupabaseAdminClient("https://proj.supabase.co", "sb_secret_test", transport=broken)
    with pytest.raises(InviteUnavailable) as err:
        failing.invite("a@x.test")
    assert "sb_secret_test" not in str(err.value) and err.value.__cause__ is None
    with pytest.raises(InviteUnavailable):
        SupabaseAdminClient("https://proj.supabase.co", "k", transport=lambda *a: {"id": "not-a-uuid"}).invite("a@x.test")


@pytest.mark.parametrize("url", ["http://proj.supabase.co", "https://proj.supabase.co/auth/v1",
                                 "https://user:pw@proj.supabase.co", "https://proj.supabase.co?x=1"])
def test_admin_client_rejects_non_origin_urls(url):
    with pytest.raises(ValueError):
        SupabaseAdminClient(url, "secret")


def test_admin_client_requires_secret():
    with pytest.raises(ValueError):
        SupabaseAdminClient("https://proj.supabase.co", "")



def test_existing_api_token_user_is_never_rewritten_or_linked_by_email(owner, conn, world):
    fake = FakeAdmin()
    with pytest.raises(InviteConflict):
        StaffInvitations(fake).invite(conn, admin(world), "clerk@sharma.test", memberships=[(world.c["id"], "payer")])
    with pytest.raises(InviteConflict):
        StaffInvitations(fake).invite(conn, admin(world), "clerk@sharma.test")
    assert fake.invited == []
    assert user_row(owner, "clerk@sharma.test")[3] is None
    assert members(owner, world.clerk) == [(str(world.a["id"]), "ap_clerk")]
