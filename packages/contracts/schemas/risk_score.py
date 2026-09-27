from typing import List, Optional
from pydantic import BaseModel

class RiskMark(BaseModel):
    field: str
    check: str
    ok: bool
    note: str

class RiskScore(BaseModel):
    document_id: str
    score: int = 0
    band: str = "low" # low | medium | high
    marks: List[RiskMark]
