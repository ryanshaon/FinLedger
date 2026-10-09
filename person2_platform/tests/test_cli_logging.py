"""The web launcher must not enable logging of credential-bearing request URLs."""

import uvicorn

from finledger_platform.cli import main


def test_platform_launcher_disables_credential_url_access_logging(monkeypatch):
    launches = []
    # Replace only the blocking network server, retaining the real command parsing/launch boundary.
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: launches.append(kwargs))
    main(["serve", "--host", "127.0.0.1", "--port", "8000"])
    assert len(launches) == 1
    assert launches[0].get("access_log") is False
