import json

from contracts.schemas import CanonicalInvoice
from extract.extract_worker import ExtractWorker


class ExtractionDispatcher:
    def __init__(self, worker: ExtractWorker, page_cap: int = 10):
        if page_cap < 1:
            raise ValueError("page_cap must be positive")
        self.worker = worker
        self.page_cap = page_cap

    def extract(self, client_id: str, document_id: str, markdown: str, page_image_paths: list[str]) -> CanonicalInvoice:
        if markdown.strip():
            return self.worker.extract(client_id, document_id, markdown)
        if not page_image_paths:
            raise ValueError("No markdown or page images available for extraction")
        if len(page_image_paths) > self.page_cap:
            raise ValueError(f"Vision page cap exceeded: {len(page_image_paths)} > {self.page_cap}")
        model = self.worker.router.resolve_model("extract_vision")
        response = self.worker.router.provider.complete("extract_vision", model, client_id, document_id, {
            "prompt": "Extract the invoice from page images", "markdown": "", "page_image_paths": page_image_paths,
        })
        data = json.loads(response.text)
        self.worker.usage_writer.log_usage(client_id, document_id, model, response.tokens_in, response.tokens_out,
            response.latency_ms, response.cost_usd, "extract_vision")
        return CanonicalInvoice(**data)
