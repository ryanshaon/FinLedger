from typing import Optional

from pydantic import AliasChoices, BaseModel, Field

class CorrectionEvent(BaseModel):
    client_id: Optional[str] = None
    document_id: str
    vendor_gstin: str
    field: str = "ledger"
    hsn_sac: str = Field(default="", validation_alias=AliasChoices("hsn_sac", "hsn"))
    description: str = ""
    old_ledger: str = Field(default="", validation_alias=AliasChoices("old_ledger", "old_value"))
    new_ledger: str = Field(default="", validation_alias=AliasChoices("new_ledger", "new_value"))
    old_tax: str = ""
    new_tax: str = ""
    actor_id: Optional[str] = None
    created_at: Optional[str] = None
