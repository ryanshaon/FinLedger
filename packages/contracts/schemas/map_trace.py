from typing import List
from pydantic import BaseModel

class MapTrace(BaseModel):
    map_trace_id: str
    document_id: str
    client_id: str
    retrieved_chunk_ids: List[str]
    model_name: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
