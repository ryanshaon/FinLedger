import json
import os
import re
from typing import Dict, Any, List
import uuid

from contracts.schemas import CanonicalInvoice, VoucherDraft
from usage.model_router import ModelRouter
from usage.usage_writer import UsageWriter
from rag.retriever import Retriever

class MapWorker:
    def __init__(self, usage_writer: UsageWriter, retriever: Retriever):
        self.usage_writer = usage_writer
        self.router = ModelRouter()
        self.retriever = retriever
        self.prompt_path = os.path.join(os.path.dirname(__file__), "prompts", "map_prompt.txt")

    def _call_llm(self, model: str, prompt: str, invoice_data: str, context: str, policy: str) -> str:
        # Mock LLM behavior
        return "{}"

    def map_invoice(self, client_id: str, invoice: CanonicalInvoice, policy: str) -> VoucherDraft:
        model = self.router.resolve_model("map")
        
        with open(self.prompt_path, "r") as f:
            prompt_template = f.read()

        # Gather fields to query retriever
        desc = [item.desc for item in invoice.line_items]
        hsn = invoice.line_items[0].hsn_sac if invoice.line_items else ""
        amount_band = str(invoice.total)
        
        chunks = self.retriever.retrieve(
            client_id=client_id,
            gstin=invoice.vendor.gstin,
            vendor_name=invoice.vendor.name,
            descriptions=desc,
            hsn=hsn,
            amount_band=amount_band,
            doc_type=invoice.document_type
        )
        
        chunk_context = json.dumps(chunks)
        invoice_json = invoice.model_dump_json()

        # LLM Call
        response_text = self._call_llm(model, prompt_template, invoice_json, chunk_context, policy)
        
        # Log usage
        tokens_in = len(prompt_template + invoice_json + chunk_context + policy) // 4
        tokens_out = len(response_text) // 4
        self.usage_writer.log_usage(
            client_id=client_id,
            doc_id=invoice.document_id,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=1200,
            cost_usd=0.0,
            purpose="map"
        )
        
        clean_json = re.sub(r'```json|```', '', response_text).strip()
        data = json.loads(clean_json)

        # Build MapTrace
        map_trace_id = str(uuid.uuid4())
        data["map_trace_id"] = map_trace_id

        return VoucherDraft(**data)
