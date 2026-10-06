"""Acceptance: an email with one PDF attachment creates the same shape as the link."""
from __future__ import annotations

import hashlib
import hmac
from email.message import EmailMessage

from conftest import invoice_pdf
from test_link import drain, jobs, send

from finledger_platform.db import tenant

GOOD_AUTH = "mx.finledger.test; spf=pass smtp.mailfrom=ganeshmetals.in; dkim=pass header.d=ganeshmetals.in; dmarc=pass"


def mail(to="invoices.acme-steel@inbound.finledger.test", attachments=(("inv.pdf", invoice_pdf()),),
         auth=GOOD_AUTH, sender="Accounts <accounts@ganeshmetals.in>", msg_id="<m1@ganeshmetals.in>", **headers) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = sender, to, "Invoice INV-2026-0042", msg_id
    if auth:
        m["Authentication-Results"] = auth
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content("Please find attached our invoice.")
    for name, data in attachments:
        m.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    return m.as_bytes()


def post(client, raw: bytes, secret=b"test-webhook-secret", **params):
    sig = "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()
    return client.post("/inbound/email", content=raw, params=params,
                       headers={"X-FinLedger-Signature": sig, "Content-Type": "message/rfc822"})


def outbox(conn, client_id):
    with tenant(conn, client_id):
        return conn.execute("select * from outbox order by id").fetchall()


def test_email_same_shape_as_link(world, client, conn, store, settings):
    pdf = invoice_pdf()
    r = post(client, mail(attachments=[("inv.pdf", pdf)]))
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    send(client, world.a["public_token"], ("inv.pdf", pdf, "application/pdf"))
    drain(conn, store, settings)

    with tenant(conn, world.a["id"]):
        docs = conn.execute("select * from documents order by channel").fetchall()
    email_doc, link_doc = docs
    assert (email_doc["channel"], link_doc["channel"]) == ("email", "link")
    for col in ("content_hash", "mime", "status", "virus_ok", "page_count", "document_class", "size_bytes"):
        assert email_doc[col] == link_doc[col], col
    ex = {j["document_id"]: j["payload"] for j in jobs(conn, world.a["id"], "extract")}
    e, l = ex[email_doc["id"]], ex[link_doc["id"]]
    assert set(e) == set(l) and e["source_channel"] == "email"
    assert {k: v for k, v in e.items() if k.endswith("_path") or k == "page_image_paths"}.keys() == \
           {k for k in l if k.endswith("_path") or k == "page_image_paths"}
    meta = email_doc["source_meta"]
    assert meta["from"] == "accounts@ganeshmetals.in" and meta["sender_domain"] == "ganeshmetals.in"
    assert meta["subject"] == "Invoice INV-2026-0042" and meta["auth"]["dkim"] == "pass"
    assert email_doc["phish_flag"] is False
    [reply] = outbox(conn, world.a["id"])
    assert reply["template"] == "received" and reply["to_addr"] == "accounts@ganeshmetals.in"


def test_bad_signature_rejected(world, client):
    assert post(client, mail(), secret=b"wrong").status_code == 401
    assert client.post("/inbound/email", content=mail()).status_code == 401


def test_unknown_recipient_dropped(world, client, conn):
    r = post(client, mail(to="invoices.nobody@inbound.finledger.test"))
    assert r.json()["status"] == "dropped"
    r = post(client, mail(to="invoices.acme-steel@gmail.com"))  # right slug, wrong domain: not our door
    assert r.json()["status"] == "dropped"


def test_envelope_recipient_param_wins(world, client, conn):
    r = post(client, mail(to="someone@else.com"), to="invoices.chai-point@inbound.finledger.test")
    assert r.json()["status"] == "accepted"
    with tenant(conn, world.c["id"]):
        assert conn.execute("select count(*) as n from documents").fetchone()["n"] == 1


def test_webhook_retry_is_idempotent(world, client, conn):
    raw = mail(attachments=[("a.pdf", invoice_pdf("A")), ("b.pdf", invoice_pdf("B"))])
    first = post(client, raw).json()["received"]
    second = post(client, raw).json()["received"]
    assert len(first) == 2 and [d["document_id"] for d in first] == [d["document_id"] for d in second]
    with tenant(conn, world.a["id"]):
        assert conn.execute("select count(*) as n from documents").fetchone()["n"] == 2
    assert len(outbox(conn, world.a["id"])) == 1


def test_failed_auth_sets_phish_flag_and_no_reply(world, client, conn):
    auth = "mx.finledger.test; spf=fail smtp.mailfrom=ganeshmetals.in; dkim=none; dmarc=fail"
    post(client, mail(auth=auth))
    with tenant(conn, world.a["id"]):
        doc = conn.execute("select phish_flag, source_meta from documents").fetchone()
    assert doc["phish_flag"] is True and "dmarc_fail" in doc["source_meta"]["phish_reasons"]
    assert outbox(conn, world.a["id"]) == []


def test_forged_auth_header_from_sender_is_ignored(world, client, conn):
    # Sender wrote their own "pass" header under a different authserv-id; our MTA's header is absent.
    post(client, mail(auth="evil.example; spf=pass; dkim=pass; dmarc=pass"))
    with tenant(conn, world.a["id"]):
        assert conn.execute("select phish_flag from documents").fetchone()["phish_flag"] is True


def test_reply_to_mismatch_flags(world, client, conn):
    post(client, mail(Reply_To="pay-here@freshbank-update.com"))
    with tenant(conn, world.a["id"]):
        meta = conn.execute("select phish_flag, source_meta from documents").fetchone()
    assert meta["phish_flag"] and "reply_to_domain_differs" in meta["source_meta"]["phish_reasons"]


def test_no_attachment_asks_resubmit(world, client, conn):
    r = post(client, mail(attachments=()))
    assert r.json()["status"] == "rejected"
    [reply] = outbox(conn, world.a["id"])
    assert reply["template"] == "resubmit" and reply["payload"]["rejected"][0]["reason"] == "no invoice attached"


def test_signature_logo_is_not_a_document(world, client, conn):
    m = EmailMessage()
    m["From"], m["To"], m["Message-ID"] = "a@ganeshmetals.in", "invoices.acme-steel@inbound.finledger.test", "<x@y>"
    m["Authentication-Results"] = GOOD_AUTH
    m.set_content("see attached")
    m.add_alternative('<p>see attached <img src="cid:logo"></p>', subtype="html")
    m.get_payload()[1].add_related(b"\x89PNG\r\n\x1a\n" + b"0" * 500, maintype="image", subtype="png", cid="<logo>")
    m.add_attachment(invoice_pdf(), maintype="application", subtype="pdf", filename="inv.pdf")
    r = post(client, m.as_bytes())
    assert [d["filename"] for d in r.json()["received"]] == ["inv.pdf"]
