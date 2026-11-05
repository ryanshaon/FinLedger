"""The development-only login: named test users, refused anywhere else. It is not real sign-in."""
import pytest

from finledger_control.dev_login import DevSessions, dev_login_allowed, parse_users
from finledger_platform.browser_sessions import SessionDenied

BASE = {"FINLEDGER_ENV": "development", "FINLEDGER_DEV_LOGIN_USERS": "a@test=t1", "FINLEDGER_SIGNING_SECRET": "s"}


@pytest.mark.parametrize("change", [
    {"FINLEDGER_ENV": "staging"}, {"FINLEDGER_ENV": "production"}, {"FINLEDGER_ENV": ""}, {"FINLEDGER_ENV": "Development"},
    {"FINLEDGER_DEV_LOGIN_USERS": ""}, {"FINLEDGER_SIGNING_SECRET": ""},
    {"SUPABASE_URL": "https://x.test", "SUPABASE_PUBLISHABLE_KEY": "k"},
])
def test_refused_outside_local_development(change):
    assert dev_login_allowed({**BASE, **change}) is False


def test_allowed_only_with_the_exact_settings():
    assert dev_login_allowed(BASE) is True
    assert parse_users(" A@test=t1 , b@test=t2,broken") == {"a@test": "t1", "b@test": "t2"}


def test_named_users_keep_their_own_identity_and_roles(pool, world):
    sessions = DevSessions({"approver@sharma.test": world["approver_token"], "clerk@sharma.test": world["clerk_token"]}, b"s")
    with pool.connection() as conn:
        cookie = sessions.sign_in(conn, "Approver@sharma.test", world["approver_token"])
        staff = sessions.resolve(conn, cookie)
        assert staff.user_id == world["approver"] and staff.firm_admin is False
        other = sessions.resolve(conn, sessions.sign_in(conn, "clerk@sharma.test", world["clerk_token"]))
        assert other.user_id == world["clerk"]


def test_wrong_password_unknown_user_and_forged_cookie_are_refused(pool, world):
    sessions = DevSessions({"approver@sharma.test": world["approver_token"]}, b"s")
    with pool.connection() as conn:
        with pytest.raises(SessionDenied):
            sessions.sign_in(conn, "approver@sharma.test", "wrong")
        with pytest.raises(SessionDenied):
            sessions.sign_in(conn, "nobody@sharma.test", world["approver_token"])
        cookie = sessions.sign_in(conn, "approver@sharma.test", world["approver_token"])
        with pytest.raises(SessionDenied):
            sessions.resolve(conn, cookie[:-1] + ("0" if cookie[-1] != "0" else "1"))
