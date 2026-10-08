"""Offline contract tests for Supabase Auth access-token verification."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from finledger_platform.supabase_auth import InvalidStaffToken, SupabaseStaffJWTVerifier


ISSUER = "https://example-project.supabase.co/auth/v1"
SUBJECT = UUID("85fcd1b0-e270-4ad7-a00c-24b7d7811ef8")


def _key(kid: str = "first"):
    private = ec.generate_private_key(ec.SECP256R1())
    public_jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private.public_key()))
    public_jwk.update(kid=kid, alg="ES256", use="sig")
    return private, public_jwk


def _token(private, kid: str = "first", **claims):
    now = datetime.now(timezone.utc)
    payload = {
        "iss": ISSUER,
        "aud": "authenticated",
        "sub": str(SUBJECT),
        "role": "authenticated",
        "iat": now,
        "exp": now + timedelta(minutes=10),
    }
    payload.update(claims)
    return jwt.encode(payload, private, algorithm="ES256", headers={"kid": kid})


def test_valid_staff_token_returns_only_subject_uuid():
    private, public_jwk = _key()
    requested = []
    verifier = SupabaseStaffJWTVerifier(
        ISSUER, jwks_fetcher=lambda url: requested.append(url) or {"keys": [public_jwk]}
    )

    assert verifier.verify(_token(private, user_metadata={"firm_admin": True})) == SUBJECT
    assert requested == [ISSUER + "/.well-known/jwks.json"]


def test_verified_claims_expose_only_subject_assurance_and_expiry():
    private, public_jwk = _key()
    verifier = SupabaseStaffJWTVerifier(ISSUER, jwks_fetcher=lambda _: {"keys": [public_jwk]})
    token = _token(private, aal="aal2", user_metadata={"firm_admin": True})

    verified = verifier.verify_staff(token)
    assert verified.subject == SUBJECT
    assert verified.aal == "aal2"
    assert verified.expires_at > datetime.now(timezone.utc)
    assert not hasattr(verified, "user_metadata")


@pytest.mark.parametrize(
    "changes",
    [
        {"exp": datetime(2020, 1, 1, tzinfo=timezone.utc)},
        {"iss": "https://other-project.supabase.co/auth/v1"},
        {"iss": ISSUER + "/"},
        {"aud": "anon"},
        {"aud": ["authenticated", "other"]},
        {"sub": None},
        {"sub": "not-a-uuid"},
        {"iat": datetime(2099, 1, 1, tzinfo=timezone.utc)},
        {"nbf": datetime(2099, 1, 1, tzinfo=timezone.utc)},
        {"role": "anon"},
    ],
)
def test_rejects_invalid_claims(changes):
    private, public_jwk = _key()
    verifier = SupabaseStaffJWTVerifier(ISSUER, jwks_fetcher=lambda _: {"keys": [public_jwk]})

    with pytest.raises(InvalidStaffToken):
        verifier.verify(_token(private, **changes))


def test_rejects_missing_required_claims():
    private, public_jwk = _key()
    verifier = SupabaseStaffJWTVerifier(ISSUER, jwks_fetcher=lambda _: {"keys": [public_jwk]})
    now = datetime.now(timezone.utc)
    payload = {"iss": ISSUER, "aud": "authenticated", "role": "authenticated", "exp": now + timedelta(minutes=5)}

    with pytest.raises(InvalidStaffToken):
        verifier.verify(jwt.encode(payload, private, algorithm="ES256", headers={"kid": "first"}))


def test_rejects_bad_signature():
    signing_private, _ = _key()
    _, trusted_public = _key()
    verifier = SupabaseStaffJWTVerifier(ISSUER, jwks_fetcher=lambda _: {"keys": [trusted_public]})

    with pytest.raises(InvalidStaffToken):
        verifier.verify(_token(signing_private))


def test_rejects_unapproved_algorithm_before_fetch():
    requests = []
    verifier = SupabaseStaffJWTVerifier(ISSUER, jwks_fetcher=lambda url: requests.append(url) or {"keys": []})
    token = jwt.encode({"sub": str(SUBJECT)}, "not-a-project-key-strong-enough-now", algorithm="HS256", headers={"kid": "first"})

    with pytest.raises(InvalidStaffToken):
        verifier.verify(token)
    assert requests == []


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c", None])
def test_rejects_malformed_tokens(token):
    verifier = SupabaseStaffJWTVerifier(ISSUER, jwks_fetcher=lambda _: {"keys": []})
    with pytest.raises(InvalidStaffToken):
        verifier.verify(token)


def test_rejects_jwks_network_failure_without_leaking_token():
    private, _ = _key()
    token = _token(private)

    def unavailable(_):
        raise OSError("connection unavailable")

    verifier = SupabaseStaffJWTVerifier(ISSUER, jwks_fetcher=unavailable)
    with pytest.raises(InvalidStaffToken) as error:
        verifier.verify(token)
    assert token not in str(error.value)


def test_unknown_kid_refreshes_cached_jwks_for_rotation():
    old_private, old_public = _key("old")
    new_private, new_public = _key("new")
    responses = [{"keys": [old_public]}, {"keys": [old_public, new_public]}]
    requests = []

    def fetch(url):
        requests.append(url)
        return responses.pop(0)

    verifier = SupabaseStaffJWTVerifier(ISSUER, jwks_fetcher=fetch)
    assert verifier.verify(_token(old_private, "old")) == SUBJECT
    assert verifier.verify(_token(new_private, "new")) == SUBJECT
    assert len(requests) == 2


def test_rejects_duplicate_kid_in_jwks():
    private, public = _key()
    _, different_public = _key()
    verifier = SupabaseStaffJWTVerifier(ISSUER, jwks_fetcher=lambda _: {"keys": [public, different_public]})
    with pytest.raises(InvalidStaffToken):
        verifier.verify(_token(private))


def test_accepts_rs256_from_project_jwks():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    public.update(kid="rsa", alg="RS256", key_ops=["verify"])
    now = datetime.now(timezone.utc)
    token = jwt.encode({
        "iss": ISSUER, "aud": "authenticated", "sub": str(SUBJECT),
        "role": "authenticated", "iat": now, "exp": now + timedelta(minutes=5),
    }, private, algorithm="RS256", headers={"kid": "rsa"})
    verifier = SupabaseStaffJWTVerifier(ISSUER, jwks_fetcher=lambda _: {"keys": [public]})

    assert verifier.verify(token) == SUBJECT


def test_cached_key_is_reused_until_ttl(monkeypatch):
    private, public = _key()
    requests = []
    monkeypatch.setattr("finledger_platform.supabase_auth.time.monotonic", lambda: 100.0)
    verifier = SupabaseStaffJWTVerifier(
        ISSUER, jwks_fetcher=lambda url: requests.append(url) or {"keys": [public]}
    )
    token = _token(private)

    assert verifier.verify(token) == SUBJECT
    assert verifier.verify(token) == SUBJECT
    assert len(requests) == 1


def test_cache_expiry_replaces_revoked_key(monkeypatch):
    private, public = _key()
    _, replacement = _key("replacement")
    clock = [100.0]
    responses = iter([{"keys": [public]}, {"keys": [replacement]}])
    monkeypatch.setattr("finledger_platform.supabase_auth.time.monotonic", lambda: clock[0])
    verifier = SupabaseStaffJWTVerifier(ISSUER, jwks_fetcher=lambda _: next(responses))
    token = _token(private)

    assert verifier.verify(token) == SUBJECT
    clock[0] = 401.0
    with pytest.raises(InvalidStaffToken):
        verifier.verify(token)


def test_cache_expiry_fails_closed_when_refresh_fails(monkeypatch):
    private, public = _key()
    clock = [100.0]
    responses = iter([{"keys": [public]}, OSError("offline")])
    monkeypatch.setattr("finledger_platform.supabase_auth.time.monotonic", lambda: clock[0])

    def fetch(_):
        response = next(responses)
        if isinstance(response, OSError):
            raise response
        return response

    verifier = SupabaseStaffJWTVerifier(ISSUER, jwks_fetcher=fetch)
    token = _token(private)
    assert verifier.verify(token) == SUBJECT
    clock[0] = 401.0
    with pytest.raises(InvalidStaffToken):
        verifier.verify(token)


@pytest.mark.parametrize("key_ops", [["sign"], "verify", []])
def test_rejects_non_verification_jwk_key_ops(key_ops):
    private, public = _key()
    public["key_ops"] = key_ops
    verifier = SupabaseStaffJWTVerifier(ISSUER, jwks_fetcher=lambda _: {"keys": [public]})
    with pytest.raises(InvalidStaffToken):
        verifier.verify(_token(private))


@pytest.mark.parametrize("issuer", [
    "http://example-project.supabase.co/auth/v1",
    "https://example-project.supabase.co/other",
    "https://user@example-project.supabase.co/auth/v1",
    "https://example-project.supabase.co/auth/v1?redirect=evil",
])
def test_rejects_unsafe_issuer_configuration(issuer):
    with pytest.raises(ValueError):
        SupabaseStaffJWTVerifier(issuer, jwks_fetcher=lambda _: {"keys": []})
