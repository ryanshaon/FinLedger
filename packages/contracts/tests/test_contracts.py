import json
import os
from contracts.schemas import CanonicalInvoice

def test_golden_intra_gst_18():
    path = os.path.join(os.path.dirname(__file__), "..", "golden", "intra_gst_18.json")
    with open(path, "r") as f:
        data = json.load(f)
    invoice = CanonicalInvoice(**data)
    assert invoice.document_id == "doc-intra-18"
    assert invoice.vendor.gstin == "29ABCDE1234F1Z5"
    assert invoice.taxable == 10000.0
    assert invoice.cgst == 900.0

def test_golden_igst_import():
    path = os.path.join(os.path.dirname(__file__), "..", "golden", "igst_import.json")
    with open(path, "r") as f:
        data = json.load(f)
    invoice = CanonicalInvoice(**data)
    assert invoice.document_id == "doc-igst"
    assert invoice.vendor.gstin == "07DEFGH5678I1Z3"
    assert invoice.igst == 9000.0

def test_golden_professional_194j():
    path = os.path.join(os.path.dirname(__file__), "..", "golden", "professional_194j.json")
    with open(path, "r") as f:
        data = json.load(f)
    invoice = CanonicalInvoice(**data)
    assert invoice.document_id == "doc-prof-194j"
    assert invoice.line_items[0].hsn_sac == "9982"
    assert invoice.total == 236000.0
