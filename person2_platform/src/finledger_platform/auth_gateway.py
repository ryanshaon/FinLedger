"""Bounded, server-only Supabase Auth REST client. No service-role key is accepted here."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID


class AuthUnavailable(ValueError):
    """Authentication failed; intentionally contains no upstream response details."""


@dataclass(frozen=True)
class AuthTokens:
    access_token: str
    refresh_token: str
    expires_in: int


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _json_transport(method: str, url: str, headers: dict, body: dict | None) -> dict:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(url, data=payload, method=method,
                      headers={**headers, "Content-Type": "application/json", "Accept": "application/json"})
    with build_opener(_NoRedirects()).open(request, timeout=4) as response:
        data = response.read(64 * 1024 + 1)
    if len(data) > 64 * 1024:
        raise ValueError("response too large")
    return json.loads(data) if data else {}


class SupabaseAuthClient:
    def __init__(self, base_url: str, publishable_key: str,
                 *, transport: Callable[[str, str, dict, dict | None], dict] = _json_transport):
        parts = urlsplit(base_url)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.path not in ("", "/") or parts.query or parts.fragment):
            raise ValueError("Supabase Auth URL must be a project HTTPS origin")
        if not publishable_key:
            raise ValueError("publishable key required")
        self.origin = base_url.rstrip("/")
        self._key = publishable_key
        self._transport = transport

    @staticmethod
    def _tokens(response: dict) -> AuthTokens:
        access, refresh, seconds = (response["access_token"], response["refresh_token"],
                                    response["expires_in"])
        if (not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh
                or not isinstance(seconds, int) or isinstance(seconds, bool) or not 0 < seconds <= 3600):
            raise ValueError("invalid token response")
        return AuthTokens(access, refresh, seconds)

    def _token(self, grant: str, body: dict) -> AuthTokens:
        try:
            response = self._transport("POST", f"{self.origin}/auth/v1/token?grant_type={grant}",
                                       {"apikey": self._key}, body)
            return self._tokens(response)
        except Exception:
            raise AuthUnavailable("authentication unavailable or denied") from None

    def sign_in(self, email: str, password: str) -> AuthTokens:
        return self._token("password", {"email": email, "password": password})

    def verify_invite(self, token_hash: str) -> AuthTokens:
        """Redeem a one-time invite ``token_hash`` from the email link for a first session."""
        if (not isinstance(token_hash, str) or not 16 <= len(token_hash) <= 512 or not token_hash.isascii()
                or not all(c.isalnum() or c in "-_." for c in token_hash)):
            raise AuthUnavailable("invalid invitation")
        try:
            response = self._transport("POST", f"{self.origin}/auth/v1/verify", {"apikey": self._key},
                                       {"type": "invite", "token_hash": token_hash})
            return self._tokens(response)
        except Exception:
            raise AuthUnavailable("invitation invalid or expired") from None

    def set_password(self, access_token: str, password: str) -> None:
        self._user_request("PUT", "user", access_token, {"password": password})

    def refresh(self, refresh_token: str) -> AuthTokens:
        return self._token("refresh_token", {"refresh_token": refresh_token})

    def sign_out(self, access_token: str) -> None:
        try:
            self._transport("POST", f"{self.origin}/auth/v1/logout?scope=local",
                            {"apikey": self._key, "Authorization": f"Bearer {access_token}"}, {})
        except Exception:
            # Caller must still revoke its server-side session when Supabase is unavailable.
            raise AuthUnavailable("sign-out upstream unavailable") from None

    def _user_request(self, method: str, path: str, access_token: str, body: dict | None) -> dict:
        try:
            response = self._transport(method, f"{self.origin}/auth/v1/{path}",
                                       {"apikey": self._key, "Authorization": f"Bearer {access_token}"}, body)
            if not isinstance(response, dict):
                raise ValueError("invalid Auth response")
            return response
        except Exception:
            raise AuthUnavailable("MFA unavailable or denied") from None

    @staticmethod
    def _factor_id(value: str) -> str:
        try:
            return str(UUID(value))
        except (ValueError, TypeError, AttributeError):
            raise AuthUnavailable("invalid MFA factor") from None

    def verified_totp_factors(self, access_token: str) -> list[str]:
        result = self._user_request("GET", "user", access_token, None)
        factors = result.get("factors", [])
        if not isinstance(factors, list):
            raise AuthUnavailable("invalid MFA factors")
        try:
            return [self._factor_id(f["id"]) for f in factors
                    if isinstance(f, dict) and f.get("factor_type") == "totp" and f.get("status") == "verified"]
        except KeyError:
            raise AuthUnavailable("invalid MFA factors") from None

    def enroll_totp(self, access_token: str) -> tuple[str, str]:
        result = self._user_request("POST", "factors", access_token, {"factor_type": "totp"})
        try:
            factor = self._factor_id(result["id"])
            secret = result["totp"]["secret"]
            if not isinstance(secret, str) or not secret:
                raise ValueError("invalid TOTP secret")
            return factor, secret
        except (KeyError, TypeError, ValueError):
            raise AuthUnavailable("MFA enrollment unavailable") from None

    def challenge_totp(self, access_token: str, factor_id: str) -> str:
        factor = self._factor_id(factor_id)
        result = self._user_request("POST", f"factors/{factor}/challenge", access_token, {})
        try:
            return str(UUID(result["id"]))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise AuthUnavailable("MFA challenge unavailable") from None

    def verify_totp(self, access_token: str, factor_id: str, challenge_id: str, code: str) -> AuthTokens:
        factor, challenge = self._factor_id(factor_id), self._factor_id(challenge_id)
        if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
            raise AuthUnavailable("invalid MFA code")
        response = self._user_request("POST", f"factors/{factor}/verify", access_token,
                                      {"challenge_id": challenge, "code": code})
        try:
            return self._tokens(response)
        except (KeyError, TypeError, ValueError):
            raise AuthUnavailable("MFA verification unavailable") from None
