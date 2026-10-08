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
