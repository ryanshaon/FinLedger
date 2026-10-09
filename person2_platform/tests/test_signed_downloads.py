"""Offline signed-download regressions: synthetic HMAC, real route and local files."""
import base64
import hashlib
import hmac
import json
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from finledger_platform.api import create_app
from finledger_platform.store import LocalStore, check_key


KEY = "11111111-1111-1111-1111-111111111111/2026/10/22222222-2222-2222-2222-222222222222/pages/invoice.pdf"
SECRET = b"synthetic-download-test-secret"
NOW = 2000000000


class RecordingStore(LocalStore):
    """Record real reads, including attempts that fail before reaching disk."""

    def __init__(self, root):
        super().__init__(str(root), SECRET, "http://testserver")
        self.reads = []

    def get(self, key):
        self.reads.append(key)
        return super().get(key)


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    monkeypatch.setattr("finledger_platform.store.time.time", lambda: NOW)
    store = RecordingStore(tmp_path / "objects")
    store.put(KEY, b"synthetic invoice", "application/pdf")
    # This route has no DB/settings dependency; None prevents accidental DB use.
    with TestClient(create_app(None, None, store)) as client:
        yield store, client


def signed_body(body):
    signature = hmac.new(SECRET, body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def signed_claims(claims):
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode("ascii")
    return signed_body(body)


def assert_denied(downloads, token):
    store, client = downloads
    response = client.get("/files/" + quote(token, safe=""))
    assert response.status_code == 403
    assert response.json() == {"detail": "link expired or invalid"}
    assert store.reads == []
    assert store.verify_token(token) is None


@pytest.mark.parametrize("token", ["abc.é", "abc." + "g" * 64, "abc." + "0" * 63,
                                   "abc." + "0" * 64, "no-dot", "." + "0" * 64])
def test_invalid_signature_denied_without_read(downloads, token):
    assert_denied(downloads, token)


@pytest.mark.parametrize("body", ["a", "!!!!", "e30=!!", "é", "bm90LWpzb24=", "_w=="])
def test_authentic_malformed_encoding_denied_without_read(downloads, body):
    assert_denied(downloads, signed_body(body))


def test_authentic_base64_with_ignored_garbage_is_denied(downloads):
    body = signed_claims({"k": KEY, "e": NOW + 60}).rpartition(".")[0]
    assert_denied(downloads, signed_body(body + "!"))


def test_boolean_expiry_is_denied_even_before_timestamp_one(downloads, monkeypatch):
    monkeypatch.setattr("finledger_platform.store.time.time", lambda: 0)
    assert_denied(downloads, signed_claims({"k": KEY, "e": True}))


@pytest.mark.parametrize("claims", [None, [], "claims", 42, {}, {"k": KEY}, {"e": NOW + 60}])
def test_authentic_invalid_claim_shape_denied_without_read(downloads, claims):
    assert_denied(downloads, signed_claims(claims))


@pytest.mark.parametrize("expiry", [None, "2000000060", [], {}, True, False,
                                    float("nan"), float("inf"), float("-inf"), NOW, NOW - 1])
def test_invalid_expiry_denied_without_read(downloads, expiry):
    assert_denied(downloads, signed_claims({"k": KEY, "e": expiry}))


@pytest.mark.parametrize("key", [None, [], {}, 42, "../outside.pdf", KEY + "\n",
                                 KEY.replace("pages/", "../"), KEY + "\0"])
def test_invalid_key_denied_without_read(downloads, key):
    assert_denied(downloads, signed_claims({"k": key, "e": NOW + 60}))


@pytest.mark.parametrize("filename", [None, [], {}, 42, True, "\ud800"])
def test_invalid_filename_claim_denied_without_read(downloads, filename):
    assert_denied(downloads, signed_claims({"k": KEY, "e": NOW + 60, "f": filename}))


def test_check_key_rejects_trailing_newline():
    with pytest.raises(ValueError):
        check_key(KEY + "\n")


@pytest.mark.parametrize("ttl", [0, -1, True, False, 1.5, 1.0, float("nan"),
                                 float("inf"), float("-inf"), "60", None])
def test_local_signed_url_rejects_invalid_ttl(downloads, ttl):
    store, _ = downloads
    with pytest.raises(ValueError):
        store.signed_url(KEY, ttl=ttl)


@pytest.mark.parametrize("ttl", [1, 300, 10**12])
def test_local_signed_url_round_trip_with_positive_integer_ttl(downloads, ttl):
    store, client = downloads
    url = store.signed_url(KEY, ttl=ttl, filename="invoice.pdf")
    assert store.verify_token(url.rsplit("/", 1)[1]) == (KEY, "invoice.pdf")
    response = client.get(url)
    assert response.status_code == 200
    assert response.content == b"synthetic invoice"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'inline; filename="invoice.pdf"'
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert store.reads == [KEY]


@pytest.mark.parametrize("expiry", [NOW + 0.5, NOW + 60])
def test_valid_legacy_claim_without_filename_downloads_nested_key(downloads, expiry):
    store, client = downloads
    response = client.get("/files/" + signed_claims({"k": KEY, "e": expiry}))
    assert response.status_code == 200
    assert response.content == b"synthetic invoice"
    assert response.headers["content-disposition"] == 'inline; filename="invoice.pdf"'
    assert store.reads == [KEY]


@pytest.mark.parametrize("filename, encoded", [
    ("कर चालान.pdf", "%E0%A4%95%E0%A4%B0%20%E0%A4%9A%E0%A4%BE%E0%A4%B2%E0%A4%BE%E0%A4%A8.pdf"),
    ('invoice"draft.pdf', "invoice%22draft.pdf"),
    ("invoice\\draft.pdf", "invoice%5Cdraft.pdf"),
    ("invoice\r\nX-Evil: yes\0.pdf", "invoice%0D%0AX-Evil%3A%20yes%00.pdf"),
])
def test_download_filename_encoded_safely(downloads, filename, encoded):
    store, client = downloads
    response = client.get(store.signed_url(KEY, filename=filename))
    assert response.status_code == 200
    assert response.content == b"synthetic invoice"
    assert response.headers["content-disposition"] == "inline; filename*=UTF-8''" + encoded
    assert "x-evil" not in response.headers
