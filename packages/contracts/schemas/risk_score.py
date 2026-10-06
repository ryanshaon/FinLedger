from typing import List, Literal
from pydantic import BaseModel, Field, model_validator

AllowedRiskCheck = Literal["suspicious_language", "payment_instruction_anomaly", "identity_impersonation"]

class RiskMark(BaseModel):
    field: str
    check: AllowedRiskCheck
    ok: bool
    note: str

class RiskScore(BaseModel):
    document_id: str
    score: int = Field(default=0, ge=0, le=100)
    band: Literal["low", "medium", "high"] = "low"
    marks: List[RiskMark] = Field(default_factory=list)

    @model_validator(mode="after")
    def band_matches_score(self):
        expected = "low" if self.score < 30 else "medium" if self.score < 70 else "high"
        if self.band != expected:
            raise ValueError(f"band must be {expected} for score {self.score}")
        return self
