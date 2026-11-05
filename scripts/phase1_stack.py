"""The whole product on this computer for the Phase 1 browser test: a throwaway Postgres, the real API, workers and
control server with the mock provider, the development login, and a SYNTHETIC Tally. Nothing here reaches a real Tally.

Prints one JSON line when ready, then serves a small control API until stopped. Uses only test values it creates.
Needs a Python with Person 1-3 installed (person3_control_ui/.venv), PostgreSQL tools on PATH, and a built desk.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for part in ("person4_tally/src", "person4_tally/tests", "person2_platform/src", "person3_control_ui/src", "packages"):
    sys.path.insert(0, str(ROOT / part))

import psycopg  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from fake_tally import FakeTally  # noqa: E402
from finledger_platform.db import migrate, tenant  # noqa: E402
from finledger_platform.tenancy import add_member, create_client, create_firm_with_admin, create_user, pair_erp_agent  # noqa: E402
from test_masters import ledger  # noqa: E402

COMPANY = "FinLedger Live Test 20261105"
GUID = "5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b"
VENDOR = "Shree Ganesh Metals"
VENDOR_GSTIN = "27AAACS1234A1Z2"
BUYER_GSTIN = "27AAPFU0939F1ZV"
APP_PASSWORD = "phase1_stack_only"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_postgres() -> tuple[Path, str, Path]:
    initdb = shutil.which("initdb")
    if not initdb:
        raise SystemExit("PostgreSQL tools (initdb) must be on PATH")
    bindir = Path(initdb).parent
    data = Path(tempfile.mkdtemp(prefix="flstack_"))
    run_as: list[str] = []
    if os.name != "nt" and os.geteuid() == 0:
        run_as = ["runuser", "-u", "postgres", "--"]
        shutil.chown(data, "postgres", "postgres")
    subprocess.run([*run_as, str(bindir / "initdb"), "-D", str(data / "pg"), "-U", "postgres", "-A", "trust", "-E", "UTF8"],
                   check=True, capture_output=True)
    port = free_port()
    options = f"-p {port} -c listen_addresses=127.0.0.1"
    if os.name != "nt":
        options += f" -c unix_socket_directories={data / 'pg'}"
    subprocess.run([*run_as, str(bindir / "pg_ctl"), "-D", str(data / "pg"), "-o", options, "-l", str(data / "pg.log"), "-w", "start"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return data, f"postgresql://postgres@127.0.0.1:{port}/postgres", bindir


def invoice_pdf(number: str, taxed: bool) -> bytes:
    lines = [f"Vendor: {VENDOR}", f"GSTIN: {VENDOR_GSTIN}", "Date: 2026-09-15", f"Inv No: {number}"]
    if taxed:
        lines += ["| Item | HSN | Qty | Rate | Taxable | CGST | SGST |", "| --- | --- | --- | --- | --- | --- | --- |",
                  "| HR Coil | 7208 | 10 | 10000 | 100000 | 9000 | 9000 |", "Total Taxable: 100000", "Total: 118000"]
    else:
        lines += ["| Item | HSN | Qty | Rate | Taxable |", "| --- | --- | --- | --- | --- |",
                  "| Books | 4901 | 1 | 100 | 100 |", "Total Taxable: 100", "Total: 100"]
    buf = io.BytesIO()
    page = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in lines:
        page.drawString(40, y, line)
        y -= 16
    page.showPage()
    page.save()
    return buf.getvalue()


class Stack:
    def __init__(self) -> None:
        self.data, self.owner_dsn, self.bindir = start_postgres()
        self.processes: list[subprocess.Popen] = []
        self.tally = FakeTally(companies=[(COMPANY, GUID)],
                               ledgers={GUID: [ledger(VENDOR, "Sundry Creditors", GUID, n=1),
                                               ledger("Purchases", "Purchase Accounts", GUID, n=2)]},
                               vouchers={GUID: []}, bills={GUID: []})
        migrate(self.owner_dsn)
        info = psycopg.conninfo.conninfo_to_dict(self.owner_dsn)
        with psycopg.connect(self.owner_dsn, autocommit=True) as owner:
            owner.execute(f"create role fl_app login password '{APP_PASSWORD}' in role finledger_app, finledger_worker_ingest, "
                          "finledger_worker_extract, finledger_worker_score, finledger_worker_map, finledger_worker_post, "
                          "finledger_queue_ops")
            firm_id, self.owner_id, self.owner_token = create_firm_with_admin(owner, "Stack Test Firm", "owner@stack.test")
        self.app_dsn = psycopg.conninfo.make_conninfo(**{**info, "user": "fl_app", "password": APP_PASSWORD})
        with psycopg.connect(self.app_dsn, autocommit=True, row_factory=psycopg.rows.dict_row) as conn:
            with conn.transaction():
                conn.execute("select set_config('app.firm_id', %s, true)", (str(firm_id),))
                client = create_client(conn, "Stack Test Company", gstins=[BUYER_GSTIN], inbound_slug="stack-test")
                self.clerk_id, self.clerk_token = create_user(conn, "clerk@stack.test")
                self.approver_id, self.approver_token = create_user(conn, "approver@stack.test")
                add_member(conn, client["id"], self.clerk_id, "ap_clerk")
                add_member(conn, client["id"], self.approver_id, "approver")
            self.client_id = str(client["id"])
            with tenant(conn, self.client_id):
                self.agent_token = pair_erp_agent(conn, self.client_id, "tally", COMPANY)
        self.api_port, self.control_port = free_port(), free_port()
        self.state_dir = self.data / "agent"
        self.env = {**os.environ,
                    "PYTHONPATH": os.pathsep.join(str(ROOT / p) for p in ("person4_tally/src", "person2_platform/src",
                                                                          "person3_control_ui/src", "packages")),
                    "PYTHONDONTWRITEBYTECODE": "1", "FINLEDGER_ENV": "development", "FINLEDGER_ALLOW_MOCK_KEYS": "true",
                    "FINLEDGER_DATABASE_URL": self.app_dsn, "FINLEDGER_OWNER_DATABASE_URL": self.owner_dsn,
                    "FINLEDGER_APP_BASE_URL": f"http://127.0.0.1:{self.api_port}",
                    "FINLEDGER_SIGNING_SECRET": "stack_signing_secret_only_for_this_test",
                    "FINLEDGER_INBOUND_WEBHOOK_SECRET": "stack_inbound_secret", "FINLEDGER_INBOUND_DOMAIN": "inbound.localhost",
                    "FINLEDGER_STORE": "local", "FINLEDGER_STORE_ROOT": str(self.data / "objects"),
                    "FINLEDGER_VIRUS_SCANNER": "eicar", "FINLEDGER_LLM_PROVIDER": "mock",
                    "FINLEDGER_DESK_DIST": str(ROOT / "desk" / "dist"),
                    "FINLEDGER_POSTING_OWNER_USER_ID": str(self.owner_id),
                    "FINLEDGER_DEV_LOGIN_USERS": (f"owner@stack.test={self.owner_token},clerk@stack.test={self.clerk_token},"
                                                  f"approver@stack.test={self.approver_token}")}
        for name in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "FINLEDGER_TALLY_POSTING", "FINLEDGER_TALLY_OFFLINE_XML"):
            self.env.pop(name, None)
        platform = "from finledger_platform.cli import main; main()"
        for args in (["serve", "--port", str(self.api_port)], ["worker"], ["extract-worker"], ["score-worker"], ["map-worker"]):
            self._spawn([sys.executable, "-c", platform, *args])
        self._spawn([sys.executable, "-c", "from finledger_control.cli import main; main()", "--port", str(self.control_port)])
        self._wait(f"http://127.0.0.1:{self.api_port}/healthz")
        self._wait(f"http://127.0.0.1:{self.control_port}/healthz")
        self._seed_vendor()

    def _spawn(self, argv: list[str]) -> None:
        self.processes.append(subprocess.Popen(argv, env=self.env, cwd=self.data, stdin=subprocess.DEVNULL,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

    @staticmethod
    def _wait(url: str) -> None:
        for _ in range(120):
            try:
                with urllib.request.urlopen(url, timeout=2):
                    return
            except OSError:
                time.sleep(0.5)
        raise SystemExit(f"{url} did not start")

    def _seed_vendor(self) -> None:
        boundary = "phase1stack"
        csv = f"name,gstin,default_ledger\r\n{VENDOR},{VENDOR_GSTIN},Purchases\r\n"
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="kind"\r\n\r\nvendors\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="vendors.csv"\r\n'
                f"Content-Type: text/csv\r\n\r\n{csv}\r\n--{boundary}--\r\n").encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.api_port}/clients/{self.client_id}/retrieval-csv", data=body,
                                         headers={"Authorization": f"Bearer {self.owner_token}",
                                                  "Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(request, timeout=30):
            pass

    def run_once(self, answer: str) -> dict:
        """The real ``run-once`` command against the real API and the synthetic Tally."""
        env = {**self.env, "FINLEDGER_TALLY_COMPANY": COMPANY, "FINLEDGER_TALLY_COMPANY_GUID": GUID,
               "FINLEDGER_TALLY_CLIENT_ID": self.client_id, "FINLEDGER_TALLY_PORT": str(self.tally.port),
               "FINLEDGER_TALLY_STATE_DIR": str(self.state_dir), "FINLEDGER_TALLY_POSTING": "enabled",
               "FINLEDGER_TALLY_LIVE_TEST_COMPANY_GUID": GUID, "FINLEDGER_PLATFORM_URL": f"http://127.0.0.1:{self.api_port}",
               "FINLEDGER_AGENT_TOKEN": self.agent_token, "PYTHONUNBUFFERED": "1"}
        process = subprocess.Popen([sys.executable, "-m", "finledger_tally", "run-once"], env=env, cwd=self.data, text=True,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        last = ""
        while True:
            line = process.stdout.readline()
            if not line:
                break
            last = line.strip() or last
            if line.startswith("Type this exact phrase"):
                phrase = process.stdout.readline().strip()
                process.stdin.write((phrase if answer == "phrase" else answer) + "\n")
                process.stdin.flush()
        process.wait(timeout=60)
        return {"exit": process.returncode, **json.loads(last)}

    def control(self, path: str, query: dict) -> tuple[int, str, bytes]:
        def ok(body: dict) -> tuple[int, str, bytes]:
            return 200, "application/json", json.dumps(body).encode()
        if path == "/pdf":
            return 200, "application/pdf", invoice_pdf(query["number"][0], query.get("taxed", ["0"])[0] == "1")
        if path == "/tally":  # a fresh company with no vouchers, answering in the given way
            self.tally.vouchers[GUID] = []
            self.tally.imports.clear()
            self.tally.import_mode = query.get("mode", ["create"])[0]
            return ok({"ok": True})
        if path == "/imports":
            return ok({"imports": len(self.tally.imports)})
        if path == "/run-once":
            return ok(self.run_once(query.get("answer", ["phrase"])[0]))
        return 404, "text/plain", b"not found"

    def close(self) -> None:
        for process in self.processes:
            process.kill()
        self.tally.close()
        subprocess.run([str(self.bindir / "pg_ctl"), "-D", str(self.data / "pg"), "-m", "immediate", "stop"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(self.data, ignore_errors=True)


def main() -> None:
    stack = Stack()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parts = urllib.parse.urlsplit(self.path)
            if parts.path == "/stop":
                self.send_response(200)
                self.end_headers()
                server.shutdown_requested = True
                return
            status, kind, body = stack.control(parts.path, urllib.parse.parse_qs(parts.query))
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 0.5
    server.shutdown_requested = False
    print(json.dumps({"harness": f"http://127.0.0.1:{server.server_address[1]}", "desk": f"http://127.0.0.1:{stack.control_port}",
                      "client_id": stack.client_id, "company": COMPANY, "guid": GUID,
                      "users": {"owner": ["owner@stack.test", stack.owner_token], "clerk": ["clerk@stack.test", stack.clerk_token],
                                "approver": ["approver@stack.test", stack.approver_token]}}), flush=True)
    try:
        while not server.shutdown_requested:
            server.handle_request()
    finally:
        stack.close()


if __name__ == "__main__":
    main()
