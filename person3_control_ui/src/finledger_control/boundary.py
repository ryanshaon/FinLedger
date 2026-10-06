"""Strict cross-seat payload validation owned by Person 3's ingestion boundary."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=False)


class Vendor(StrictModel):
    name: str = Field(min_length=1)
    gstin: str | None = None


class LineItem(StrictModel):
    desc: str
    hsn_sac: str | None = None
    qty: Decimal | None = None
    rate: Decimal | None = None
    taxable: Decimal
    tax_rate: Decimal
    tax_amount: Decimal


class CanonicalInvoiceBoundary(StrictModel):
    document_id: UUID
    document_type: Literal["purchase_invoice", "credit_note", "debit_note"]
    vendor: Vendor
    buyer_gstin: str | None = None
    invoice_no: str = Field(min_length=1)
    invoice_date: date
    due_date: date | None = None
    irn: str | None = None
    po_number: str | None = None
    grn_number: str | None = None
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    cess: Decimal
    round_off: Decimal
    total: Decimal = Field(gt=0)
    line_items: list[LineItem] = Field(min_length=1)
    bank_upi: dict | None = None
    confidences: dict[str, float]
    raw_markdown_path: str
    page_image_paths: list[str]

    @model_validator(mode="after")
    def confidence_bounds(self):
        if any(not 0 <= value <= 1 for value in self.confidences.values()):
            raise ValueError("confidence must be between 0 and 1")
        return self


class VoucherLine(StrictModel):
    ledger: str = Field(min_length=1)
    amount: Decimal
    is_debit: bool
    cost_centre: str | None = None


class GstDraft(StrictModel):
    treatment: Literal["intra", "inter", "exempt", "non_gst", "reverse_charge"]
    input_ledgers: list[str]
    tax_breakup: dict


class TdsDraft(StrictModel):
    applicable: bool
    section: str | None = None
    ledger: str | None = None
    amount: Decimal = Decimal("0")


class BillWise(StrictModel):
    ref_type: Literal["new_ref", "against_ref", "advance", "on_account"]
    ref: str
    due_date: date | None = None


class VoucherDraftBoundary(StrictModel):
    document_id: UUID
    date: date
    voucher_type: Literal["purchase", "credit_note", "debit_note", "journal"]
    party_ledger: str = Field(min_length=1)
    party_create_proposal: bool
    lines: list[VoucherLine] = Field(min_length=1)
    gst: GstDraft
    tds: TdsDraft
    bill_wise: BillWise
    narration: str
    attachment_document_id: UUID
    reasons: list[str]
    map_trace_id: UUID
    ai_paragraph: str | None = None


def validate_invoice(payload: dict, document_id: UUID | str) -> tuple[dict, Decimal]:
    invoice = CanonicalInvoiceBoundary.model_validate(payload)
    expected = UUID(str(document_id))
    if invoice.document_id != expected:
        raise ValueError("document_id does not match route document")
    return invoice.model_dump(mode="json"), invoice.total


def validate_draft(payload: dict, document_id: UUID | str) -> tuple[dict, Decimal]:
    draft = VoucherDraftBoundary.model_validate(payload)
    expected = UUID(str(document_id))
    if draft.document_id != expected:
        raise ValueError("document_id does not match route document")
    if draft.attachment_document_id != expected:
        raise ValueError("attachment_document_id does not match route document")
    total = sum((abs(line.amount) for line in draft.lines), Decimal("0"))
    return draft.model_dump(mode="json"), total
