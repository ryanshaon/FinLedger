"""Browser shell for the Vite desk, and the copy the desk is required to show."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from finledger_control.api import create_app
from finledger_control.desk_shell import desk_dist
from finledger_platform.browser_sessions import StaffSession

ROOT = Path(__file__).resolve().parents[2]
COOKIE = "desk-shell-cookie-with-forty-characters-minimum"


class _Pool:
    def connection(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_default_desk_dist_is_the_repo_folder(monkeypatch):
    monkeypatch.delenv("FINLEDGER_DESK_DIST", raising=False)
    assert desk_dist() == ROOT / "desk" / "dist"


def test_browser_html_uses_the_built_desk_and_star_accept_keeps_the_old_pages(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<!doctype html><title>Desk shell</title>", encoding="utf-8")
    monkeypatch.setenv("FINLEDGER_DESK_DIST", str(tmp_path))

    class Sessions:
        aal = "aal2"

        def resolve(self, conn, cookie):
            assert cookie == COOKIE
            return StaffSession(uuid4(), uuid4(), True, self.aal)

    sessions = Sessions()
    client = TestClient(create_app(_Pool(), browser_sessions=sessions))
    shell = client.get("/login", headers={"Accept": "text/html"})
    assert shell.status_code == 200
    assert "Desk shell" in shell.text
    assert "Password" not in shell.text
    assert shell.cookies["fl_login_csrf"]
    classic = client.get("/login", headers={"Accept": "*/*"})
    assert "Password" in classic.text and "Desk shell" not in classic.text

    client.cookies.set("fl_dev_session", COOKIE)
    home = client.get("/", headers={"Accept": "text/html"})
    assert "Desk shell" in home.text
    sessions.aal = "aal1"
    blocked = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert blocked.status_code == 303 and blocked.headers["location"] == "/mfa"
    page = client.get("/mfa", headers={"Accept": "text/html"})
    assert "Desk shell" in page.text

    staff = client.get("/staff", headers={"Accept": "text/html"})
    assert "Desk shell" in staff.text
    fallback = client.get("/staff", headers={"Accept": "*/*"}, follow_redirects=False)
    assert fallback.status_code == 302 and fallback.headers["location"] == "/admin/staff"

    client_id = "11111111-1111-1111-1111-111111111111"
    document_id = "22222222-2222-2222-2222-222222222222"
    html = client.get(f"/clients/{client_id}", headers={"Accept": "text/html"})
    assert "Desk shell" in html.text
    review = client.get(f"/clients/{client_id}?queue=exception", headers={"Accept": "*/*"}, follow_redirects=False)
    assert review.status_code == 302
    assert review.headers["location"] == f"/clients/{client_id}/review?queue=exception"
    missing = client.get(f"/clients/{client_id}?queue=nope", headers={"Accept": "*/*"}, follow_redirects=False)
    assert missing.headers["location"].endswith("/review?queue=needs_review")
    invoice = client.get(f"/clients/{client_id}/invoices/{document_id}", headers={"Accept": "*/*"}, follow_redirects=False)
    assert invoice.status_code == 302
    assert invoice.headers["location"] == f"/clients/{client_id}/review/{document_id}"
    browser = client.get(f"/clients/{client_id}/review/{document_id}", headers={"Accept": "text/html"}, follow_redirects=False)
    assert browser.status_code == 302
    assert browser.headers["location"] == f"/clients/{client_id}/invoices/{document_id}"
    stayed = client.get(browser.headers["location"], headers={"Accept": "text/html"})
    assert "Desk shell" in stayed.text
    staff_browser = client.get("/admin/staff", headers={"Accept": "text/html"}, follow_redirects=False)
    assert staff_browser.status_code == 302 and staff_browser.headers["location"] == "/staff"
    queue = client.get(f"/clients/{client_id}/review?queue=needs_review", headers={"Accept": "text/html"}, follow_redirects=False)
    assert queue.status_code == 302
    assert queue.headers["location"] == f"/clients/{client_id}?queue=needs_review"


def test_desk_copy_and_queue_order():
    src = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "desk" / "src").rglob("*")
        if path.suffix in {".ts", ".tsx", ".css"}
    )
    theme = (ROOT / "desk" / "src" / "theme.ts").read_text(encoding="utf-8")
    router = (ROOT / "desk" / "src" / "router.ts").read_text(encoding="utf-8")
    css = (ROOT / "desk" / "src" / "styles.css").read_text(encoding="utf-8").lower()
    for line in ("Welcome back", "Sign out", "Clients", "Staff", "Search", "Notifications"):
        assert line in src
    assert (ROOT / "desk" / "public" / "finledger-logo.png").is_file()
    assert (ROOT / "desk" / "public" / "finledger-mark.png").is_file()
    assert "finledger-theme" in theme
    assert "Control plane" not in src
    labels = ['label: "New"', 'label: "Needs review"', 'label: "Approved"', 'label: "Exception"']
    positions = [router.index(label) for label in labels]
    assert positions == sorted(positions)
    assert 'label: "Posted"' not in router
    assert 'href="/transactions"' not in src
    for color in ("#0b3a66", "#1d4e89", "#0e1216", "#8fb4d9", "#1e3a5f"):
        assert color in css
    package = json.loads((ROOT / "desk" / "package.json").read_text(encoding="utf-8"))
    assert "react" in package["dependencies"]
    assert "tailwindcss" in package["devDependencies"]
