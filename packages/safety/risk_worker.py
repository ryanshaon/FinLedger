import json
import os
import re
from contracts.schemas import CanonicalInvoice, VoucherDraft, RiskScore
from usage.model_router import ModelRouter
from usage.usage_writer import UsageWriter

class RiskWorker:
    def __init__(self, usage_writer: UsageWriter):
        self.usage_writer = usage_writer
        self.router = ModelRouter()
        self.prompt_path = os.path.join(os.path.dirname(__file__), "prompts", "risk_prompt.txt")

    def _call_llm(self, model: str, prompt: str, invoice_json: str, draft_json: str) -> str:
        # Mock LLM for safety assessment
        return "{}"

    def assess_risk(self, client_id: str, invoice: CanonicalInvoice, draft: VoucherDraft) -> RiskScore:
        model = self.router.resolve_model("risk_llm")
        
        with open(self.prompt_path, "r") as f:
            prompt_template = f.read()

        inv_json = invoice.model_dump_json()
        dr_json = draft.model_dump_json()
        
        response_text = self._call_llm(model, prompt_template, inv_json, dr_json)
        
        # Log usage
        tokens_in = len(prompt_template + inv_json + dr_json) // 4
        tokens_out = len(response_text) // 4
        
        self.usage_writer.log_usage(
            client_id=client_id,
            doc_id=invoice.document_id,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=800,
            cost_usd=0.0,
            purpose="risk_llm"
        )
        
        clean_json = re.sub(r'```json|```', '', response_text).strip()
        data = json.loads(clean_json)

        return RiskScore(**data)
