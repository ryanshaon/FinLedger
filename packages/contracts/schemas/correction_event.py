from pydantic import BaseModel

class CorrectionEvent(BaseModel):
    document_id: str
    vendor_gstin: str
    hsn_sac: str
    description: str
    old_ledger: str
    new_ledger: str
    old_tax: str
    new_tax: str
