import json
import os
import re
from typing import Optional
from contracts.schemas import CanonicalInvoice
from usage.model_router import ModelRouter
from usage.usage_writer import UsageWriter

class ExtractWorker:
    def __init__(self, usage_writer: UsageWriter):
        self.usage_writer = usage_writer
        self.router = ModelRouter()
        self.prompt_path = os.path.join(os.path.dirname(__file__), "prompts", "extract_digital.txt")

    def _call_llm(self, model: str, prompt: str, markdown: str) -> str:
        # In a real environment, call the LLM API here.
        # For tests, this will be mocked.
        return "{}"

    def extract(self, client_id: str, document_id: str, markdown: str) -> CanonicalInvoice:
        model = self.router.resolve_model("extract")
        
        with open(self.prompt_path, "r") as f:
            prompt_template = f.read()
            
        # Call LLM
        response_text = self._call_llm(model, prompt_template, markdown)
        
        # Log usage (stubbing tokens for now)
        self.usage_writer.log_usage(
            client_id=client_id,
            doc_id=document_id,
            model=model,
            tokens_in=len(markdown) // 4,
            tokens_out=len(response_text) // 4,
            latency_ms=1000,
            cost_usd=0.0,
            purpose="extract"
        )
        
        # Clean response text in case of markdown blocks
        clean_json = re.sub(r'```json|```', '', response_text).strip()
        
        try:
            data = json.loads(clean_json)
        except json.JSONDecodeError:
            raise ValueError("LLM did not return valid JSON")

        invoice = CanonicalInvoice(**data)
        
        # Post-processing: Line-sum mismatch check
        total_line_taxable = sum(item.taxable for item in invoice.line_items)
        # using a small epsilon for float comparison
        if abs(total_line_taxable - invoice.taxable) > 0.01:
            invoice.confidences.total = 0.5  # Mark low confidence
            
        return invoice
