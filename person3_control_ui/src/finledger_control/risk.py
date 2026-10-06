"""Deterministic AP controls. The model may add marks; it cannot remove or soften these."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
import re
from typing import Any, Iterable


@dataclass(frozen=True)
class Mark:
    field: str
    check: str
    ok: bool
    note: str
    weight: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskResult:
    score: int
    band: str
    marks: tuple[Mark, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"score": self.score, "band": self.band, "marks": [m.to_dict() for m in self.marks]}


@dataclass(frozen=True)
class RiskContext:
    client_gstins: tuple[str, ...]
    vendor_exists: bool = False
    vendor_msme: bool = False
    vendor_master_name: str | None = None
    vendor_median: Decimal | None = None
    previous_bank_upi: str | None = None
    duplicate: bool = False
    phish_flag: bool = False
    unreadable_page: bool = False
    po_required: bool = False
    po_required_above: Decimal | None = None
    match_mode: str = "invoice_only"
    itc_180_warning: bool = True
    msme_clock: bool = True
    tds_likely: bool = False
    einvoice_expected: bool = False


def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _band(score: int) -> str:
    return "low" if score <= 24 else "medium" if score <= 59 else "high"


def _gstin_valid(value: str) -> bool:
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    gstin = (value or "").strip().upper()
    if not re.fullmatch(r"[0-9]{2}[A-Z0-9]{10}[0-9A-Z]Z[0-9A-Z]", gstin): return False
    total = 0
    for i, ch in enumerate(gstin[:14]):
        product = chars.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return chars[(36 - total % 36) % 36] == gstin[14]


def _name_key(value: str) -> str:
    return re.sub(r"\b(pvt|private|ltd|limited|llp|co|company)\b|[^a-z0-9]", "", (value or "").lower())


def score_invoice(invoice: dict[str, Any], ctx: RiskContext, *, llm_score: int = 0,
                  llm_marks: Iterable[dict[str, Any]] = ()) -> RiskResult:
    marks: list[Mark] = []

    def mark(field: str, check: str, ok: bool, note: str, weight: int = 0) -> None:
        marks.append(Mark(field, check, ok, note, 0 if ok else weight))

    conf = invoice.get("confidences") or {}
    for field, key in (("invoice_no", "invoice_no"), ("invoice_date", "date"),
                       ("vendor.gstin", "gstin"), ("total", "total")):
        value = float(conf.get(key, 0))
        mark(field, "confidence", value >= .75, f"{value:.2f}", 15)

    lines = invoice.get("line_items") or []
    line_taxable = sum((_money(x.get("taxable")) for x in lines), Decimal(0))
    taxable = _money(invoice.get("taxable"))
    tolerance = max(Decimal("1"), taxable * Decimal("0.005"))
    components = taxable + sum((_money(invoice.get(k)) for k in ("cgst", "sgst", "igst", "cess", "round_off")), Decimal(0))
    line_ok = abs(line_taxable - taxable) <= tolerance and abs(components - _money(invoice.get("total"))) <= tolerance
    mark("total", "lines_mismatch", line_ok,
         f"total {_money(invoice.get('total'))}; components {components}; line taxable {line_taxable}", 20)
    mark("source", "readable", not ctx.unreadable_page,
         "all pages readable" if not ctx.unreadable_page else "one or more pages unreadable", 25)

    buyer = str(invoice.get("buyer_gstin") or "").upper()
    vendor_data = invoice.get("vendor") or {}; vendor_gstin = str(vendor_data.get("gstin") or "").upper()
    mark("vendor.gstin", "gstin_checksum", _gstin_valid(vendor_gstin),
         "checksum valid" if _gstin_valid(vendor_gstin) else "invalid GSTIN format or checksum", 25)
    extracted_name, master_name = str(vendor_data.get("name") or ""), ctx.vendor_master_name
    name_score = SequenceMatcher(None, _name_key(extracted_name), _name_key(master_name or extracted_name)).ratio()
    name_ok = not master_name or name_score >= .65
    mark("vendor.name", "master_name", name_ok,
         "matches vendor master" if name_ok else f"does not match {master_name}", 20)
    mark("buyer_gstin", "client_identity", buyer in ctx.client_gstins,
         "matches client GSTIN" if buyer in ctx.client_gstins else f"{buyer or 'missing'} is not a client GSTIN", 65)
    mark("vendor", "known_vendor", ctx.vendor_exists,
         "vendor master matched" if ctx.vendor_exists else "new vendor", 20)
    bank = invoice.get("bank_upi")
    changed = bool(bank and ctx.previous_bank_upi and bank != ctx.previous_bank_upi)
    mark("bank_upi", "bank_changed", not changed, "unchanged" if not changed else "differs from last bill", 40)
    mark("sender", "sender_domain", not ctx.phish_flag,
         "domain aligned" if not ctx.phish_flag else "sender domain differs from vendor", 35)
    mark("invoice_no", "duplicate", not ctx.duplicate,
         "no client-scoped duplicate" if not ctx.duplicate else "same vendor, invoice number, FY and amount", 60)

    vendor_state, buyer_state = vendor_gstin[:2], buyer[:2]
    interstate = bool(vendor_state and buyer_state and vendor_state != buyer_state)
    cgst, sgst, igst = (_money(invoice.get(k)) for k in ("cgst", "sgst", "igst"))
    tax_ok = (igst > 0 and cgst == 0 and sgst == 0) if interstate else (igst == 0 and cgst == sgst)
    mark("gst", "gst_treatment", tax_ok,
         f"{'inter' if interstate else 'intra'}-state tax shape", 25)
    rates = {_money(x.get("tax_rate")) for x in lines}
    unusual = any(r not in {Decimal(0), Decimal(5), Decimal(12), Decimal(18), Decimal(28)} for r in rates)
    mark("line_items.tax_rate", "gst_rate", not unusual,
         "standard GST rates" if not unusual else f"unusual rates: {sorted(rates)}", 10)
    mark("irn", "irn_present", not (ctx.einvoice_expected and not invoice.get("irn")),
         "present or not required" if invoice.get("irn") or not ctx.einvoice_expected else "expected e-invoice IRN missing", 10)
    mark("tds", "tds_likely", not ctx.tds_likely,
         "not indicated" if not ctx.tds_likely else "review TDS applicability; amount not computed", 8)

    inv_date, due = _parse_date(invoice.get("invoice_date")), _parse_date(invoice.get("due_date"))
    msme_bad = bool(ctx.msme_clock and ctx.vendor_msme and inv_date and due and (due - inv_date).days > 45)
    mark("due_date", "msme_45_day", not msme_bad,
         "within MSME clock" if not msme_bad else "due date exceeds 45 days", 20)
    old = bool(ctx.itc_180_warning and inv_date and (date.today() - inv_date).days > 180)
    mark("invoice_date", "itc_180_day", not old,
         "inside 180-day window" if not old else "invoice older than 180 days", 18)

    total = _money(invoice.get("total"))
    po_needed = ctx.po_required and (ctx.po_required_above is None or total >= ctx.po_required_above)
    mark("po_number", "po_required", not (po_needed and not invoice.get("po_number")),
         "present or not required" if invoice.get("po_number") or not po_needed else "client policy requires PO", 25)
    grn_needed = ctx.match_mode == "three_way"
    mark("grn_number", "grn_required", not (grn_needed and not invoice.get("grn_number")),
         "present or not required" if invoice.get("grn_number") or not grn_needed else "3-way match requires GRN", 20)
    spike = bool(ctx.vendor_median and total > ctx.vendor_median * Decimal("1.75"))
    mark("total", "vendor_amount_spike", not spike,
         "within vendor range" if not spike else f"above 1.75x median {ctx.vendor_median}", 20)
    future = bool(inv_date and inv_date > date.today())
    mark("invoice_date", "future_date", not future, "not future-dated" if not future else "invoice date is in future", 25)

    deterministic = min(100, sum(m.weight for m in marks))
    for raw in llm_marks:
        marks.append(Mark(str(raw.get("field") or "document"), str(raw.get("check") or "llm_mark"),
                          bool(raw.get("ok", False)), str(raw.get("note") or ""), 0))
    score = min(100, max(deterministic, int(llm_score or 0)))
    return RiskResult(score, _band(score), tuple(marks))

