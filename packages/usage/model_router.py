from typing import Optional

class ModelRouter:
    def __init__(self):
        # Default model assignments based on purpose
        self.routes = {
            "extract": "gemini-1.5-flash",
            "extract_vision": "gemini-1.5-pro",
            "risk_llm": "gemini-1.5-flash",
            "map": "gemini-1.5-pro",
            "summary": "gemini-1.5-flash"
        }

    def resolve_model(self, purpose: str) -> str:
        if purpose not in self.routes:
            raise ValueError(f"Unknown purpose: {purpose}")
        return self.routes[purpose]
