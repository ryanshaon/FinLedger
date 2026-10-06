from __future__ import annotations

import os, shutil, socket, subprocess, sys, tempfile
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "person2_platform" / "src"))
from finledger_platform.db import make_pool, migrate, tenant
from finledger_platform.tenancy import create_firm_with_admin, create_client, create_user, add_member

APP_PASSWORD = "fl_control_test_pw"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


@pytest.fixture(scope="session")
def owner_dsn():
    initdb = shutil.which("initdb") or next(Path("C:/Program Files/PostgreSQL").glob("*/bin/initdb.exe"), None)
    if not initdb:
        pytest.skip("Postgres initdb not installed")
    bindir, data, port = Path(initdb).parent, Path(tempfile.mkdtemp(prefix="flp3_")), _free_port()
    subprocess.run([str(bindir/"initdb"), "-D", str(data), "-U", "postgres", "-A", "trust", "-E", "UTF8"],
                   check=True, capture_output=True)
    subprocess.run([str(bindir/"pg_ctl"), "-D", str(data), "-o", f"-p {port} -c listen_addresses=127.0.0.1",
                    "-l", str(data/"log.txt"), "-w", "start"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try: yield f"postgresql://postgres@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run([str(bindir/"pg_ctl"), "-D", str(data), "-m", "immediate", "stop"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(data, ignore_errors=True)


@pytest.fixture(scope="session")
def app_dsn(owner_dsn):
    migrate(owner_dsn)
    with psycopg.connect(owner_dsn, autocommit=True) as c:
        c.execute("drop role if exists fl_control")
        c.execute(f"create role fl_control login password '{APP_PASSWORD}' in role finledger_app")
    info = conninfo_to_dict(owner_dsn); info.update(user="fl_control", password=APP_PASSWORD)
    return make_conninfo(**info)


@pytest.fixture(scope="session")
def pool(app_dsn):
    p = make_pool(app_dsn, min_size=1, max_size=4); yield p; p.close()


@pytest.fixture
def world(owner_dsn, pool):
    tables = "posting_attempts, workflow_events, approvals, correction_events, review_tasks, voucher_drafts, risk_assessments, canonical_invoices, firms, users, clients, client_members, vendors, documents, document_assets, jobs, erp_links, mapping_table, masters_snapshots, open_bills, rag_chunks, outbox, rate_limits"
    with psycopg.connect(owner_dsn, autocommit=True) as owner:
        owner.execute(f"truncate {tables} restart identity cascade")
        firm_id, admin_id, _ = create_firm_with_admin(owner, "Sharma & Co", "admin@sharma.test")
        clerk_id, approver_id, payer_id = (None, None, None)
        with pool.connection() as conn, conn.transaction():
            conn.execute("select set_config('app.firm_id', %s, true)", (str(firm_id),))
            client = create_client(conn, "Acme Steel", gstins=["27AAPFU0939F1ZV"], inbound_slug="acme-steel",
                                   policy={"auto_post_cap": 25000, "maker_checker": True})
            clerk_id, clerk_token = create_user(conn, "clerk@sharma.test")
            approver_id, approver_token = create_user(conn, "approver@sharma.test")
            payer_id, payer_token = create_user(conn, "payer@sharma.test")
            add_member(conn, client["id"], clerk_id, "ap_clerk")
            add_member(conn, client["id"], approver_id, "approver")
            add_member(conn, client["id"], payer_id, "payer")
        with tenant(owner, client["id"]):
            owner.execute("insert into vendors(client_id,gstin,name,msme,email_domains) values (%s,%s,%s,false,%s)",
                          (client["id"], "27AAACS1234A1Z2", "Shree Ganesh Metals", ["ganesh.test"]))
    return {"client": client, "admin": admin_id, "clerk": clerk_id, "clerk_token": clerk_token,
            "approver": approver_id, "approver_token": approver_token, "payer": payer_id, "payer_token": payer_token}


@pytest.fixture
def conn(pool):
    with pool.connection() as c: yield c


@pytest.fixture
def document(conn, world):
    import hashlib
    cid = world["client"]["id"]
    did = "11111111-1111-1111-1111-111111111111"
    with tenant(conn, cid):
        conn.execute("""insert into documents(id,client_id,channel,object_path,original_filename,content_hash,mime,size_bytes,status,virus_ok,source_meta)
                        values (%s,%s,'link',%s,'invoice.pdf',%s,'application/pdf',100,'extracted',true,'{}')""",
                     (did, cid, f"{cid}/2026/10/{did}/raw.pdf", hashlib.sha256(b"x").hexdigest()))
    return did

