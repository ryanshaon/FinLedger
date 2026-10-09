"""The staff web launcher must not log emailed invitation URLs."""

import sys

import uvicorn

import finledger_control.cli as cli
import finledger_platform.db as db


def test_control_launcher_disables_invitation_url_access_logging(monkeypatch, tmp_path):
    launches = []
    monkeypatch.setenv("FINLEDGER_DATABASE_URL", "postgresql://unused-local-test")
    monkeypatch.setenv("FINLEDGER_STORE", "local")
    monkeypatch.setenv("FINLEDGER_STORE_ROOT", str(tmp_path))
    monkeypatch.setenv("FINLEDGER_SIGNING_SECRET", "synthetic-local-test-key")
    monkeypatch.setattr(sys, "argv", ["finledger-control"])
    # No real pool, Auth service or network server is needed to verify the real launch configuration.
    monkeypatch.setattr(db, "make_pool", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli, "browser_sessions_from_env", lambda: None)
    monkeypatch.setattr(cli, "staff_invitations_from_env", lambda: None)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: launches.append(kwargs))
    cli.main()
    assert len(launches) == 1
    assert launches[0].get("access_log") is False
