import json
import os
from schemas import (
    CanonicalInvoice,
    RiskScore,
    VoucherDraft,
    CorrectionEvent,
    MapTrace,
    MapResult,
)

def export():
    out_dir = os.path.join(os.path.dirname(__file__), "json_schemas")
    os.makedirs(out_dir, exist_ok=True)
    
    models = {
        "canonical_invoice.json": CanonicalInvoice,
        "risk_score.json": RiskScore,
        "voucher_draft.json": VoucherDraft,
        "correction_event.json": CorrectionEvent,
        "map_trace.json": MapTrace,
        "map_result.json": MapResult,
    }
    
    for filename, model in models.items():
        path = os.path.join(out_dir, filename)
        schema = model.model_json_schema()
        with open(path, "w") as f:
            json.dump(schema, f, indent=2)

if __name__ == "__main__":
    export()
