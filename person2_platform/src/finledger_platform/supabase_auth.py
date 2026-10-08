"""Local verification of asymmetric Supabase Auth access tokens.

Authentication only: callers must look up the verified subject in their own
staff/tenant tables before granting access. Never authorize from JWT metadata.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
from uuid import UUID

import jwt


_ALGORITHMS = {"ES256": ("EC", "P-256"), "RS256": ("RSA", None)}
_CACHE_SECONDS = 300  # Supabase edge caching is separate and may add 10 minutes.
_MAX_JWKS_BYTES = 64 * 1024
_MAX_TOKEN_CHARS = 16 * 1024


class InvalidStaffToken(ValueError):
    """A token could not be authenticated; its content is never included."""


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _fetch_jwks(url: str) -> dict:
    """Fetch one bounded JWKS document from the configured issuer only."""
    with build_opener(_NoRedirects()).open(url, timeout=3) as response:
        body = response.read(_MAX_JWKS_BYTES + 1)
    if len(body) > _MAX_JWKS_BYTES:
        raise ValueError("JWKS too large")
    return json.loads(body)


class SupabaseStaffJWTVerifier:
    """Verify project Auth JWTs and return a subject UUID, never staff privileges.

    ``jwks_fetcher`` accepts the fixed project JWKS URL and returns its parsed
    JSON object. Inject it for offline tests or a controlled transport.
    """

    def __init__(self, issuer: str, *, jwks_fetcher: Callable[[str], dict] = _fetch_jwks):
        parts = urlsplit(issuer)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.path != "/auth/v1" or parts.query or parts.fragment):
            raise ValueError("issuer must be a project HTTPS /auth/v1 URL")
        self.issuer = issuer
        self.jwks_url = issuer + "/.well-known/jwks.json"
        self._jwks_fetcher = jwks_fetcher
        self._keys: dict[str, tuple[str, object]] = {}
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        document = self._jwks_fetcher(self.jwks_url)
        if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
            raise ValueError("invalid JWKS")
        keys = document["keys"]
        if not 0 < len(keys) <= 32:
            raise ValueError("invalid JWKS key count")
        parsed: dict[str, tuple[str, object]] = {}
        for item in keys:
            if not isinstance(item, dict):
                raise ValueError("invalid JWK")
            kid, alg = item.get("kid"), item.get("alg")
            if not isinstance(kid, str) or not kid or len(kid) > 256 or kid in parsed:
                raise ValueError("invalid or duplicate JWK key ID")
            if alg not in _ALGORITHMS or item.get("kty") != _ALGORITHMS[alg][0]:
                raise ValueError("unsupported JWK algorithm")
            if alg == "ES256" and item.get("crv") != "P-256":
                raise ValueError("unsupported JWK curve")
            if "d" in item or item.get("use", "sig") != "sig":
                raise ValueError("JWK is not a public signing key")
            parsed[kid] = (alg, jwt.PyJWK.from_dict(item, algorithm=alg).key)
        self._keys = parsed
        self._expires_at = time.monotonic() + _CACHE_SECONDS

    def verify(self, token: str) -> UUID:
        """Return the authenticated user ID or raise ``InvalidStaffToken``."""
        try:
            if not isinstance(token, str) or not 0 < len(token) <= _MAX_TOKEN_CHARS:
                raise ValueError("malformed token")
            header = jwt.get_unverified_header(token)
            alg, kid = header.get("alg"), header.get("kid")
            if alg not in _ALGORITHMS or not isinstance(kid, str) or not kid or len(kid) > 256:
                raise ValueError("unsupported JWT header")
            with self._lock:
                if time.monotonic() >= self._expires_at or kid not in self._keys:
                    self._refresh()
                key_alg, public_key = self._keys[kid]
            if alg != key_alg:
                raise ValueError("JWT/JWK algorithm mismatch")
            claims = jwt.decode(
                token,
                public_key,
                algorithms=[key_alg],
                issuer=self.issuer,
                audience="authenticated",
                options={"require": ["exp", "iat", "iss", "aud", "sub"], "strict_aud": True},
            )
            if claims.get("role") != "authenticated":
                raise ValueError("not an authenticated user")
            return UUID(claims["sub"])
        except Exception:
            # Never expose a token, claims, network URL, or library exception.
            raise InvalidStaffToken("invalid staff token") from None
