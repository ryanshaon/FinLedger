"""Storage proof failures must not hide cleanup or claim bucket privacy."""

from urllib.parse import unquote, urlparse

import pytest

from finledger_platform import s3_smoke
from finledger_platform.cli import _report
from finledger_platform.store import S3Store

ENDPOINT = "https://synthetic.supabase.co/storage/v1/s3"
CANARY = "private-signature-and-invoice-canary"


@pytest.fixture
def smoke_store(monkeypatch):
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    with moto.mock_aws():
        backend = boto3.client("s3", region_name="ap-south-1")
        backend.create_bucket(Bucket="smoke-docs", CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
        store = S3Store("smoke-docs", client=backend)

        def get(url):
            if "X-Amz-Signature" not in url:
                return 403, b""
            key = unquote(urlparse(url).path).lstrip("/").removeprefix("smoke-docs/")
            return 200, store.get(key)

        monkeypatch.setattr(s3_smoke, "_http_get", get)
        yield store


@pytest.mark.parametrize("status", [400, 429, 500, 503])
def test_service_errors_are_not_proof_of_public_access_denial(smoke_store, monkeypatch, status):
    original = s3_smoke._http_get
    monkeypatch.setattr(s3_smoke, "_http_get",
                        lambda url: original(url) if "X-Amz-Signature" in url else (status, b""))
    report = s3_smoke.run(smoke_store, ENDPOINT)
    assert not report.ok
    assert any("unsigned public" in label for label in report.failed)
    assert smoke_store.s3.list_objects_v2(Bucket=smoke_store.bucket)["KeyCount"] == 0


@pytest.mark.parametrize("stage", ["put", "get", "signed_url", "presigned_get", "public_get"])
def test_smoke_step_failure_returns_safe_report_and_cleanup(smoke_store, monkeypatch, capsys, stage):
    def fail(*args, **kwargs):
        raise RuntimeError(CANARY)

    if stage == "put":
        original = smoke_store.put

        def uncertain_put(*args, **kwargs):
            original(*args, **kwargs)  # simulate acceptance followed by a lost response
            fail()

        monkeypatch.setattr(smoke_store, "put", uncertain_put)
    elif stage in ("get", "signed_url"):
        monkeypatch.setattr(smoke_store, stage, fail)
    else:
        original = s3_smoke._http_get

        def get(url):
            signed = "X-Amz-Signature" in url
            if signed == (stage == "presigned_get"):
                fail()
            return original(url)

        monkeypatch.setattr(s3_smoke, "_http_get", get)
    report = s3_smoke.run(smoke_store, ENDPOINT)
    assert not report.ok
    assert "deleted smoke object" in report.passed
    assert smoke_store.s3.list_objects_v2(Bucket=smoke_store.bucket)["KeyCount"] == 0
    with pytest.raises(SystemExit) as result:
        _report(report)
    assert result.value.code == 1
    output = capsys.readouterr().out
    assert "deleted smoke object" in output and CANARY not in output


def test_cleanup_failure_is_reported_without_sensitive_exception(smoke_store, monkeypatch, capsys):
    def fail(**kwargs):
        raise RuntimeError(CANARY)

    monkeypatch.setattr(smoke_store.s3, "delete_object", fail)
    report = s3_smoke.run(smoke_store, ENDPOINT)
    assert not report.ok
    assert any("delete smoke object" in label for label in report.failed)
    assert smoke_store.s3.list_objects_v2(Bucket=smoke_store.bucket)["KeyCount"] == 1
    with pytest.raises(SystemExit):
        _report(report)
    assert CANARY not in capsys.readouterr().out
