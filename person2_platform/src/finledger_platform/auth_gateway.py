"""Bounded, server-only Supabase Auth REST client. No service-role key is accepted here."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


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


def _json_transport(method: str, url: str, headers: dict, body: dict) -> dict:
    payload = json.dumps(body).encode("utf-8")
    request = Request(url, data=payload, method=method,
                      headers={**headers, "Content-Type": "application/json", "Accept": "application/json"})
    with build_opener(_NoRedirects()).open(request, timeout=4) as response:
        data = response.read(64 * 1024 + 1)
    if len(data) > 64 * 1024:
        raise ValueError("response too large")
    return json.loads(data) if data else {}


class SupabaseAuthClient:
    def __init__(self, base_url: str, publishable_key: str,
                 *, transport: Callable[[str, str, dict, dict], dict] = _json_transport):
        parts = urlsplit(base_url)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.path not in ("", "/") or parts.query or parts.fragment):
            raise ValueError("Supabase Auth URL must be a project HTTPS origin")
        if not publishable_key:
            raise ValueError("publishable key required")
        self.origin = base_url.rstrip("/")
        self._key = publishable_key
        self._transport = transport

    def _token(self, grant: str, body: dict) -> AuthTokens:
        try:
            response = self._transport("POST", f"{self.origin}/auth/v1/token?grant_type={grant}",
                                       {"apikey": self._key}, body)
            access, refresh, seconds = (response["access_token"], response["refresh_token"],
                                        response["expires_in"])
            if (not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh
                    or not isinstance(seconds, int) or isinstance(seconds, bool) or not 0 < seconds <= 3600):
                raise ValueError("invalid token response")
            return AuthTokens(access, refresh, seconds)
        except Exception:
            raise AuthUnavailable("authentication unavailable or denied") from None

    def sign_in(self, email: str, password: str) -> AuthTokens:
        return self._token("password", {"email": email, "password": password})

    def refresh(self, refresh_token: str) -> AuthTokens:
        return self._token("refresh_token", {"refresh_token": refresh_token})

    def sign_out(self, access_token: str) -> None:
        try:
            self._transport("POST", f"{self.origin}/auth/v1/logout?scope=local",
                            {"apikey": self._key, "Authorization": f"Bearer {access_token}"}, {})
        except Exception:
            # Caller must still revoke its server-side session when Supabase is unavailable.
            raise AuthUnavailable("sign-out upstream unavailable") from None
