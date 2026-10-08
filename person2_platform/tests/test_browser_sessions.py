"""Browser session integration against the app role and a throwaway PostgreSQL cluster."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from finledger_platform.auth_gateway import AuthTokens
from finledger_platform.browser_sessions import BrowserSessions, SessionDenied
from finledger_platform.supabase_auth import VerifiedStaffToken
from finledger_platform.supabase_auth import InvalidStaffToken


class FakeGateway:
    signed_out = False

    def sign_in(self, email, password):
        return AuthTokens("access", "refresh", 900)

    def refresh(self, token):
        return AuthTokens("fresh-access", "fresh-refresh", 900)

    def sign_out(self, token):
        self.signed_out = True

    def verified_totp_factors(self, token):
        return ["11111111-1111-1111-1111-111111111111"]

    def enroll_totp(self, token):
        return "22222222-2222-2222-2222-222222222222", "TESTSECRET"

    def challenge_totp(self, token, factor):
        return "33333333-3333-3333-3333-333333333333"

    def verify_totp(self, token, factor, challenge, code):
        assert code == "123456"
        return AuthTokens("fresh-access", "fresh-refresh", 900)


class FakeVerifier:
    subject = None

    def verify_staff(self, token):
        return VerifiedStaffToken(self.subject, "aal2" if token == "fresh-access" else "aal1",
                                  datetime.now(timezone.utc) + timedelta(minutes=10))


def test_sign_in_requires_explicit_subject_link_and_stores_only_ciphertext(owner, conn, world):
    verifier = FakeVerifier()
    verifier.subject = uuid4()
    gateway = FakeGateway()
    sessions = BrowserSessions(gateway, verifier, Fernet.generate_key())
    with pytest.raises(SessionDenied):
        sessions.sign_in(conn, "admin@sharma.test", "password")
    assert gateway.signed_out is True

    owner.execute("update users set auth_subject = %s where id = %s", (verifier.subject, world.admin1))
    cookie = sessions.sign_in(conn, "admin@sharma.test", "password")
    assert len(cookie) >= 40
    row = sessions.resolve(conn, cookie)
    assert row.user_id == world.admin1
    assert row.firm_id == world.firm1
    assert row.aal == "aal1"
    stored = owner.execute("select credential_ciphertext from finledger_private.staff_sessions").fetchone()[0]
    assert b"access" not in stored and b"refresh" not in stored
    sessions.sign_out(conn, cookie)
    assert gateway.signed_out is True
    with pytest.raises(SessionDenied):
        sessions.resolve(conn, cookie)


def test_refresh_rotates_encrypted_tokens_and_assurance(owner, conn, world):
    verifier = FakeVerifier()
    verifier.subject = uuid4()
    owner.execute("update users set auth_subject = %s where id = %s", (verifier.subject, world.admin1))
    sessions = BrowserSessions(FakeGateway(), verifier, Fernet.generate_key())
    cookie = sessions.sign_in(conn, "admin@sharma.test", "password")
    owner.execute("update finledger_private.staff_sessions set access_expires_at = now() + interval '20 seconds'")
    assert sessions.resolve(conn, cookie).aal == "aal2"
    assert owner.execute("select version from finledger_private.staff_sessions").fetchone()[0] == 2


def test_expired_access_token_is_refreshed_before_verification(owner, conn, world):
    class ExpiringVerifier(FakeVerifier):
        def verify_staff(self, token):
            if token == "access" and expired[0]:
                raise InvalidStaffToken("invalid staff token")
            return super().verify_staff(token)

    expired = [False]
    verifier = ExpiringVerifier()
    verifier.subject = uuid4()
    owner.execute("update users set auth_subject = %s where id = %s", (verifier.subject, world.admin1))
    sessions = BrowserSessions(FakeGateway(), verifier, Fernet.generate_key())
    cookie = sessions.sign_in(conn, "admin@sharma.test", "password")
    owner.execute("update finledger_private.staff_sessions set access_expires_at = now() - interval '1 second'")
    expired[0] = True
    assert sessions.resolve(conn, cookie).aal == "aal2"


def test_mfa_enrollment_and_verification_upgrade_only_same_identity(owner, conn, world):
    verifier = FakeVerifier()
    verifier.subject = uuid4()
    owner.execute("update users set auth_subject = %s where id = %s", (verifier.subject, world.admin1))
    sessions = BrowserSessions(FakeGateway(), verifier, Fernet.generate_key())
    cookie = sessions.sign_in(conn, "admin@sharma.test", "password")
    assert sessions.mfa_factors(conn, cookie) == ["11111111-1111-1111-1111-111111111111"]
    assert sessions.mfa_enroll(conn, cookie) == ("22222222-2222-2222-2222-222222222222", "TESTSECRET")
    upgraded = sessions.mfa_verify(conn, cookie, "11111111-1111-1111-1111-111111111111", "123456")
    assert upgraded != cookie
    with pytest.raises(SessionDenied):
        sessions.resolve(conn, cookie)
    assert sessions.resolve(conn, upgraded).aal == "aal2"
