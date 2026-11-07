"""Compare a purchase order, a goods receipt, and an invoice.

Quantities agree when all three are the same number. Amounts agree when the
purchase-order amount and the invoice amount are the same number. A mismatch
names the side that does not share the value the other sides share. When every
quantity is different, each side is named.

Offline Tally XML still refuses a GST draft. This module does not post and does
not call Tally. It only says that posting stays blocked.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

PO_MISSING = "Purchase order is not on file."
GRN_MISSING = "Goods receipt is not on file."
PO_QTY = "Purchase order quantity differs."
GRN_QTY = "Goods receipt quantity differs."
INV_QTY = "Invoice quantity differs."
PO_AMOUNT = "Purchase order amount differs from the invoice."
INV_AMOUNT = "Invoice amount differs."

INTRA_BLOCK = "Posting is blocked. Offline Tally XML refuses intra-GST drafts."
GST_BLOCK = "Posting is blocked. Offline Tally XML refuses this GST draft."

PHASE1_REASONS = {
    "posting_not_enabled": "Posting is off for this company.",
    "not_human_approved": "A person must approve this revision before it can post.",
    "tax_on_invoice": "This bill has tax. Phase 1 can't post taxed bills. Enter it in Tally by hand.",
    "gst_not_exempt": "This draft isn't marked GST-exempt. Phase 1 can't post it. Enter it in Tally by hand.",
    "tds_present": "This bill has TDS. Phase 1 can't post it. Enter it in Tally by hand.",
    "new_vendor_ledger": "Create this ledger in Tally first. FinLedger never creates ledgers.",
    "not_purchase": "Phase 1 only posts purchase vouchers.",
    "round_off_present": "This bill has a round-off amount. Phase 1 can't post it. Enter it in Tally by hand.",
}

_QTY = Decimal("0.001")
_MONEY = Decimal("0.01")


def assess_three_way(invoice: dict | None, purchase_order: dict | None, goods_receipt: dict | None,
                      match_mode: str) -> dict:
    invoice = invoice or {}
    po_number = _text(invoice.get("po_number")) or _text((purchase_order or {}).get("po_number"))
    grn_number = _text(invoice.get("grn_number")) or _text((goods_receipt or {}).get("grn_number"))
    applicable = match_mode == "three_way" or purchase_order is not None or goods_receipt is not None
    po_qty = _qty((purchase_order or {}).get("quantity")) if purchase_order else None
    po_amount = _money((purchase_order or {}).get("amount")) if purchase_order else None
    grn_qty = _qty((goods_receipt or {}).get("quantity_received")) if goods_receipt else None
    invoice_qty = _invoice_qty(invoice)
    invoice_amount = _money(invoice.get("total")) if "total" in invoice and invoice.get("total") is not None else None
    differences: list[str] = []
    if applicable:
        if purchase_order is None:
            differences.append(PO_MISSING)
        if goods_receipt is None:
            differences.append(GRN_MISSING)
        differences.extend(_quantity_differences(
            po_qty if purchase_order is not None else None,
            grn_qty if goods_receipt is not None else None,
            invoice_qty,
            po_present=purchase_order is not None,
            grn_present=goods_receipt is not None,
        ))
        differences.extend(_amount_differences(
            po_amount if purchase_order is not None else None,
            invoice_amount,
            po_present=purchase_order is not None,
        ))
    matched = bool(applicable and not differences)
    holds_review = match_mode == "three_way" and not matched
    note = ""
    if holds_review:
        note = "Needs review. " + " ".join(differences) if differences else "Needs review."
    return {
        "applicable": applicable,
        "matched": matched,
        "holds_review": holds_review,
        "match_mode": match_mode,
        "differences": differences,
        "review_note": note,
        "purchase_order": {
            "po_number": po_number or None,
            "quantity": _fmt_qty(po_qty) if purchase_order is not None else None,
            "amount": _fmt_money(po_amount) if purchase_order is not None else None,
        },
        "goods_receipt": {
            "grn_number": grn_number or None,
            "quantity_received": _fmt_qty(grn_qty) if goods_receipt is not None else None,
        },
        "invoice": {
            "quantity": _fmt_qty(invoice_qty),
            "amount": _fmt_money(invoice_amount),
        },
    }


def posting_view(draft: dict | None) -> dict:
    reason = offline_xml_posting_block(draft)
    return {"blocked": reason is not None, "reason": reason}


def offline_xml_posting_block(draft: dict | None) -> str | None:
    """Same refusal as offline purchase XML: GST drafts are not written.

    ``treatment`` ``intra`` is named. Any other non-exempt treatment, or tax
    ledgers or a tax breakup on an exempt draft, is the same block. Exempt with
    no tax data can still be posted by the existing job path. This does not
    build XML and does not open a Tally connection.
    """
    gst = (draft or {}).get("gst") if isinstance(draft, dict) else None
    if not isinstance(gst, dict):
        return None
    treatment = gst.get("treatment")
    ledgers = gst.get("input_ledgers") or []
    breakup = gst.get("tax_breakup") or {}
    taxed = bool(ledgers) or bool(breakup)
    if treatment == "exempt" and not taxed:
        return None
    if treatment == "intra":
        return INTRA_BLOCK
    if treatment not in (None, "", "exempt") or taxed:
        return GST_BLOCK
    return None


def _is_zero(value) -> bool:
    """True only for a readable amount equal to zero. A missing or unreadable amount is not zero: the gate fails closed."""
    try:
        return Decimal(str(value)) == 0
    except (InvalidOperation, ValueError):
        return False


def phase1_posting_block(draft: dict | None, invoice: dict | None, *, posting_enabled: bool,
                         human_approved: bool) -> str | None:
    """The first reason this bill may not create a post job in Phase 1, as a PHASE1_REASONS code; None when it may.

    Checks the invoice as well as the draft: a draft marked exempt on an invoice that carries tax must not post.
    Applies in every match mode. It does not build XML and does not open a Tally connection.
    """
    draft = draft if isinstance(draft, dict) else {}
    invoice = invoice if isinstance(invoice, dict) else {}
    if not posting_enabled:
        return "posting_not_enabled"
    if not human_approved:
        return "not_human_approved"
    if not all(_is_zero(invoice.get(field)) for field in ("cgst", "sgst", "igst", "cess")):
        return "tax_on_invoice"
    gst = draft.get("gst") if isinstance(draft.get("gst"), dict) else {}
    if gst.get("treatment") != "exempt" or gst.get("input_ledgers") or gst.get("tax_breakup"):
        return "gst_not_exempt"
    tds = draft.get("tds") if isinstance(draft.get("tds"), dict) else {}
    if tds.get("applicable") or tds.get("section") or tds.get("ledger") or not _is_zero(tds.get("amount", 0)):
        return "tds_present"
    if draft.get("party_create_proposal") is not False:
        return "new_vendor_ledger"
    if draft.get("voucher_type") != "purchase":
        return "not_purchase"
    if not _is_zero(invoice.get("round_off")):
        return "round_off_present"
    return None


def _quantity_differences(po_qty, grn_qty, invoice_qty, *, po_present: bool, grn_present: bool) -> list[str]:
    slots: list[tuple[str, Decimal | None]] = []
    if po_present:
        slots.append((PO_QTY, po_qty))
    if grn_present:
        slots.append((GRN_QTY, grn_qty))
    slots.append((INV_QTY, invoice_qty))
    present = [(label, qty) for label, qty in slots if qty is not None]
    notes = [label for label, qty in slots if qty is None]
    if len(present) < 2 or len({qty for _, qty in present}) == 1:
        return notes
    if len(present) == 3 and len({qty for _, qty in present}) == 2:
        counts: dict[Decimal, int] = {}
        for _, qty in present:
            counts[qty] = counts.get(qty, 0) + 1
        notes.extend(label for label, qty in present if counts[qty] == 1)
        return notes
    notes.extend(label for label, _ in present)
    return notes


def _amount_differences(po_amount, invoice_amount, *, po_present: bool) -> list[str]:
    if not po_present:
        return []
    if po_amount is None or invoice_amount is None:
        return [PO_AMOUNT if po_amount is None else INV_AMOUNT]
    if po_amount != invoice_amount:
        return [PO_AMOUNT]
    return []


def _invoice_qty(invoice: dict) -> Decimal | None:
    lines = invoice.get("line_items") or []
    if not lines:
        return None
    total = Decimal("0")
    for line in lines:
        if not isinstance(line, dict) or line.get("qty") is None:
            return None
        qty = _qty(line.get("qty"))
        if qty is None:
            return None
        total += qty
    return total.quantize(_QTY, rounding=ROUND_HALF_UP)


def _qty(value) -> Decimal | None:
    number = _decimal(value)
    if number is None:
        return None
    return number.quantize(_QTY, rounding=ROUND_HALF_UP)


def _money(value) -> Decimal | None:
    number = _decimal(value)
    if number is None:
        return None
    return number.quantize(_MONEY, rounding=ROUND_HALF_UP)


def _decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return number


def _fmt_qty(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _fmt_money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()
