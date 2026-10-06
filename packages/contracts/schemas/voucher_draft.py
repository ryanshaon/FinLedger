from typing import List, Optional, Dict, Literal
from pydantic import BaseModel, Field, model_validator

class VoucherLine(BaseModel):
    ledger: str
    amount: float
    is_debit: bool = True
    cost_centre: Optional[str] = None

class GstDetails(BaseModel):
    treatment: Literal["intra", "inter", "exempt"] = "exempt"
    input_ledgers: List[str] = Field(default_factory=list)
    tax_breakup: Dict[str, float] = Field(default_factory=dict)

class TdsDetails(BaseModel):
    applicable: bool = False
    section: Optional[str] = None
    ledger: Optional[str] = None
    amount: float = 0.0

class BillWise(BaseModel):
    ref_type: Literal["new_ref", "against_ref", "advance", "on_account"] = "new_ref"
    ref: str
    due_date: Optional[str] = None

class VoucherDraft(BaseModel):
    document_id: str
    date: str
    voucher_type: Literal["purchase", "debit_note", "credit_note", "payment"]
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

    @model_validator(mode="after")
    def document_ids_match(self):
        if self.attachment_document_id != self.document_id:
            raise ValueError("attachment_document_id must match document_id")
        return self
