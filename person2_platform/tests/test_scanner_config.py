"""Reject development-only antivirus settings before hosted workers start."""

import pytest

from finledger_platform.config import Settings
from finledger_platform.virus import EICAR, scan


def scanner_settings(scanner):
    return Settings(database_url="postgresql://unused-local-test", signing_secret=b"test-signing",
                    inbound_webhook_secret=b"test-webhook", virus_scanner=scanner)


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_hosted_settings_reject_eicar_test_scanner(monkeypatch, environment):
    monkeypatch.setenv("FINLEDGER_ENV", environment)
    with pytest.raises(ValueError, match="clamd"):
        scanner_settings("eicar")


@pytest.mark.parametrize("scanner", ["", "clmad"])
def test_unknown_scanner_configuration_is_rejected(monkeypatch, scanner):
    monkeypatch.setenv("FINLEDGER_ENV", "development")
    with pytest.raises(ValueError, match="scanner"):
        scanner_settings(scanner)


def test_development_eicar_mode_still_detects_test_signature(monkeypatch):
    monkeypatch.setenv("FINLEDGER_ENV", "development")
    settings = scanner_settings("eicar")
    assert scan(b"synthetic prefix" + EICAR, settings) == "Eicar-Test-Signature"
    assert scan(b"synthetic plain bytes", settings) is None


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_hosted_clamd_configuration_can_start_without_network_io(monkeypatch, environment):
    monkeypatch.setenv("FINLEDGER_ENV", environment)
    settings = scanner_settings("clamd")
    assert settings.virus_scanner == "clamd"
