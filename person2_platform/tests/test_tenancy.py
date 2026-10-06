"""Acceptance: two clients cannot read each other's files, rows, or embeddings. Object path contains client_id."""
from __future__ import annotations

import psycopg
import pytest
from conftest import auth, invoice_pdf

from finledger_platform import tenancy
from finledger_platform.db import UnsafeRoleError, make_pool, tenant
from finledger_platform.worker import run_once


def upload(client, token, name="bill.pdf", data=None):
    r = client.post(f"/i/{token}", files=[("files", (name, data or invoice_pdf(), "application/pdf"))])
    assert r.status_code == 201, r.text
    return r.json()["received"][0]["document_id"]


def test_app_refuses_roles_that_bypass_rls(owner_dsn):
    with pytest.raises(UnsafeRoleError):
        make_pool(owner_dsn, min_size=1, max_size=1)


def test_no_tenant_set_means_no_rows(world, client, conn):
    upload(client, world.a["public_token"])
    with conn.transaction():
        assert conn.execute("select count(*) as n from documents").fetchone()["n"] == 0
        assert conn.execute("select count(*) as n from jobs").fetchone()["n"] == 0


def test_rows_do_not_cross_clients(world, client, conn, store, settings):
    doc_a = upload(client, world.a["public_token"])
    doc_b = upload(client, world.b["public_token"])
    while run_once(conn, store, settings):
        pass
    for table in ("documents", "document_assets", "jobs"):
        with tenant(conn, world.b["id"]):
            rows = conn.execute(f"select * from {table}").fetchall()
        assert rows and all(r["client_id"] == world.b["id"] for r in rows), table
    with tenant(conn, world.b["id"]):
        assert conn.execute("select * from documents where id = %s", (doc_a,)).fetchone() is None
        # Updates to the other client's rows silently touch nothing.
        n = conn.execute("update documents set status = 'rejected' where id = %s", (doc_a,)).rowcount
        assert n == 0
    with tenant(conn, world.a["id"]):
        assert conn.execute("select status from documents where id = %s", (doc_a,)).fetchone()["status"] == "received"
        assert conn.execute("select 1 from documents where id = %s", (doc_b,)).fetchone() is None


def test_cannot_write_rows_for_another_client(world, conn):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant(conn, world.b["id"]):
            conn.execute("insert into rag_chunks (client_id, chunk_type, content) values (%s, 'ledger', 'x')",
                         (world.a["id"],))


def test_composite_foreign_key_rejects_cross_tenant_document_reference(world, client, owner):
    doc_a = upload(client, world.a["public_token"])
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        owner.execute("""insert into document_assets(client_id,document_id,kind,path,page_no)
                         values(%s,%s,'page_image',%s,99)""",
                      (world.b["id"], doc_a, f"{world.b['id']}/cross-tenant.pdf"))


def test_general_app_role_has_no_queue_claim_privilege(owner):
    assert not owner.execute("select has_function_privilege('finledger_app','claim_job(text,integer)','execute')").fetchone()[0]
    assert owner.execute("select has_function_privilege('finledger_worker_ingest','claim_job(text,integer)','execute')").fetchone()[0]


def test_embeddings_do_not_cross_clients(world, conn):
    for c, text in ((world.a, "Purchase - Raw Material"), (world.b, "Purchase - Yarn")):
        with tenant(conn, c["id"]):
            conn.execute("insert into rag_chunks (client_id, chunk_type, content, embedding) values (%s, 'ledger', %s, %s)",
                         (c["id"], text, "{0.1,0.2,0.3}"))
    with tenant(conn, world.b["id"]):
        contents = [r["content"] for r in conn.execute("select content from rag_chunks").fetchall()]
    assert contents == ["Purchase - Yarn"]


def test_object_path_contains_client_id(world, client, conn, store, settings):
    doc = upload(client, world.a["public_token"])
    run_once(conn, store, settings)
    with tenant(conn, world.a["id"]):
        d = conn.execute("select object_path, received_at from documents where id = %s", (doc,)).fetchone()
        paths = [r["path"] for r in conn.execute("select path from document_assets where document_id = %s", (doc,))]
    assert d["object_path"] == f"{world.a['id']}/{d['received_at']:%Y}/{d['received_at']:%m}/{doc}/raw.pdf"
    assert paths and all(p.startswith(f"{world.a['id']}/") for p in paths)


def test_database_rejects_path_without_client_prefix(world, conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        with tenant(conn, world.a["id"]):
            conn.execute("""insert into documents (client_id, channel, object_path, content_hash, mime, size_bytes)
                            values (%s, 'link', 'global-bucket/x.pdf', repeat('a', 64), 'application/pdf', 1)""",
                         (world.a["id"],))


def test_staff_of_other_firm_cannot_see_client(world, client):
    doc = upload(client, world.a["public_token"])
    base = f"/clients/{world.a['id']}"
    for path in (base, f"{base}/documents", f"{base}/documents/{doc}", f"{base}/documents/{doc}/raw-url"):
        assert client.get(path, headers=auth(world.token2)).status_code == 404, path
    assert client.get(f"{base}/documents", headers=auth(world.token1)).status_code == 200


def test_member_sees_only_assigned_client(world, client):
    r = client.get("/clients", headers=auth(world.clerk_token))
    assert [c["name"] for c in r.json()] == ["Acme Steel Pvt Ltd"]
    assert client.get(f"/clients/{world.c['id']}", headers=auth(world.clerk_token)).status_code == 404
    got = client.get(f"/clients/{world.a['id']}", headers=auth(world.clerk_token)).json()
    assert got["my_roles"] == ["ap_clerk"]
    # clerks cannot change policy or rotate the link
    assert client.patch(f"/clients/{world.a['id']}/policy", json={"auto_post_cap": 1},
                        headers=auth(world.clerk_token)).status_code == 403


def test_admin_lists_only_own_firm(world, client):
    names = sorted(c["name"] for c in client.get("/clients", headers=auth(world.token1)).json())
    assert names == ["Acme Steel Pvt Ltd", "Chai Point Traders"]


def test_bad_token_401(client, world):
    assert client.get("/clients", headers=auth("flu_nope")).status_code == 401
    assert client.get("/clients").status_code == 401


def test_create_client_publishes_both_doors(world, client):
    r = client.post("/clients", headers=auth(world.token1), json={
        "name": "Delta Pharma", "gstins": ["27AAPFU0939F1ZV"], "policy": {"auto_post_cap": 50000, "po_required": True}})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["inbound_email"] == "invoices.delta-pharma@inbound.finledger.test"
    assert c["upload_link"] == f"http://testserver/i/{c['public_token']}"
    assert c["auto_post_cap"] == "50000.00" or float(c["auto_post_cap"]) == 50000
    assert client.post("/clients", headers=auth(world.token1), json={"name": "X", "inbound_slug": "delta-pharma"}
                       ).status_code in (409, 422)
    assert client.post("/clients", headers=auth(world.token1),
                       json={"name": "Bad GST", "gstins": ["27AAPFU0939F1ZX"]}).status_code == 422
    assert client.post("/clients", headers=auth(world.token1),
                       json={"name": "Bad policy", "policy": {"match_mode": "vibes"}}).status_code == 422


def test_policy_update(world, client):
    r = client.patch(f"/clients/{world.a['id']}/policy", headers=auth(world.token1),
                     json={"maker_checker": False, "tds_sections": ["194C", "194J"], "firm_id": "ignored"})
    assert r.status_code == 200
    assert r.json()["tds_sections"] == ["194C", "194J"] and r.json()["maker_checker"] is False


def test_gstin_checksum():
    assert tenancy.gstin_valid("27AAPFU0939F1ZV")
    assert not tenancy.gstin_valid("27AAPFU0939F1ZX")  # wrong check digit
    assert not tenancy.gstin_valid("27AAPFU0939F1Z")   # short
