import os
from dataclasses import dataclass

from usage.kill_switch import KillSwitch
from usage.provider import MockProvider


@dataclass(frozen=True)
class Settings:
    provider: str = "mock"
    per_bill_token_cap: int = 20000
    kill_switch_enabled: bool = True
    model_extract: str = "gemini-1.5-flash"
    model_extract_vision: str = "gemini-1.5-pro"
    model_risk_llm: str = "gemini-1.5-flash"
    model_map: str = "gemini-1.5-pro"
    model_summary: str = "gemini-1.5-flash"

    @classmethod
    def from_env(cls):
        return cls(
            provider=os.getenv("FINLEDGER_LLM_PROVIDER", "mock").strip().lower(),
            per_bill_token_cap=int(os.getenv("FINLEDGER_PER_BILL_TOKEN_CAP", "20000")),
            kill_switch_enabled=os.getenv("FINLEDGER_LLM_KILL_SWITCH", "true").lower() not in {"0", "false", "off"},
            model_extract=os.getenv("FINLEDGER_MODEL_EXTRACT", "gemini-1.5-flash"),
            model_extract_vision=os.getenv("FINLEDGER_MODEL_EXTRACT_VISION", "gemini-1.5-pro"),
            model_risk_llm=os.getenv("FINLEDGER_MODEL_RISK", "gemini-1.5-flash"),
            model_map=os.getenv("FINLEDGER_MODEL_MAP", "gemini-1.5-pro"),
            model_summary=os.getenv("FINLEDGER_MODEL_SUMMARY", "gemini-1.5-flash"),
        )


_PROVIDERS = {}

class ModelRouter:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.routes = {
            "extract": self.settings.model_extract,
            "extract_vision": self.settings.model_extract_vision,
            "risk_llm": self.settings.model_risk_llm,
            "map": self.settings.model_map,
            "summary": self.settings.model_summary,
        }
        if self.settings.provider != "mock":
            raise ValueError(f"Unsupported LLM provider: {self.settings.provider}")
        key = (self.settings.provider, self.settings.per_bill_token_cap, self.settings.kill_switch_enabled)
        self.provider = _PROVIDERS.setdefault(key, MockProvider(KillSwitch(self.settings.per_bill_token_cap, self.settings.kill_switch_enabled)))

    def resolve_model(self, purpose: str) -> str:
        if purpose not in self.routes:
            raise ValueError(f"Unknown purpose: {purpose}")
        return self.routes[purpose]
