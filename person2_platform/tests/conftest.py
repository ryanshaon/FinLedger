"""Test harness. Uses FINLEDGER_TEST_OWNER_DSN if set, otherwise boots a throwaway Postgres cluster with initdb.

The app side always connects as `fl_app` (member of finledger_app, not owner, no BYPASSRLS), so every test
exercises real Row Level Security, never a superuser shortcut.
"""
from __future__ import annotations

import io
import os
import shutil
import socket
import subprocess
import tempfile
import zipfile
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from finledger_platform import tenancy
from finledger_platform.api import create_app
from finledger_platform.config import Settings
from finledger_platform.db import firm, make_pool, migrate
from finledger_platform.store import LocalStore

APP_PASSWORD = "fl_app_test_pw"
TENANT_TABLES = "firms, users, clients, client_members, vendors, documents, document_assets, jobs, erp_links, " \
                "mapping_table, masters_snapshots, open_bills, rag_chunks, outbox, rate_limits"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def owner_dsn():
    if dsn := os.environ.get("FINLEDGER_TEST_OWNER_DSN"):
        yield dsn
        return
    initdb = shutil.which("initdb") or next(Path("C:/Program Files/PostgreSQL").glob("*/bin/initdb.exe"), None)
    if not initdb:
        pytest.skip("no Postgres: set FINLEDGER_TEST_OWNER_DSN or put initdb on PATH")
    bindir = Path(initdb).parent
    data = Path(tempfile.mkdtemp(prefix="flpg_"))
    port = _free_port()
    subprocess.run([str(bindir / "initdb"), "-D", str(data), "-U", "postgres", "-A", "trust", "-E", "UTF8"],
                   check=True, capture_output=True)
    subprocess.run([str(bindir / "pg_ctl"), "-D", str(data), "-o", f"-p {port} -c listen_addresses=127.0.0.1",
                    "-l", str(data / "log.txt"), "-w", "start"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # pipes would be inherited by postgres and never close
    try:
        yield f"postgresql://postgres@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run([str(bindir / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(data, ignore_errors=True)


@pytest.fixture(scope="session")
def app_dsn(owner_dsn):
    migrate(owner_dsn)
    with psycopg.connect(owner_dsn, autocommit=True) as c:
        c.execute("drop role if exists fl_app")
        c.execute(f"create role fl_app login password '{APP_PASSWORD}' in role finledger_app")
        c.execute("grant finledger_worker_ingest,finledger_worker_extract,finledger_worker_score,finledger_worker_map,finledger_worker_post,finledger_queue_ops to fl_app")
    info = conninfo_to_dict(owner_dsn)
    info.update(user="fl_app", password=APP_PASSWORD)
    return make_conninfo(**info)


@pytest.fixture(scope="session")
def pool(app_dsn):
    p = make_pool(app_dsn, min_size=1, max_size=4)
    yield p
    p.close()


@pytest.fixture
def owner(owner_dsn, app_dsn):
    with psycopg.connect(owner_dsn, autocommit=True) as c:
        c.execute(f"truncate {TENANT_TABLES} restart identity cascade")
        yield c


@pytest.fixture
def settings(app_dsn, owner_dsn, tmp_path):
    return Settings(
        database_url=app_dsn, owner_database_url=owner_dsn, app_base_url="http://testserver",
        signing_secret=b"test-signing-secret", enc_key="",
        store="local", store_root=str(tmp_path / "objects"), s3_bucket="",
        inbound_domain="inbound.finledger.test", inbound_webhook_secret=b"test-webhook-secret",
        inbound_authserv_id="mx.finledger.test",
        virus_scanner="eicar", clamd_host="127.0.0.1", clamd_port=3310,
        max_upload_bytes=5 * 1024 * 1024, rate_per_token_hour=1000, rate_per_ip_hour=1000, render_page_cap=30,
    )


@pytest.fixture
def store(settings):
    return LocalStore(settings.store_root, settings.signing_secret, settings.app_base_url)


@pytest.fixture
def conn(pool):
    with pool.connection() as c:
        yield c


class World:
    """Firm 1 has clients A and C; firm 2 has client B. Tokens are firm-admin bearer tokens."""


@pytest.fixture
def world(owner, conn):
    w = World()
    w.firm1, w.admin1, w.token1 = tenancy.create_firm_with_admin(owner, "Sharma & Co CAs", "admin@sharma.test")
    w.firm2, w.admin2, w.token2 = tenancy.create_firm_with_admin(owner, "Iyer Associates", "admin@iyer.test")
    with firm(conn, w.firm1):
        w.a = tenancy.create_client(conn, "Acme Steel Pvt Ltd", gstins=["27AAPFU0939F1ZV"], inbound_slug="acme-steel")
        w.c = tenancy.create_client(conn, "Chai Point Traders", inbound_slug="chai-point")
        w.clerk, w.clerk_token = tenancy.create_user(conn, "clerk@sharma.test")
        tenancy.add_member(conn, w.a["id"], w.clerk, "ap_clerk")
    with firm(conn, w.firm2):
        w.b = tenancy.create_client(conn, "Bharat Textiles", inbound_slug="bharat-tex")
    return w


@pytest.fixture
def client(settings, pool, store):
    return TestClient(create_app(settings, pool, store))


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------- fixture files ----------

def invoice_pdf(invoice_no: str = "INV-2026-0042", total: str = "1,18,000.00") -> bytes:
    """A digital GST tax invoice (text layer + a line-item table)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in ["TAX INVOICE", "Shree Ganesh Metals, GSTIN: 27AAACS1234A1Z1", f"Invoice No: {invoice_no}",
                 "Invoice Date: 15-09-2026", "Bill To: Acme Steel Pvt Ltd, GSTIN: 27AAPFU0939F1ZV"]:
        c.drawString(50, y, line)
        y -= 18
    y -= 10
    rows = [("Description", "HSN", "Qty", "Rate", "Amount"), ("HR Coil 2mm", "7208", "10", "10,000", "1,00,000.00")]
    for row in rows:
        for x, cell in zip((50, 230, 300, 350, 440), row):
            c.drawString(x, y, cell)
        c.line(45, y - 4, 540, y - 4)
        y -= 18
    for line in ["CGST 9%: 9,000.00", "SGST 9%: 9,000.00", f"Grand Total: {total}"]:
        c.drawString(350, y, line)
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


def scanned_pdf() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1240, 1754), "white")
    ImageDraw.Draw(img).text((100, 100), "TAX INVOICE  INV-SCAN-7  Total 5900.00", fill="black")
    buf = io.BytesIO()
    img.save(buf, "PDF", resolution=150)
    return buf.getvalue()


def photo_jpg() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (800, 1100), (240, 240, 235)).save(buf, "JPEG")
    return buf.getvalue()


def statement_xlsx() -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Date", "Invoice", "Debit", "Balance"])
    ws.append(["2026-09-01", "INV-1", 5000, 5000])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def zip_of(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()
