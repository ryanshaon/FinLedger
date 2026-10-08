"""The staging proof tools themselves: isolation-check must pass on a correct schema, catch a leak, and write nothing."""
from __future__ import annotations

from urllib.parse import unquote, urlparse

import psycopg
import pytest

from finledger_platform import isolation_check, s3_smoke
from finledger_platform.store import S3Store


def test_isolation_check_passes_and_leaves_nothing(owner_dsn, app_dsn):
    rep = isolation_check.run(owner_dsn)
    assert rep.ok, rep.failed
    assert len(rep.passed) >= 12
    with psycopg.connect(owner_dsn) as c:
        assert c.execute("select count(*) from firms where name like 'iso-%'").fetchone()[0] == 0


def test_isolation_check_catches_a_disabled_policy(owner_dsn, app_dsn):
    with psycopg.connect(owner_dsn, autocommit=True) as c:
        c.execute("alter table vendors disable row level security")
        try:
            rep = isolation_check.run(owner_dsn)
        finally:
            c.execute("alter table vendors enable row level security")
    assert not rep.ok
    assert "client A context: cannot update client B vendors" in rep.failed


def test_s3_smoke_roundtrip_and_cleanup(monkeypatch):
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    with moto.mock_aws():
        s3 = boto3.client("s3", region_name="ap-south-1")
        s3.create_bucket(Bucket="fl-docs", CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
        store = S3Store("fl-docs", client=s3)

        def fake_get(url):  # moto intercepts botocore, not urllib: serve presigned URLs from the mock bucket
            if "X-Amz-Signature" not in url:
                return 403, b""
            key = unquote(urlparse(url).path).lstrip("/").removeprefix("fl-docs/")  # virtual- or path-style
            return 200, store.get(key)

        monkeypatch.setattr(s3_smoke, "_http_get", fake_get)
        rep = s3_smoke.run(store, "https://ref.supabase.co/storage/v1/s3")
        assert rep.ok, rep.failed
        assert s3.list_objects_v2(Bucket="fl-docs").get("KeyCount", 0) == 0
