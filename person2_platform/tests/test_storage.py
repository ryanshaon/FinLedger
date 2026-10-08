"""Weeks 2 + 4: masters write path, CSV stub, ERP link / credentials, mapping, ageing storage, S3 store."""
from __future__ import annotations

import pytest
from conftest import auth
from cryptography.fernet import Fernet
from test_link import drain, jobs

from finledger_platform import tenancy
from finledger_platform.db import tenant
from finledger_platform.config import Settings
from finledger_platform.store import S3Store, check_key, make_store, object_key


def pair(client, world, c=None):
    c = c or world.a
    r = client.post(f"/clients/{c['id']}/erp/pair", headers=auth(world.token1),
                    json={"erp_type": "tally", "company_name": "Acme Steel (FY 26-27)"})
    assert r.status_code == 201
    return auth(r.json()["agent_token"])


def test_agent_writes_masters_and_vendors(world, client, conn):
    agent = pair(client, world)
    r = client.post("/agent/masters", headers=agent, json={"kind": "ledger", "items": [
        {"name": "Purchase - Raw Material", "parent": "Purchase Accounts"}, {"name": "Input CGST", "parent": "Duties & Taxes"}]})
    assert r.status_code == 201 and r.json()["items"] == 2
    client.post("/agent/masters", headers=agent, json={"kind": "vendor", "items": [
        {"name": "Shree Ganesh Metals", "gstin": "27AAPFU0939F1ZV", "msme": True},
        {"name": "No GSTIN Kirana"}, {"name": "Typo GSTIN", "gstin": "27AAPFU0939F1ZX"}]})
    with tenant(conn, world.a["id"]):
        snap = tenancy.latest_masters(conn, "ledger")
        vendors = conn.execute("select name, gstin, pan, state_code, msme from vendors").fetchall()
    assert snap["item_count"] == 2
    assert vendors == [{"name": "Shree Ganesh Metals", "gstin": "27AAPFU0939F1ZV", "pan": "AAPFU0939F",
                        "state_code": "27", "msme": True}]
    with tenant(conn, world.b["id"]):
        assert tenancy.latest_masters(conn, "ledger") is None  # client-scoped


def test_agent_token_is_per_client_and_rotates(world, client):
    old = pair(client, world)
    new = pair(client, world)
    assert client.post("/agent/masters", headers=old, json={"kind": "ledger", "items": []}).status_code == 401
    assert client.post("/agent/masters", headers=new, json={"kind": "ledger", "items": []}).status_code == 201
    assert client.post("/agent/masters", headers=auth("fla_nope"), json={"kind": "ledger", "items": []}).status_code == 401
    # only firm admins pair
    assert client.post(f"/clients/{world.a['id']}/erp/pair", headers=auth(world.clerk_token), json={}).status_code == 403


def test_open_bills_replace(world, client, conn):
    agent = pair(client, world)
    bill = {"party_ledger": "Shree Ganesh Metals", "bill_ref": "INV-1", "bill_date": "2026-08-01",
            "due_date": "2026-09-15", "amount": 118000, "pending_amount": 118000}
    client.post("/agent/open-bills", headers=agent, json={"as_of": "2026-09-28T10:00:00+05:30", "bills": [bill, {**bill, "bill_ref": "INV-2"}]})
    r = client.post("/agent/open-bills", headers=agent, json={"as_of": "2026-09-29T10:00:00+05:30", "bills": [bill]})
    assert r.json()["open_bills"] == 1
    with tenant(conn, world.a["id"]):
        rows = conn.execute("select bill_ref, pending_amount, due_date::text from open_bills").fetchall()
    assert rows == [{"bill_ref": "INV-1", "pending_amount": 118000, "due_date": "2026-09-15"}]


def test_mapping_upsert(world, client, conn):
    agent = pair(client, world)
    for value in ("Ganesh Metals", "Shree Ganesh Metals"):
        assert client.put("/agent/mapping", headers=agent,
                          json={"kind": "party", "our_key": "27AAPFU0939F1ZV", "erp_value": value}).status_code == 200
    with tenant(conn, world.a["id"]):
        assert conn.execute("select erp_value from mapping_table").fetchall() == [{"erp_value": "Shree Ganesh Metals"}]


def test_erp_credentials_encrypted_at_rest(world, conn, owner):
    key = Fernet.generate_key().decode()
    with tenant(conn, world.a["id"]):
        tenancy.pair_erp_agent(conn, world.a["id"], "zoho")
        tenancy.put_erp_credentials(conn, world.a["id"], b'{"refresh_token":"zoho-secret"}', key)
        assert tenancy.get_erp_credentials(conn, world.a["id"], key) == b'{"refresh_token":"zoho-secret"}'
    raw = owner.execute("select credentials_enc from erp_links").fetchone()[0]
    assert b"zoho-secret" not in bytes(raw)
    with pytest.raises(RuntimeError):
        tenancy.put_erp_credentials(conn, world.a["id"], b"x", "")


def test_csv_door_one_document_per_row(world, client, conn, store, settings):
    csv = b"vendor,invoice_no,date,total\nGanesh Metals,INV-1,2026-09-01,118000\n,,,\nRaj Traders,R-9,2026-09-02,5900\n"
    r = client.post(f"/clients/{world.a['id']}/csv", headers=auth(world.clerk_token),
                    files={"file": ("opening_bills.csv", csv, "text/csv")})
    assert r.status_code == 201 and len(r.json()["received"]) == 2
    drain(conn, store, settings)
    payloads = [j["payload"] for j in jobs(conn, world.a["id"], "extract")]
    assert {p["source_channel"] for p in payloads} == {"csv"} and {p["document_class"] for p in payloads} == {"csv_row"}
    md = store.get(payloads[1]["raw_markdown_path"]).decode()
    assert "Raj Traders" in md and "invoice_no" in md and "Ganesh" not in md
    assert client.post(f"/clients/{world.a['id']}/csv", headers=auth(world.clerk_token),
                       files={"file": ("x.csv", b"only,header\n", "text/csv")}).status_code == 422


def test_object_keys_are_strict(world):
    from datetime import datetime

    k = object_key(world.a["id"], world.a["id"], datetime(2026, 9, 28), "pages/p001.png")
    assert k.startswith(f"{world.a['id']}/2026/09/")
    for bad in ("raw.pdf", f"{world.a['id']}/2026/09/../x", "../../etc/passwd", f"x/2026/09/{world.a['id']}/raw.pdf"):
        with pytest.raises(ValueError):
            check_key(bad)


def test_s3_store_roundtrip():
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    with moto.mock_aws():
        s3 = boto3.client("s3", region_name="ap-south-1")
        s3.create_bucket(Bucket="fl-docs", CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
        st = S3Store("fl-docs", client=s3)
        key = "11111111-1111-1111-1111-111111111111/2026/09/22222222-2222-2222-2222-222222222222/raw.pdf"
        st.put(key, b"%PDF-1.7 hi", "application/pdf")
        assert st.get(key) == b"%PDF-1.7 hi"
        assert s3.head_object(Bucket="fl-docs", Key=key)["ServerSideEncryption"] == "AES256"
        assert "X-Amz-Signature" in st.signed_url(key, 60, "inv.pdf")
        with pytest.raises(ValueError):
            st.put("global/raw.pdf", b"x", "application/pdf")


def test_supabase_s3_uses_project_endpoint_path_style_and_no_sse(monkeypatch):
    from urllib.parse import parse_qs, urlparse
    from botocore.stub import Stubber

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret-key")
    monkeypatch.setenv("FINLEDGER_S3_ENDPOINT_URL", "https://project-ref.storage.supabase.co/storage/v1/s3")
    monkeypatch.setenv("FINLEDGER_S3_REGION", "ap-south-1")
    monkeypatch.setenv("FINLEDGER_S3_ADDRESSING_STYLE", "path")
    settings = Settings(
        database_url="postgresql://unused", signing_secret=b"test", inbound_webhook_secret=b"test",
        store="s3", s3_bucket="documents",
    )
    store = make_store(settings)
    key = "11111111-1111-1111-1111-111111111111/2026/09/22222222-2222-2222-2222-222222222222/raw.pdf"
    with Stubber(store.s3) as stub:
        stub.add_response("put_object", {"ETag": '"test"'}, {
            "Bucket": "documents", "Key": key, "Body": b"pdf", "ContentType": "application/pdf",
        })
        store.put(key, b"pdf", "application/pdf")
        stub.assert_no_pending_responses()

    url = urlparse(store.signed_url(key, 60))
    assert url.netloc == "project-ref.storage.supabase.co"
    assert url.path == f"/storage/v1/s3/documents/{key}"
    assert parse_qs(url.query)["X-Amz-Credential"][0].split("/")[2] == "ap-south-1"
    assert "X-Amz-Signature" in parse_qs(url.query)


@pytest.mark.parametrize("sse", ["auto", "AES256"])
def test_minio_endpoint_defaults_to_path_style_and_supports_sse(monkeypatch, sse):
    from urllib.parse import urlparse
    from botocore.stub import Stubber

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret-key")
    store = S3Store("documents", endpoint_url="http://127.0.0.1:9000", region="us-east-1",
                    server_side_encryption=sse)
    key = "11111111-1111-1111-1111-111111111111/2026/09/22222222-2222-2222-2222-222222222222/raw.pdf"
    expected = {"Bucket": "documents", "Key": key, "Body": b"pdf", "ContentType": "application/pdf"}
    if sse == "AES256":
        expected["ServerSideEncryption"] = "AES256"
    with Stubber(store.s3) as stub:
        stub.add_response("put_object", {"ETag": '"test"'}, expected)
        store.put(key, b"pdf", "application/pdf")
        stub.assert_no_pending_responses()
    assert urlparse(store.signed_url(key, 60)).path == f"/documents/{key}"


def test_local_store_survives_long_paths(tmp_path):
    from finledger_platform.store import LocalStore

    deep = tmp_path / ("d" * 60) / ("e" * 60) / ("f" * 60)  # root + key well past Windows' 260-char MAX_PATH
    st = LocalStore(str(deep), b"s", "http://x")
    key = "11111111-1111-1111-1111-111111111111/2026/09/22222222-2222-2222-2222-222222222222/pages/p001.png"
    st.put(key, b"png", "image/png")
    assert st.get(key) == b"png"
