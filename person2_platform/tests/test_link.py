"""Acceptance: upload via link -> documents row + extract job; ZIP of two -> two docs; virus never reaches extract;
same bytes -> same hash. Plus file prep for P1: markdown, layout blocks, page images."""
from __future__ import annotations

import json

from conftest import auth, invoice_pdf, photo_jpg, scanned_pdf, statement_xlsx, zip_of

from finledger_platform.db import tenant
from finledger_platform.virus import EICAR
from finledger_platform.worker import load_extract_input, run_once

EXTRACT_KEYS = {"document_id", "client_id", "source_channel", "document_class", "mime", "content_hash", "raw_path",
                "raw_markdown_path", "layout_blocks_path", "page_image_paths", "page_count", "is_scanned",
                "phish_flag", "po_number"}


def send(client, token, *files, headers=None, **form):
    return client.post(f"/i/{token}", files=[("files", f) for f in files], data=form, headers=headers or {})


def drain(conn, store, settings):
    out = []
    while (r := run_once(conn, store, settings)) is not None:
        out.append(r)
    return out


def jobs(conn, client_id, queue):
    with tenant(conn, client_id):
        return conn.execute("select * from jobs where queue = %s order by id", (queue,)).fetchall()


def test_upload_form_renders(world, client):
    r = client.get(f"/i/{world.a['public_token']}")
    assert r.status_code == 200
    assert "Send a bill to Acme Steel Pvt Ltd" in r.text
    assert "invoices.acme-steel@inbound.finledger.test" in r.text
    assert client.get("/i/not-a-real-token").status_code == 404


def test_link_upload_creates_document_and_extract_job(world, client, conn, store, settings):
    r = send(client, world.a["public_token"], ("inv.pdf", invoice_pdf(), "application/pdf"), po_number="PO-77", note="Sept")
    assert r.status_code == 201
    doc_id = r.json()["received"][0]["document_id"]
    with tenant(conn, world.a["id"]):
        doc = conn.execute("select * from documents where id = %s", (doc_id,)).fetchone()
    assert doc["channel"] == "link" and doc["status"] == "received" and doc["virus_ok"] is None
    assert doc["po_number"] == "PO-77" and doc["vendor_note"] == "Sept"
    assert doc["source_meta"]["ip"]
    assert [j["state"] for j in jobs(conn, world.a["id"], "ingest")] == ["queued"]

    assert drain(conn, store, settings) == ["prepared"]
    [job] = jobs(conn, world.a["id"], "extract")
    assert job["state"] == "queued" and set(job["payload"]) == EXTRACT_KEYS
    p = job["payload"]
    assert p["document_id"] == doc_id and p["is_scanned"] is False and p["page_image_paths"] == []
    assert p["po_number"] == "PO-77" and p["page_count"] == 1

    full = load_extract_input(conn, store, job)
    assert "INV-2026-0042" in full["raw_markdown"] and "27AAACS1234A1Z1" in full["raw_markdown"]
    blocks = full["layout_blocks"]["blocks"]
    assert any(b["type"] == "line" and "Grand Total" in b["text"] for b in blocks)
    assert all(len(b["bbox"]) == 4 and b["page"] == 1 for b in blocks)
    with tenant(conn, world.a["id"]):
        doc = conn.execute("select * from documents where id = %s", (doc_id,)).fetchone()
    assert doc["virus_ok"] is True and doc["prepared_at"] is not None and doc["status"] == "received"


def test_same_bytes_same_hash(world, client):
    pdf = invoice_pdf()
    h1 = send(client, world.a["public_token"], ("a.pdf", pdf, "application/pdf")).json()["received"][0]["content_hash"]
    h2 = send(client, world.a["public_token"], ("b.pdf", pdf, "application/pdf")).json()["received"][0]["content_hash"]
    h3 = send(client, world.a["public_token"], ("c.pdf", invoice_pdf("INV-9"), "application/pdf")).json()["received"][0]
    assert h1 == h2 != h3["content_hash"]


def test_zip_of_two_pdfs_is_two_documents(world, client, conn, store, settings):
    z = zip_of({"one.pdf": invoice_pdf("A-1"), "sub/two.pdf": invoice_pdf("A-2"), "__MACOSX/._one.pdf": b"x",
                "readme.txt": b"hello", "inner.zip": zip_of({"x.pdf": invoice_pdf()})})
    r = send(client, world.a["public_token"], ("bills.zip", z, "application/zip"))
    body = r.json()
    assert r.status_code == 201 and len(body["received"]) == 2
    assert {x["filename"] for x in body["rejected"]} == {"readme.txt", "inner.zip"}
    assert drain(conn, store, settings) == ["prepared", "prepared"]
    assert len(jobs(conn, world.a["id"], "extract")) == 2
    with tenant(conn, world.a["id"]):
        metas = [r["source_meta"] for r in conn.execute("select source_meta from documents")]
    assert all(m["archive"] == "bills.zip" for m in metas)


def test_virus_never_reaches_extract(world, client, conn, store, settings):
    infected = invoice_pdf() + b"\n" + EICAR
    r = send(client, world.a["public_token"], ("evil.pdf", infected, "application/pdf"))
    doc_id = r.json()["received"][0]["document_id"]
    assert drain(conn, store, settings) == ["quarantined"]
    assert jobs(conn, world.a["id"], "extract") == []
    with tenant(conn, world.a["id"]):
        doc = conn.execute("select * from documents where id = %s", (doc_id,)).fetchone()
    assert doc["status"] == "quarantined" and doc["virus_ok"] is False and "Eicar" in doc["status_reason"]
    r = client.get(f"/clients/{world.a['id']}/documents/{doc_id}/raw-url", headers=auth(world.token1))
    assert r.status_code == 409


def test_scanned_pdf_gets_page_images(world, client, conn, store, settings):
    send(client, world.a["public_token"], ("scan.pdf", scanned_pdf(), "application/pdf"))
    drain(conn, store, settings)
    [job] = jobs(conn, world.a["id"], "extract")
    p = job["payload"]
    assert p["is_scanned"] is True and p["raw_markdown_path"] is None
    assert len(p["page_image_paths"]) == 1 and p["page_image_paths"][0].endswith("/pages/p001.png")
    assert store.get(p["page_image_paths"][0])[:8] == b"\x89PNG\r\n\x1a\n"
    assert json.loads(store.get(p["layout_blocks_path"]))["pages"][0]["has_text"] is False


def test_photo_becomes_page_image(world, client, conn, store, settings):
    send(client, world.a["public_token"], ("IMG_2031.jpg", photo_jpg(), "image/jpeg"))
    drain(conn, store, settings)
    [job] = jobs(conn, world.a["id"], "extract")
    assert job["payload"]["is_scanned"] and len(job["payload"]["page_image_paths"]) == 1


def test_xlsx_is_flagged_statement(world, client, conn, store, settings):
    send(client, world.a["public_token"], ("stmt.xlsx", statement_xlsx(), "application/octet-stream"))
    drain(conn, store, settings)
    [job] = jobs(conn, world.a["id"], "extract")
    assert job["payload"]["document_class"] == "statement"
    assert "INV-1" in load_extract_input(conn, store, job)["raw_markdown"]


def test_content_type_lies_are_ignored(world, client):
    r = send(client, world.a["public_token"], ("invoice.pdf", b"MZ\x90\x00 definitely an exe", "application/pdf"))
    assert r.status_code == 422 and r.json()["received"] == []


def test_corrupt_pdf_goes_to_exception_not_extract(world, client, conn, store, settings):
    r = send(client, world.a["public_token"], ("broken.pdf", b"%PDF-1.7\n garbage garbage", "application/pdf"))
    doc_id = r.json()["received"][0]["document_id"]
    assert drain(conn, store, settings) == ["exception"]
    assert jobs(conn, world.a["id"], "extract") == []
    with tenant(conn, world.a["id"]):
        doc = conn.execute("select status, status_reason from documents where id = %s", (doc_id,)).fetchone()
    assert doc["status"] == "exception" and "unreadable PDF" in doc["status_reason"]


def test_rate_limit_per_token(world, client, settings):
    object.__setattr__(settings, "rate_per_token_hour", 2)
    codes = [send(client, world.a["public_token"], ("a.pdf", invoice_pdf(), "application/pdf")).status_code
             for _ in range(3)]
    assert codes == [201, 201, 429]
    # a different client's link is unaffected
    assert send(client, world.c["public_token"], ("a.pdf", invoice_pdf(), "application/pdf")).status_code == 201


def test_rate_limit_per_ip(world, client, settings):
    object.__setattr__(settings, "rate_per_ip_hour", 1)
    assert send(client, world.a["public_token"], ("a.pdf", invoice_pdf(), "application/pdf")).status_code == 201
    assert send(client, world.c["public_token"], ("a.pdf", invoice_pdf(), "application/pdf")).status_code == 429


def test_upload_size_limit(world, client, settings):
    object.__setattr__(settings, "max_upload_bytes", 1000)
    assert send(client, world.a["public_token"], ("a.pdf", invoice_pdf(), "application/pdf")).status_code == 413


def test_rotated_token_kills_old_link(world, client):
    old = world.a["public_token"]
    r = client.post(f"/clients/{world.a['id']}/rotate-token", headers=auth(world.token1))
    new = r.json()["upload_link"].rsplit("/", 1)[1]
    assert new != old
    assert client.get(f"/i/{old}").status_code == 404
    assert send(client, old, ("a.pdf", invoice_pdf(), "application/pdf")).status_code == 404
    assert send(client, new, ("a.pdf", invoice_pdf(), "application/pdf")).status_code == 201


def test_browser_gets_receipt_page(world, client):
    r = send(client, world.a["public_token"], ("inv.pdf", invoice_pdf(), "application/pdf"),
             ("notes.txt", b"hi", "text/plain"), headers={"Accept": "text/html"})
    assert r.status_code == 201 and "text/html" in r.headers["content-type"]
    assert "RECEIVED" in r.text and "Acme Steel Pvt Ltd has your bill" in r.text and "notes.txt" in r.text


def test_signed_raw_url(world, client, store, monkeypatch):
    doc = send(client, world.a["public_token"], ("inv.pdf", invoice_pdf(), "application/pdf")).json()["received"][0]
    r = client.get(f"/clients/{world.a['id']}/documents/{doc['document_id']}/raw-url", headers=auth(world.clerk_token))
    url = r.json()["url"]
    got = client.get(url.replace("http://testserver", ""))
    assert got.status_code == 200 and got.content[:5] == b"%PDF-" and got.headers["content-type"] == "application/pdf"
    tampered = url[:-1] + ("0" if url[-1] != "0" else "1")
    assert client.get(tampered.replace("http://testserver", "")).status_code == 403
    import time

    issued_at = time.time()
    expired = store.signed_url(store.verify_token(url.rsplit("/", 1)[1])[0], ttl=1)
    monkeypatch.setattr("finledger_platform.store.time.time", lambda: issued_at + 2)
    assert client.get(expired.replace("http://testserver", "")).status_code == 403


def test_documents_list_filters(world, client, conn, store, settings):
    send(client, world.a["public_token"], ("a.pdf", invoice_pdf(), "application/pdf"))
    send(client, world.a["public_token"], ("v.pdf", invoice_pdf() + EICAR, "application/pdf"))
    drain(conn, store, settings)
    base = f"/clients/{world.a['id']}/documents"
    assert len(client.get(base, headers=auth(world.token1)).json()) == 2
    q = client.get(base, params={"status": "quarantined"}, headers=auth(world.token1)).json()
    assert [d["original_filename"] for d in q] == ["v.pdf"]
    assert client.get(base, params={"channel": "email"}, headers=auth(world.token1)).json() == []
    detail = client.get(f"{base}/{q[0]['id']}", headers=auth(world.token1)).json()
    assert [a["kind"] for a in detail["assets"]] == ["raw"] and detail["jobs"][0]["queue"] == "ingest"
