"""Server-side browser sessions; only an opaque random cookie is sent to the browser."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken

from .auth_gateway import AuthUnavailable
from .supabase_auth import InvalidStaffToken


MIN_PASSWORD_CHARS = 12


class SessionDenied(ValueError):
    """No usable provisioned browser session; never includes credentials."""


@dataclass(frozen=True)
class StaffSession:
    user_id: UUID
    firm_id: UUID
    firm_admin: bool
    aal: str


class BrowserSessions:
    def __init__(self, gateway, verifier, encryption_key: str | bytes):
        if not encryption_key:
            raise ValueError("FINLEDGER_ENC_KEY is required for browser sessions")
        self._cipher = Fernet(encryption_key)
        self._gateway = gateway
        self._verifier = verifier

    @staticmethod
    def _hash(cookie: str) -> bytes:
        if not isinstance(cookie, str) or not 40 <= len(cookie) <= 128:
            raise SessionDenied("session unavailable")
        try:
            return hashlib.sha256(cookie.encode("ascii")).digest()
        except UnicodeEncodeError:
            raise SessionDenied("session unavailable") from None

    def _seal(self, access: str, refresh: str) -> bytes:
        return self._cipher.encrypt(json.dumps({"access": access, "refresh": refresh}).encode())

    def _open(self, value: bytes) -> dict:
        try:
            data = json.loads(self._cipher.decrypt(value))
            if not isinstance(data["access"], str) or not isinstance(data["refresh"], str):
                raise ValueError("bad credential bundle")
            return data
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise SessionDenied("session unavailable") from None

    def _staff(self, conn, subject: UUID) -> dict:
        row = conn.execute("select * from finledger_private.auth_user_by_subject(%s)", (subject,)).fetchone()
        if not row:
            raise SessionDenied("account not provisioned")
        return row

    def sign_in(self, conn, email: str, password: str) -> str:
        try:
            tokens = self._gateway.sign_in(email, password)
            verified = self._verifier.verify_staff(tokens.access_token)
        except (AuthUnavailable, InvalidStaffToken):
            raise SessionDenied("sign-in denied") from None
        try:
            staff = self._staff(conn, verified.subject)
        except SessionDenied:
            try:
                self._gateway.sign_out(tokens.access_token)
            except AuthUnavailable:
                pass
            raise
        cookie = secrets.token_urlsafe(32)
        conn.execute("select finledger_private.staff_session_start(%s, %s, %s, %s)",
                     (self._hash(cookie), staff["user_id"],
                      self._seal(tokens.access_token, tokens.refresh_token), verified.expires_at))
        return cookie

    def accept_invite(self, conn, token_hash: str, password: str) -> None:
        """First login: redeem the emailed invite, require an explicitly linked user, set a password, end the
        temporary Auth session. No browser session is created; the person then signs in and enrolls TOTP."""
        if not isinstance(password, str) or not MIN_PASSWORD_CHARS <= len(password) <= 1024:
            raise SessionDenied("password does not meet policy")  # checked first so a typo never burns the link
        try:
            tokens = self._gateway.verify_invite(token_hash)
            verified = self._verifier.verify_staff(tokens.access_token)
        except (AuthUnavailable, InvalidStaffToken):
            raise SessionDenied("invitation invalid or expired") from None
        try:
            self._staff(conn, verified.subject)  # linked by the invite flow only; never matched by email
            self._gateway.set_password(tokens.access_token, password)
        except AuthUnavailable:
            raise SessionDenied("password could not be set") from None
        finally:
            try:
                self._gateway.sign_out(tokens.access_token)
            except AuthUnavailable:
                pass

    def resolve(self, conn, cookie: str) -> StaffSession:
        digest = self._hash(cookie)
        row = conn.execute("select * from finledger_private.staff_session_touch(%s)", (digest,)).fetchone()
        if not row:
            raise SessionDenied("session unavailable")
        bundle = self._open(row["credential_ciphertext"])
        try:
            if row["access_expires_at"] <= datetime.now(timezone.utc) + timedelta(seconds=60):
                tokens = self._gateway.refresh(bundle["refresh"])
                verified = self._verifier.verify_staff(tokens.access_token)
                if not conn.execute("select finledger_private.staff_session_rotate(%s, %s, %s, %s) as ok",
                                    (digest, row["version"], self._seal(tokens.access_token, tokens.refresh_token),
                                     verified.expires_at)).fetchone()["ok"]:
                    raise SessionDenied("session changed; retry")
            else:
                verified = self._verifier.verify_staff(bundle["access"])
            staff = self._staff(conn, verified.subject)
            if staff["user_id"] != row["user_id"]:
                raise SessionDenied("session identity changed")
            return StaffSession(staff["user_id"], staff["firm_id"], staff["firm_admin"], verified.aal)
        except (AuthUnavailable, InvalidStaffToken):
            raise SessionDenied("session unavailable") from None

    def sign_out(self, conn, cookie: str) -> None:
        digest = self._hash(cookie)
        row = conn.execute("select * from finledger_private.staff_session_touch(%s)", (digest,)).fetchone()
        try:
            if row:
                bundle = self._open(row["credential_ciphertext"])
                self._gateway.sign_out(bundle["access"])
        except (AuthUnavailable, SessionDenied):
            pass  # Local revocation must succeed even if Supabase is unavailable.
        finally:
            conn.execute("select finledger_private.staff_session_revoke(%s)", (digest,))

    def _mfa_credentials(self, conn, cookie: str) -> tuple[bytes, dict, dict]:
        self.resolve(conn, cookie)  # Re-check identity and refresh access token when required.
        digest = self._hash(cookie)
        row = conn.execute("select * from finledger_private.staff_session_touch(%s)", (digest,)).fetchone()
        if not row:
            raise SessionDenied("session unavailable")
        return digest, row, self._open(row["credential_ciphertext"])

    def mfa_factors(self, conn, cookie: str) -> list[str]:
        _, _, credentials = self._mfa_credentials(conn, cookie)
        try:
            return self._gateway.verified_totp_factors(credentials["access"])
        except AuthUnavailable:
            raise SessionDenied("MFA unavailable") from None

    def mfa_enroll(self, conn, cookie: str) -> tuple[str, str]:
        _, _, credentials = self._mfa_credentials(conn, cookie)
        try:
            return self._gateway.enroll_totp(credentials["access"])
        except AuthUnavailable:
            raise SessionDenied("MFA unavailable") from None

    def mfa_verify(self, conn, cookie: str, factor_id: str, code: str) -> str:
        digest, row, credentials = self._mfa_credentials(conn, cookie)
        try:
            challenge = self._gateway.challenge_totp(credentials["access"], factor_id)
            tokens = self._gateway.verify_totp(credentials["access"], factor_id, challenge, code)
            verified = self._verifier.verify_staff(tokens.access_token)
            staff = self._staff(conn, verified.subject)
            if verified.aal != "aal2" or staff["user_id"] != row["user_id"]:
                raise SessionDenied("MFA identity mismatch")
            upgraded_cookie = secrets.token_urlsafe(32)
            ok = conn.execute("select finledger_private.staff_session_upgrade(%s, %s, %s, %s, %s) as ok",
                              (digest, self._hash(upgraded_cookie), row["version"],
                               self._seal(tokens.access_token, tokens.refresh_token),
                               verified.expires_at)).fetchone()["ok"]
            if not ok:
                raise SessionDenied("session changed; retry")
            return upgraded_cookie
        except (AuthUnavailable, InvalidStaffToken):
            raise SessionDenied("MFA denied") from None
