import os
import json

fixtures = [
    {
        "name": "intra_state",
        "markdown": "# INVOICE\nVendor: Local Tech\nGSTIN: 29ABCDE1234F1Z5\nDate: 2026-09-01\nInv No: INV-01\n\n| Item | HSN | Qty | Rate | Taxable | CGST | SGST |\n|---|---|---|---|---|---|---|\n| Laptop | 8471 | 1 | 50000 | 50000 | 4500 | 4500 |\n\nTotal Taxable: 50000\nTotal: 59000",
        "json": {
            "document_id": "doc-intra",
            "document_type": "purchase_invoice",
            "vendor": {"name": "Local Tech", "gstin": "29ABCDE1234F1Z5", "pan": "ABCDE1234F", "state": ""},
            "invoice_no": "INV-01",
            "invoice_date": "2026-09-01",
            "taxable": 50000.0,
            "cgst": 4500.0,
            "sgst": 4500.0,
            "total": 59000.0,
            "line_items": [{"desc": "Laptop", "hsn_sac": "8471", "qty": 1.0, "rate": 50000.0, "taxable": 50000.0, "tax_rate": 18.0, "tax_amount": 9000.0}],
            "confidences": {"invoice_no": 0.9, "date": 0.9, "gstin": 0.9, "total": 0.9}
        }
    },
    {
        "name": "inter_state",
        "markdown": "# INVOICE\nVendor: Delhi Goods\nGSTIN: 07DEFGH5678I1Z3\nDate: 2026-09-02\nInv No: IGST-02\n\n| Item | HSN | Qty | Rate | Taxable | IGST |\n|---|---|---|---|---|---|\n| Monitor | 8528 | 2 | 10000 | 20000 | 3600 |\n\nTotal Taxable: 20000\nTotal: 23600",
        "json": {
            "document_id": "doc-inter",
            "document_type": "purchase_invoice",
            "vendor": {"name": "Delhi Goods", "gstin": "07DEFGH5678I1Z3", "pan": "DEFGH5678I", "state": ""},
            "invoice_no": "IGST-02",
            "invoice_date": "2026-09-02",
            "taxable": 20000.0,
            "igst": 3600.0,
            "total": 23600.0,
            "line_items": [{"desc": "Monitor", "hsn_sac": "8528", "qty": 2.0, "rate": 10000.0, "taxable": 20000.0, "tax_rate": 18.0, "tax_amount": 3600.0}],
            "confidences": {"invoice_no": 0.9, "date": 0.9, "gstin": 0.9, "total": 0.9}
        }
    },
    {
        "name": "debit_note",
        "markdown": "# DEBIT NOTE\nVendor: Supply Co\nGSTIN: 27XYZAB9876C1Z2\nDate: 2026-09-03\nDN No: DN-03\n\n| Item | HSN | Qty | Rate | Taxable | IGST |\n|---|---|---|---|---|---|\n| Return | 1234 | 1 | 5000 | 5000 | 900 |\n\nTotal Taxable: 5000\nTotal: 5900",
        "json": {
            "document_id": "doc-dn",
            "document_type": "debit_note",
            "vendor": {"name": "Supply Co", "gstin": "27XYZAB9876C1Z2", "pan": "XYZAB9876C", "state": ""},
            "invoice_no": "DN-03",
            "invoice_date": "2026-09-03",
            "taxable": 5000.0,
            "igst": 900.0,
            "total": 5900.0,
            "line_items": [{"desc": "Return", "hsn_sac": "1234", "qty": 1.0, "rate": 5000.0, "taxable": 5000.0, "tax_rate": 18.0, "tax_amount": 900.0}],
            "confidences": {"invoice_no": 0.9, "date": 0.9, "gstin": 0.9, "total": 0.9}
        }
    },
    {
        "name": "missing_fields",
        "markdown": "# INVOICE\nVendor: Unknown\nGSTIN: 33AAAAA0000A1Z5\nDate: 2026-09-04\n\n| Item | HSN | Taxable |\n|---|---|---|\n| Service | | 1000 |\n\nTotal Taxable: 1000\nTotal: 1000",
        "json": {
            "document_id": "doc-missing",
            "document_type": "purchase_invoice",
            "vendor": {"name": "Unknown", "gstin": "33AAAAA0000A1Z5", "pan": "AAAAA0000A", "state": ""},
            "invoice_date": "2026-09-04",
            "taxable": 1000.0,
            "total": 1000.0,
            "line_items": [{"desc": "Service", "hsn_sac": "", "qty": 0.0, "rate": 0.0, "taxable": 1000.0, "tax_rate": 0.0, "tax_amount": 0.0}],
            "confidences": {"invoice_no": 0.0, "date": 0.9, "gstin": 0.9, "total": 0.9}
        }
    },
    {
        "name": "mismatch",
        "markdown": "# INVOICE\nVendor: Mismatch Co\nGSTIN: 29MMMMM1111M1Z1\nDate: 2026-09-05\nInv No: MIS-05\n\n| Item | HSN | Taxable |\n|---|---|---|\n| Item A | 1111 | 3000 |\n| Item B | 2222 | 4000 |\n\nTotal Taxable: 8000\nTotal: 9440",
        "json": {
            "document_id": "doc-mismatch",
            "document_type": "purchase_invoice",
            "vendor": {"name": "Mismatch Co", "gstin": "29MMMMM1111M1Z1", "pan": "MMMMM1111M", "state": ""},
            "invoice_no": "MIS-05",
            "invoice_date": "2026-09-05",
            "taxable": 8000.0,
            "total": 9440.0,
            "line_items": [
                {"desc": "Item A", "hsn_sac": "1111", "qty": 0.0, "rate": 0.0, "taxable": 3000.0, "tax_rate": 0.0, "tax_amount": 0.0},
                {"desc": "Item B", "hsn_sac": "2222", "qty": 0.0, "rate": 0.0, "taxable": 4000.0, "tax_rate": 0.0, "tax_amount": 0.0}
            ],
            "confidences": {"invoice_no": 0.9, "date": 0.9, "gstin": 0.9, "total": 0.9}
        }
    }
]

def main():
    base = os.path.join(os.path.dirname(__file__), "packages", "extract", "tests", "fixtures")
    os.makedirs(base, exist_ok=True)
    
    for fix in fixtures:
        with open(os.path.join(base, f"{fix['name']}.md"), "w") as f:
            f.write(fix['markdown'])
        with open(os.path.join(base, f"{fix['name']}.json"), "w") as f:
            json.dump(fix['json'], f, indent=2)

if __name__ == "__main__":
    main()
