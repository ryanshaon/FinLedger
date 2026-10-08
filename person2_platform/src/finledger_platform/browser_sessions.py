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
        staff = self._staff(conn, verified.subject)
        cookie = secrets.token_urlsafe(32)
        conn.execute("select finledger_private.staff_session_start(%s, %s, %s, %s)",
                     (self._hash(cookie), staff["user_id"],
                      self._seal(tokens.access_token, tokens.refresh_token), verified.expires_at))
        return cookie

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
