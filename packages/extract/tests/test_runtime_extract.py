from pathlib import Path

import pytest

from extract.extract_worker import ExtractWorker
from extract.vision_fallback import ExtractionDispatcher
from usage.usage_writer import UsageWriter


FIXTURES = Path(__file__).parent / "fixtures"


def test_mock_extract_runs_without_monkeypatch():
    markdown = (FIXTURES / "intra_state.md").read_text()
    invoice = ExtractWorker(UsageWriter()).extract("client-a", "doc-runtime", markdown)
    assert invoice.document_id == "doc-runtime"
    assert invoice.invoice_no == "INV-01"
    assert invoice.total == 59000.0


def test_dispatcher_uses_markdown_without_images():
    dispatcher = ExtractionDispatcher(ExtractWorker(UsageWriter()), page_cap=2)
    invoice = dispatcher.extract("client-a", "doc-digital", "Invoice No: A-1\nTotal: 100", ["p1.png"])
    assert invoice.invoice_no == "A-1"
    assert invoice.page_image_paths == []


def test_dispatcher_uses_capped_images_when_markdown_empty():
    dispatcher = ExtractionDispatcher(ExtractWorker(UsageWriter()), page_cap=2)
    invoice = dispatcher.extract("client-a", "doc-scan", "  ", ["p1.png", "p2.png"])
    assert invoice.document_id == "doc-scan"
    assert invoice.page_image_paths == ["p1.png", "p2.png"]
    with pytest.raises(ValueError, match="page cap"):
        dispatcher.extract("client-a", "doc-over", "", ["1", "2", "3"])

