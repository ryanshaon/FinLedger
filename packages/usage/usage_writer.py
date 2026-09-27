from dataclasses import dataclass
from typing import Optional
import datetime

@dataclass
class UsageRow:
    client_id: str
    doc_id: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: float
    purpose: str
    timestamp: str

class UsageWriter:
    def __init__(self):
        self.logs = []

    def log_usage(
        self,
        client_id: str,
        doc_id: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        cost_usd: float,
        purpose: str
    ) -> UsageRow:
        row = UsageRow(
            client_id=client_id,
            doc_id=doc_id,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            purpose=purpose,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
        )
        self.logs.append(row)
        # In a real app this would write to a DB or log stream
        return row
