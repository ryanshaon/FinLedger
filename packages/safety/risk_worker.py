import json
import os
import re
from contracts.schemas import CanonicalInvoice, VoucherDraft, RiskScore
from usage.model_router import ModelRouter
from usage.usage_writer import UsageWriter

class RiskWorker:
    def __init__(self, usage_writer: UsageWriter, router: ModelRouter | None = None):
        self.usage_writer = usage_writer
        self.router = router or ModelRouter()
        self.prompt_path = os.path.join(os.path.dirname(__file__), "prompts", "risk_prompt.txt")

    def _call_llm(self, model: str, prompt: str, invoice_json: str, draft_json: str) -> str:
        response = self.router.provider.complete("risk_llm", model, self._client_id, self._document_id, {
            "prompt": prompt, "invoice": json.loads(invoice_json), "draft": json.loads(draft_json)})
        self._provider_response = response
        return response.text

    def assess_risk(self, client_id: str, invoice: CanonicalInvoice, draft: VoucherDraft) -> RiskScore:
        model = self.router.resolve_model("risk_llm")
        self._document_id = invoice.document_id
        self._client_id = client_id
        
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
            tokens_in=getattr(getattr(self, "_provider_response", None), "tokens_in", max(1, tokens_in)),
            tokens_out=getattr(getattr(self, "_provider_response", None), "tokens_out", max(1, tokens_out)),
            latency_ms=getattr(getattr(self, "_provider_response", None), "latency_ms", 800),
            cost_usd=getattr(getattr(self, "_provider_response", None), "cost_usd", 0.0),
            purpose="risk_llm"
        )
        
        clean_json = re.sub(r'```json|```', '', response_text).strip()
        data = json.loads(clean_json)

        if data.get("document_id") != invoice.document_id:
            raise ValueError("Risk document_id must match invoice document_id")
        return RiskScore(**data)
