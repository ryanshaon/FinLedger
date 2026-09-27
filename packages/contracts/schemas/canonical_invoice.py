from typing import List, Optional
from pydantic import BaseModel, Field

class Vendor(BaseModel):
    name: str = ""
    gstin: str = ""
    pan: str = ""
    state: str = ""

class LineItem(BaseModel):
    desc: str = ""
    hsn_sac: str = ""
    qty: float = 0.0
    rate: float = 0.0
    taxable: float = 0.0
    tax_rate: float = 0.0
    tax_amount: float = 0.0

class Confidences(BaseModel):
    invoice_no: float = 0.0
    date: float = 0.0
    gstin: float = 0.0
    total: float = 0.0

class CanonicalInvoice(BaseModel):
    document_id: str
    document_type: str = Field(description="purchase_invoice | debit_note | credit_note | expense | unknown")
    vendor: Vendor
    buyer_gstin: str = ""
    invoice_no: str = ""
    invoice_date: str = ""
    due_date: Optional[str] = None
    irn: Optional[str] = None
    po_number: Optional[str] = None
    grn_number: Optional[str] = None
    taxable: float = 0.0
    cgst: float = 0.0
    sgst: float = 0.0
    igst: float = 0.0
    cess: float = 0.0
    round_off: float = 0.0
    total: float = 0.0
    line_items: List[LineItem] = Field(default_factory=list)
    bank_upi: Optional[str] = None
    confidences: Confidences = Field(default_factory=Confidences)
    raw_markdown_path: str = ""
    page_image_paths: List[str] = Field(default_factory=list)
