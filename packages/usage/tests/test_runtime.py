import pytest
from concurrent.futures import ThreadPoolExecutor

from usage.kill_switch import KillSwitch, TokenCapExceededException
from usage.model_router import ModelRouter, Settings
from usage.provider import MockProvider


def test_settings_and_router_are_environment_backed(monkeypatch):
    monkeypatch.setenv("FINLEDGER_LLM_PROVIDER", "mock")
    monkeypatch.setenv("FINLEDGER_MODEL_MAP", "local-map-v2")
    monkeypatch.setenv("FINLEDGER_PER_BILL_TOKEN_CAP", "321")
    settings = Settings.from_env()
    router = ModelRouter(settings)
    assert settings.provider == "mock"
    assert settings.per_bill_token_cap == 321
    assert router.resolve_model("map") == "local-map-v2"
    assert isinstance(router.provider, MockProvider)


def test_provider_calls_share_a_document_kill_switch():
    switch = KillSwitch(limit=20)
    provider = MockProvider(kill_switch=switch)
    provider.complete("summary", "mock", "client-a", "doc-1", {"text": "one"})
    with pytest.raises(TokenCapExceededException):
        provider.complete("summary", "mock", "client-a", "doc-1", {"text": "a much longer request that cannot fit in the remaining budget"})


def test_cap_is_scoped_by_client_and_document():
    switch = KillSwitch(limit=10)
    reservation = switch.reserve("client-a", "doc-1", 10)
    switch.reconcile(reservation, 10)
    other = switch.reserve("client-b", "doc-1", 10)
    switch.reconcile(other, 10)
    assert switch.usage("client-a", "doc-1") == 10
    assert switch.usage("client-b", "doc-1") == 10


def test_reservation_is_thread_safe():
    switch = KillSwitch(limit=10)
    def reserve():
        try:
            return switch.reserve("client-a", "doc-1", 10)
        except TokenCapExceededException:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sum(result is not None for result in results) == 1


def test_reconcile_replaces_estimate_with_actual_usage():
    switch = KillSwitch(limit=100)
    reservation = switch.reserve("client-a", "doc-1", 80)
    switch.reconcile(reservation, 25)
    assert switch.usage("client-a", "doc-1") == 25
    assert switch.reserved("client-a", "doc-1") == 0


def test_rejected_provider_call_never_invokes_generation():
    class CountingProvider(MockProvider):
        generations = 0
        def _generate(self, purpose, document_id, payload):
            self.generations += 1
            return super()._generate(purpose, document_id, payload)

    switch = KillSwitch(limit=1)
    provider = CountingProvider(kill_switch=switch)
    with pytest.raises(TokenCapExceededException):
        provider.complete("summary", "mock", "client-a", "doc-1", {"text": "too large"})
    assert provider.generations == 0

