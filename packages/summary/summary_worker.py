import os
from contracts.schemas import CanonicalInvoice, VoucherDraft, RiskScore
from usage.model_router import ModelRouter
from usage.usage_writer import UsageWriter

class SummaryWorker:
    def __init__(self, usage_writer: UsageWriter):
        self.usage_writer = usage_writer
        self.router = ModelRouter()

    def _call_llm(self, model: str, prompt: str) -> str:
        # Mock LLM for summary
        return "Draft created. Total amount: 100.0. Vendor: V1."

    def generate_summary(self, client_id: str, invoice: CanonicalInvoice, draft: VoucherDraft, risk: RiskScore) -> str:
        model = self.router.resolve_model("summary")
        
        prompt = f"""
        Summarize the following FinLedger processing for a human reviewer.
        Invoice: {invoice.model_dump_json()}
        Draft: {draft.model_dump_json()}
        Risk: {risk.model_dump_json()}
        """
        
        response_text = self._call_llm(model, prompt)
        
        # Log usage
        tokens_in = len(prompt) // 4
        tokens_out = len(response_text) // 4
        
        self.usage_writer.log_usage(
            client_id=client_id,
            doc_id=invoice.document_id,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=500,
            cost_usd=0.0,
            purpose="summary"
        )
        
        return response_text.strip()
