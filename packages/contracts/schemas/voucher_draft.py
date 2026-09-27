from typing import List, Optional, Dict
from pydantic import BaseModel, Field

class VoucherLine(BaseModel):
    ledger: str
    amount: float
    is_debit: bool = True
    cost_centre: Optional[str] = None

class GstDetails(BaseModel):
    treatment: str = Field(description="intra | inter | exempt")
    input_ledgers: List[str] = Field(default_factory=list)
    tax_breakup: Dict[str, float] = Field(default_factory=dict)

class TdsDetails(BaseModel):
    applicable: bool = False
    section: Optional[str] = None
    ledger: Optional[str] = None
    amount: float = 0.0

class BillWise(BaseModel):
    ref_type: str = "new_ref"
    ref: str
    due_date: str

class VoucherDraft(BaseModel):
    document_id: str
    date: str
    voucher_type: str = Field(description="purchase | debit_note | credit_note | payment")
    party_ledger: str
    party_create_proposal: bool = False
    lines: List[VoucherLine] = Field(default_factory=list)
    gst: GstDetails = Field(default_factory=GstDetails)
    tds: TdsDetails = Field(default_factory=TdsDetails)
    bill_wise: BillWise
    narration: str = ""
    attachment_document_id: str
    reasons: List[str] = Field(default_factory=list)
    map_trace_id: str
