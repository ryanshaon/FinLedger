import os
import json
import pytest
from unittest.mock import patch, mock_open
from extract.extract_worker import ExtractWorker
from usage.usage_writer import UsageWriter
from contracts.schemas import CanonicalInvoice

def get_fixture_data(name):
    base = os.path.join(os.path.dirname(__file__), "fixtures")
    with open(os.path.join(base, f"{name}.md"), "r") as f:
        markdown = f.read()
    with open(os.path.join(base, f"{name}.json"), "r") as f:
        expected_json = f.read()
    return markdown, expected_json

@patch('extract.extract_worker.ExtractWorker._call_llm')
def test_extract_valid_invoice(mock_call_llm):
    markdown, expected_json = get_fixture_data("intra_state")
    mock_call_llm.return_value = expected_json
    
    writer = UsageWriter()
    worker = ExtractWorker(writer)
    invoice = worker.extract("client1", "doc-intra", markdown)
    
    assert invoice.document_id == "doc-intra"
    assert invoice.vendor.gstin == "29ABCDE1234F1Z5"
    assert invoice.total == 59000.0
    assert len(writer.logs) == 1
    assert invoice.confidences.total == 0.9  # from fixture

@patch('extract.extract_worker.ExtractWorker._call_llm')
def test_mismatch_total(mock_call_llm):
    markdown, expected_json = get_fixture_data("mismatch")
    mock_call_llm.return_value = expected_json
    
    writer = UsageWriter()
    worker = ExtractWorker(writer)
    invoice = worker.extract("client1", "doc-mismatch", markdown)
    
    # Line items sum = 7000.0, but header taxable = 8000.0 -> mismatch
    assert invoice.taxable == 8000.0
    assert invoice.confidences.total == 0.5  # Modified by post-processing

@patch('extract.extract_worker.ExtractWorker._call_llm')
def test_gstin_format_validation(mock_call_llm):
    markdown, expected_json = get_fixture_data("missing_fields")
    mock_call_llm.return_value = expected_json
    
    writer = UsageWriter()
    worker = ExtractWorker(writer)
    invoice = worker.extract("client1", "doc-missing", markdown)
    
    import re
    assert re.match(r"\d{2}[A-Z]{5}\d{4}[A-Z]{1}\d{1}[A-Z]{1}\d{1}", invoice.vendor.gstin)
