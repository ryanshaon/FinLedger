"""Development-only named test users, for testing maker-checker on this computer. This is not real sign-in.

It implements the small part of ``BrowserSessions`` the desk uses, so the desk runs the same session paths it runs with
Supabase. Each named user signs in with their email and their own API token as the password. It is refused unless the
environment is exactly ``development``, Supabase sign-in isn't configured, and the users are listed in
``FINLEDGER_DEV_LOGIN_USERS`` as ``email=token,email=token``; ``cli.main`` also refuses it on anything but a loopback
address. Two-step verification is not proven here: every test session reports ``aal2``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Mapping

from finledger_platform.browser_sessions import SessionDenied, StaffSession


def dev_login_allowed(env: Mapping[str, str]) -> bool:
    if env.get("FINLEDGER_ENV", "") != "development":
        return False
    if env.get("SUPABASE_URL", "").strip() and env.get("SUPABASE_PUBLISHABLE_KEY", "").strip():
        return False
    return bool(env.get("FINLEDGER_DEV_LOGIN_USERS", "").strip() and env.get("FINLEDGER_SIGNING_SECRET", ""))


def parse_users(text: str) -> dict[str, str]:
    users = {}
    for pair in text.split(","):
        email, _, token = pair.partition("=")
        if email.strip() and token.strip():
            users[email.strip().lower()] = token.strip()
    return users


class DevSessions:
    def __init__(self, users: Mapping[str, str], secret: bytes):
        self._users = dict(users)
        self._secret = secret

    def _sign(self, email: str) -> str:
        return hmac.new(self._secret, email.encode(), hashlib.sha256).hexdigest()

    def _staff(self, conn, email: str) -> StaffSession:
        digest = hashlib.sha256(self._users[email].encode()).digest()
        row = conn.execute("select * from auth_user(%s)", (digest,)).fetchone()
        if not row:
            raise SessionDenied("this test user isn't provisioned")
        return StaffSession(row["user_id"], row["firm_id"], row["firm_admin"], "aal2", mfa_required=False)

    def sign_in(self, conn, email: str, password: str) -> str:
        email = email.strip().lower()
        if email not in self._users or not hmac.compare_digest(password.encode(), self._users[email].encode()):
            raise SessionDenied("sign-in denied")
        self._staff(conn, email)
        return base64.urlsafe_b64encode(email.encode()).decode() + "." + self._sign(email)

    def resolve(self, conn, cookie: str) -> StaffSession:
        try:
            encoded, signature = cookie.split(".", 1)
            email = base64.urlsafe_b64decode(encoded.encode()).decode()
        except (ValueError, UnicodeError):
            raise SessionDenied("session unavailable") from None
        if email not in self._users or not hmac.compare_digest(signature, self._sign(email)):
            raise SessionDenied("session unavailable")
        return self._staff(conn, email)

    def sign_out(self, conn, cookie: str) -> None:
        return None

    def accept_invite(self, conn, token_hash: str, password: str) -> None:
        raise SessionDenied("invitations need real sign-in")

    def mfa_factors(self, conn, cookie: str) -> list[str]:
        return []

    def mfa_enroll(self, conn, cookie: str) -> tuple[str, str]:
        raise SessionDenied("two-step verification needs real sign-in")

    def mfa_verify(self, conn, cookie: str, factor_id: str, code: str) -> str:
        raise SessionDenied("two-step verification needs real sign-in")
