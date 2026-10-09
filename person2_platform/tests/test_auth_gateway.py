"""Offline boundary tests: Supabase Auth credentials never enter logs or errors."""

import pytest

from finledger_platform.auth_gateway import AuthUnavailable, SupabaseAuthClient


def test_password_login_and_refresh_use_only_configured_auth_origin():
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return {"access_token": "access", "refresh_token": "refresh", "expires_in": 900}

    auth = SupabaseAuthClient("https://project.supabase.co", "publishable-test", transport=transport)
    pair = auth.sign_in("staff@example.test", "private-password")
    assert (pair.access_token, pair.refresh_token) == ("access", "refresh")
    assert calls[0][0:2] == ("POST", "https://project.supabase.co/auth/v1/token?grant_type=password")
    assert calls[0][2]["apikey"] == "publishable-test"
    assert calls[0][3] == {"email": "staff@example.test", "password": "private-password"}

    auth.refresh(pair.refresh_token)
    assert calls[1][1] == "https://project.supabase.co/auth/v1/token?grant_type=refresh_token"
    assert calls[1][3] == {"refresh_token": "refresh"}
    auth.sign_out(pair.access_token)
    assert calls[2][1] == "https://project.supabase.co/auth/v1/logout?scope=local"
    assert calls[2][2]["Authorization"] == "Bearer access"


def test_auth_errors_are_sanitized():
    def fail(*_):
        raise OSError("private-password and token value in transport error")

    auth = SupabaseAuthClient("https://project.supabase.co", "publishable-test", transport=fail)
    with pytest.raises(AuthUnavailable) as error:
        auth.sign_in("staff@example.test", "private-password")
    assert "private-password" not in str(error.value)
    assert "transport error" not in str(error.value)


@pytest.mark.parametrize("url", ["http://project.supabase.co", "https://project.supabase.co/other",
                                  "https://evil.test@project.supabase.co", "https://project.supabase.co?q=x"])
def test_rejects_unsafe_auth_base_url(url):
    with pytest.raises(ValueError):
        SupabaseAuthClient(url, "publishable-test")


def test_rejects_missing_tokens_without_exposing_response():
    auth = SupabaseAuthClient("https://project.supabase.co", "publishable-test",
                              transport=lambda *_: {"error": "secret-response"})
    with pytest.raises(AuthUnavailable) as error:
        auth.sign_in("staff@example.test", "private-password")
    assert "secret-response" not in str(error.value)


def test_totp_factor_enrollment_challenge_and_verify_contract():
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        if url.endswith("/user"):
            return {"factors": [{"id": "11111111-1111-1111-1111-111111111111", "factor_type": "totp", "status": "verified"}]}
        if url.endswith("/factors"):
            return {"id": "22222222-2222-2222-2222-222222222222", "totp": {"secret": "TESTSECRET"}}
        if url.endswith("/challenge"):
            return {"id": "33333333-3333-3333-3333-333333333333"}
        return {"access_token": "aal2-access", "refresh_token": "new-refresh", "expires_in": 900}

    auth = SupabaseAuthClient("https://project.supabase.co", "publishable-test", transport=transport)
    assert len(auth.verified_totp_factors("aal1-access")) == 1
    factor, secret = auth.enroll_totp("aal1-access")
    assert secret == "TESTSECRET"
    challenge = auth.challenge_totp("aal1-access", factor)
    assert auth.verify_totp("aal1-access", factor, challenge, "123456").access_token == "aal2-access"
    assert calls[0][0] == "GET" and calls[0][1].endswith("/auth/v1/user")
    assert calls[1][3] == {"factor_type": "totp"}
    assert calls[3][3] == {"challenge_id": challenge, "code": "123456"}


def test_invite_redeem_and_password_contract_uses_publishable_key_only():
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return {"access_token": "access", "refresh_token": "refresh", "expires_in": 900} if "verify" in url else {}

    auth = SupabaseAuthClient("https://project.supabase.co", "publishable-test", transport=transport)
    tokens = auth.verify_invite("pkce_0123456789abcdef")
    assert tokens.access_token == "access"
    assert calls[0] == ("POST", "https://project.supabase.co/auth/v1/verify", {"apikey": "publishable-test"},
                        {"type": "invite", "token_hash": "pkce_0123456789abcdef"})
    auth.set_password("access", "a long new password")
    assert calls[1][0:2] == ("PUT", "https://project.supabase.co/auth/v1/user")
    assert calls[1][2] == {"apikey": "publishable-test", "Authorization": "Bearer access"}
    assert calls[1][3] == {"password": "a long new password"}


@pytest.mark.parametrize("token_hash", ["", "short", "x" * 513, "abc def ghij klmnop", "abcdefghijklmnop/../x",
                                        "abcdefghijklmnop?x=1", None])
def test_invite_token_hash_is_validated_before_any_request(token_hash):
    def transport(*_):
        raise AssertionError("must not call upstream")

    with pytest.raises(AuthUnavailable):
        SupabaseAuthClient("https://project.supabase.co", "k", transport=transport).verify_invite(token_hash)
