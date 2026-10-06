from contracts.schemas import CanonicalInvoice, MapResult, Vendor
from mapper.map_worker import MapWorker
from rag.indexer import Indexer
from rag.retriever import Retriever
from usage.usage_writer import UsageWriter


def test_mapper_returns_typed_result_and_legacy_draft():
    indexer = Indexer()
    invoice = CanonicalInvoice(
        document_id="doc-map-runtime", document_type="purchase_invoice",
        vendor=Vendor(name="Vendor X", gstin="29ABCDE1234F1Z5"),
        invoice_no="INV-9", invoice_date="2026-09-01", due_date=None,
        total=118.0, taxable=100.0,
    )
    worker = MapWorker(UsageWriter(), Retriever(indexer))
    result = worker.map_invoice_result("client-a", invoice, "default")
    assert isinstance(result, MapResult)
    assert result.draft.map_trace_id == result.trace.map_trace_id
    assert result.trace.document_id == invoice.document_id
    assert worker.map_invoice("client-a", invoice, "default").document_id == invoice.document_id

