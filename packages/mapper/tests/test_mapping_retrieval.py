"""Mapping cases: party-ledger memory, isolation, and VoucherDraft JSON."""
import json
import uuid
from pathlib import Path
from unittest.mock import patch

from contracts.schemas import CanonicalInvoice, CorrectionEvent, LineItem, Vendor, VoucherDraft
from mapper.map_worker import MapWorker, apply_party_ledger_memory
from memory.memory_indexer import MemoryIndexer
from rag.chunk_types import ChunkType
from rag.indexer import Indexer
from rag.library import seed_client_library
from rag.retriever import Retriever
from usage.usage_writer import UsageWriter

GSTIN = "29ABCDE1234F1Z5"
OTHER_GSTIN = "24BBBBB0000B1Z8"
WRONG = "Purchases - Bearings"
CORRECTED = "SKF India - Sundry Creditors"
OTHER_LEDGER = "Bharat Bearings - Sundry Creditors"
_SCHEMA = Path(__file__).resolve().parents[2] / "contracts" / "json_schemas" / "voucher_draft.json"


def _bill(document_id, *, gstin=GSTIN, name="SKF Bearings", desc="Bearings", hsn="8482", total=1180.0):
    return CanonicalInvoice(
        document_id=document_id,
        document_type="purchase_invoice",
        vendor=Vendor(name=name, gstin=gstin),
        invoice_no=f"INV-{document_id}",
        invoice_date="2026-10-02",
        due_date="2026-11-01",
        total=total,
        taxable=total,
        line_items=[LineItem(desc=desc, hsn_sac=hsn, qty=1, rate=total, taxable=total)],
    )


def _correction(store, client_id, document_id, new_ledger, *, field="party_ledger", gstin=GSTIN,
                description="Bearings", hsn="8482", old_ledger=WRONG):
    return MemoryIndexer(store).index_correction(CorrectionEvent(
        client_id=client_id, document_id=document_id, vendor_gstin=gstin, field=field,
        hsn_sac=hsn, description=description, old_ledger=old_ledger, new_ledger=new_ledger,
    ))


def _fake(party, prompts):
    def fake(_model, prompt, invoice_data, _context, _policy):
        invoice = json.loads(invoice_data)
        prompts.append(prompt)
        document_id = invoice["document_id"]
        return json.dumps({
            "document_id": document_id,
            "date": invoice.get("invoice_date") or "2026-10-02",
            "voucher_type": "purchase",
            "party_ledger": party,
            "party_create_proposal": False,
            "lines": [{"ledger": "Purchases", "amount": invoice.get("total", 0), "is_debit": True}],
            "gst": {"treatment": "intra", "input_ledgers": [], "tax_breakup": {}},
            "tds": {"applicable": False, "amount": 0.0},
            "bill_wise": {
                "ref_type": "new_ref",
                "ref": invoice.get("invoice_no") or document_id,
                "due_date": invoice.get("due_date"),
            },
            "narration": "Being purchase",
            "attachment_document_id": document_id,
            "reasons": ["model kept its party ledger"],
        })
    return fake


def _map(store, client_id, invoice, party=WRONG):
    prompts = []
    worker = MapWorker(UsageWriter(), Retriever(store))
    with patch.object(MapWorker, "_call_llm", side_effect=_fake(party, prompts)):
        result = worker.map_invoice_result(client_id, invoice, "policy")
    return result, prompts


def _ranked(store, invoice, client_id="client-a"):
    return Retriever(store).retrieve(
        client_id, invoice.vendor.gstin, invoice.vendor.name,
        [item.desc for item in invoice.line_items],
        invoice.line_items[0].hsn_sac if invoice.line_items else "",
        str(invoice.total), invoice.document_type, top_k=20,
    )


def _assert_voucher_draft_json(draft, *, party_ledger):
    payload = json.loads(draft.model_dump_json())
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    assert schema["title"] == "VoucherDraft"
    assert schema["required"] == [
        "document_id", "date", "voucher_type", "party_ledger",
        "bill_wise", "attachment_document_id", "map_trace_id",
    ]
    assert isinstance(payload, dict)
    for key in schema["required"]:
        assert key in payload
    assert payload["party_ledger"] == party_ledger
    assert payload["voucher_type"] in schema["properties"]["voucher_type"]["enum"]
    assert isinstance(payload["document_id"], str) and payload["document_id"]
    assert isinstance(payload["date"], str) and payload["date"]
    assert isinstance(payload["party_ledger"], str) and payload["party_ledger"]
    uuid.UUID(payload["map_trace_id"])
    assert payload["attachment_document_id"] == payload["document_id"]
    assert payload["map_trace_id"] == draft.map_trace_id
    assert isinstance(payload["party_create_proposal"], bool)
    assert isinstance(payload["narration"], str)
    assert payload["reasons"] and all(isinstance(item, str) for item in payload["reasons"])
    assert "chunks" not in payload
    assert "retrieved_chunk_ids" not in payload

    bill_schema = schema["$defs"]["BillWise"]
    bill = payload["bill_wise"]
    for key in bill_schema["required"]:
        assert key in bill
    assert bill["ref_type"] in bill_schema["properties"]["ref_type"]["enum"]
    assert isinstance(bill["ref"], str) and bill["ref"]

    gst = payload["gst"]
    assert gst["treatment"] in schema["$defs"]["GstDetails"]["properties"]["treatment"]["enum"]
    assert isinstance(gst["input_ledgers"], list)
    assert all(isinstance(item, str) for item in gst["input_ledgers"])
    assert isinstance(gst["tax_breakup"], dict)

    tds = payload["tds"]
    assert isinstance(tds["applicable"], bool)
    assert isinstance(tds["amount"], (int, float)) and not isinstance(tds["amount"], bool)

    line_schema = schema["$defs"]["VoucherLine"]
    assert payload["lines"]
    for line in payload["lines"]:
        for key in line_schema["required"]:
            assert key in line
        assert isinstance(line["ledger"], str) and line["ledger"]
        assert isinstance(line["amount"], (int, float)) and not isinstance(line["amount"], bool)
        assert isinstance(line["is_debit"], bool)

    again = VoucherDraft.model_validate(payload)
    assert again.party_ledger == party_ledger
    assert again.document_id == draft.document_id
    assert again.bill_wise.ref == draft.bill_wise.ref
    return payload


def test_correction_wins_the_next_similar_bill():
    store = Indexer()
    seed_client_library(store, "client-a")
    correction = _correction(store, "client-a", "doc-prev", CORRECTED)
    narration = _correction(
        store, "client-a", "doc-note", CORRECTED, field="narration", old_ledger="old note",
    )
    store.upsert(
        "client-a", "ledger:purchases", ChunkType.LEDGER,
        f"Ledger {WRONG}. Parent group Purchase Accounts.", {"name": WRONG},
    )
    posted = MemoryIndexer(store).index_posted_bill(
        "client-a", "doc-posted", "erp-9", GSTIN, "Bearings", "8482", 1180.0,
    )
    invoice = _bill("doc-next")
    result, prompts = _map(store, "client-a", invoice)

    assert result.draft.party_ledger == CORRECTED
    assert result.draft.party_ledger != WRONG
    assert result.trace.client_id == "client-a"
    assert result.trace.document_id == "doc-next"
    assert result.trace.party_ledger_chunk_id == correction["chunk_id"]
    assert result.trace.party_ledger_chunk_id != narration["chunk_id"]
    assert result.trace.party_ledger_chunk_id != posted["chunk_id"]
    assert result.trace.party_ledger_from_memory == CORRECTED
    assert correction["chunk_id"] in result.trace.retrieved_chunk_ids
    assert posted["chunk_id"] in result.trace.retrieved_chunk_ids
    namespace = {chunk["chunk_id"] for chunk in store.get_namespace_chunks("client-a")}
    assert set(result.trace.retrieved_chunk_ids) <= namespace
    assert result.draft.lines[0].ledger == "Purchases"
    assert result.draft.lines[0].amount == 1180.0
    assert result.draft.bill_wise.ref == "INV-doc-next"
    assert result.draft.party_create_proposal is False
    assert f"set party_ledger to {CORRECTED} from memory chunk {correction['chunk_id']}" in prompts[0]

    ranked = {row["chunk_id"]: row for row in _ranked(store, invoice)}
    assert ranked[correction["chunk_id"]]["score"] > ranked["ledger:purchases"]["score"]
    assert ranked[correction["chunk_id"]]["score"] > 0
    assert "gstin" in ranked[correction["chunk_id"]]["ranking_reasons"]


def test_correction_does_not_touch_an_unrelated_bill():
    store = Indexer()
    seed_client_library(store, "client-a")
    correction = _correction(store, "client-a", "doc-prev", CORRECTED)
    invoice = _bill(
        "doc-other", gstin=OTHER_GSTIN, name="Quilted Cotton Mills",
        desc="Calico bolts", hsn="5208", total=50.0,
    )
    result, prompts = _map(store, "client-a", invoice)
    ranked = {row["chunk_id"]: row for row in _ranked(store, invoice)}

    assert correction["chunk_id"] not in result.trace.retrieved_chunk_ids
    assert correction["chunk_id"] not in ranked
    assert result.draft.party_ledger == WRONG
    assert result.draft.party_ledger != CORRECTED
    assert result.trace.party_ledger_from_memory is None
    assert result.trace.party_ledger_chunk_id is None
    assert "Required: set party_ledger" not in prompts[0]
    assert result.draft.lines[0].ledger == "Purchases"
    assert result.draft.bill_wise.ref == "INV-doc-other"


def test_party_correction_cannot_override_a_different_known_vendor_selling_the_same_goods():
    store = Indexer()
    correction = _correction(store, "client-a", "doc-prev", CORRECTED)
    invoice = _bill("doc-other-vendor", gstin=OTHER_GSTIN, name="Bharat Bearings")

    result, prompts = _map(store, "client-a", invoice, party=OTHER_LEDGER)

    assert result.draft.party_ledger == OTHER_LEDGER
    assert result.trace.party_ledger_from_memory is None
    assert correction["chunk_id"] not in result.trace.retrieved_chunk_ids
    assert "Required: set party_ledger" not in prompts[0]


def test_newer_equally_relevant_correction_wins_instead_of_hash_order():
    store = Indexer()
    newer = MemoryIndexer(store).index_correction(CorrectionEvent(
        client_id="client-a", document_id="doc-new", vendor_gstin=GSTIN,
        field="party_ledger", hsn_sac="8482", description="Bearings",
        old_ledger=WRONG, new_ledger="Current creditors",
        created_at="2026-11-02T12:00:00+00:00",
    ))
    older = MemoryIndexer(store).index_correction(CorrectionEvent(
        client_id="client-a", document_id="doc-old", vendor_gstin=GSTIN,
        field="party_ledger", hsn_sac="8482", description="Bearings",
        old_ledger=WRONG, new_ledger="Superseded creditors",
        created_at="2026-11-01T12:00:00+00:00",
    ))
    # Force the old record first so neither insertion order nor a hash is recency.
    store.get_namespace_chunks("client-a").reverse()
    result, _ = _map(store, "client-a", _bill("doc-after-corrections"))

    assert result.draft.party_ledger == "Current creditors"
    assert result.trace.party_ledger_chunk_id == newer["chunk_id"]
    assert result.trace.party_ledger_chunk_id != older["chunk_id"]


def test_party_ledger_override_is_isolated_by_client():
    store = Indexer()
    correction_a = _correction(store, "client-a", "doc-a-prev", CORRECTED)
    correction_b = _correction(store, "client-b", "doc-b-prev", OTHER_LEDGER)
    result_a, prompts_a = _map(store, "client-a", _bill("doc-a-next"))
    result_b, prompts_b = _map(store, "client-b", _bill("doc-b-next"))
    result_c, prompts_c = _map(store, "client-c", _bill("doc-c-next"))

    assert result_a.draft.party_ledger == CORRECTED
    assert result_a.trace.party_ledger_chunk_id == correction_a["chunk_id"]
    assert result_a.trace.party_ledger_from_memory == CORRECTED
    assert correction_b["chunk_id"] not in result_a.trace.retrieved_chunk_ids
    assert OTHER_LEDGER not in prompts_a[0]
    assert correction_a["chunk_id"] in prompts_a[0]

    assert result_b.draft.party_ledger == OTHER_LEDGER
    assert result_b.trace.party_ledger_chunk_id == correction_b["chunk_id"]
    assert result_b.trace.party_ledger_from_memory == OTHER_LEDGER
    assert correction_a["chunk_id"] not in result_b.trace.retrieved_chunk_ids
    assert CORRECTED not in result_b.draft.party_ledger
    assert correction_a["chunk_id"] not in prompts_b[0]
    assert correction_b["chunk_id"] in prompts_b[0]

    assert result_c.draft.party_ledger == WRONG
    assert result_c.trace.retrieved_chunk_ids == []
    assert result_c.trace.party_ledger_chunk_id is None
    assert result_c.trace.party_ledger_from_memory is None
    assert "Required: set party_ledger" not in prompts_c[0]
    assert set(result_a.trace.retrieved_chunk_ids).isdisjoint(result_b.trace.retrieved_chunk_ids)


def test_missing_gstin_does_not_apply_a_gstin_only_correction():
    store = Indexer()
    correction = _correction(store, "client-a", "doc-prev", CORRECTED)
    invoice = _bill(
        "doc-blank", gstin="", name="Quilted Cotton Mills", desc="Calico bolts", hsn="5208", total=50.0,
    )
    result, prompts = _map(store, "client-a", invoice)
    ranked = {row["chunk_id"]: row for row in _ranked(store, invoice)}

    assert "gstin" not in ranked[correction["chunk_id"]]["ranking_reasons"]
    assert ranked[correction["chunk_id"]]["ranking_reasons"] == []
    assert result.draft.party_ledger == WRONG
    assert result.trace.party_ledger_from_memory is None
    assert result.trace.party_ledger_chunk_id is None
    assert "Required: set party_ledger" not in prompts[0]


def test_missing_gstin_still_overrides_when_description_and_hsn_match():
    store = Indexer()
    correction = _correction(store, "client-a", "doc-prev", CORRECTED)
    invoice = _bill("doc-blank-similar", gstin="", name="SKF Bearings", desc="Bearings", hsn="8482")
    result, prompts = _map(store, "client-a", invoice)
    ranked = _ranked(store, invoice)
    row = next(item for item in ranked if item["chunk_id"] == correction["chunk_id"])

    assert "gstin" not in row["ranking_reasons"]
    assert "description" in row["ranking_reasons"]
    assert "hsn" in row["ranking_reasons"]
    assert row["score"] > 2
    assert result.draft.party_ledger == CORRECTED
    assert result.trace.party_ledger_chunk_id == correction["chunk_id"]
    assert result.trace.party_ledger_from_memory == CORRECTED
    assert f"set party_ledger to {CORRECTED} from memory chunk {correction['chunk_id']}" in prompts[0]


def test_empty_history_keeps_the_model_party_ledger():
    result, prompts = _map(Indexer(), "client-a", _bill("doc-empty"))
    assert result.trace.retrieved_chunk_ids == []
    assert result.draft.party_ledger == WRONG
    assert result.trace.party_ledger_chunk_id is None
    assert result.trace.party_ledger_from_memory is None
    assert result.draft.lines[0].ledger == "Purchases"
    assert result.draft.bill_wise.ref == "INV-doc-empty"
    assert "Required: set party_ledger" not in prompts[0]
    assert "chunks" not in result.draft.model_dump()


def test_duplicate_memory_ref_upsert_uses_the_replacement_ledger():
    store = Indexer()
    correction = _correction(store, "client-a", "doc-prev", CORRECTED)
    replacement = "Replacement Creditors"
    store.upsert(
        "client-a", correction["chunk_id"], ChunkType.MEMORY,
        f"Correction for {GSTIN}: Bearings HSN 8482; party_ledger changed from {WRONG} to {replacement}",
        {**correction["metadata"], "new_ledger": replacement},
    )
    assert len(store.get_namespace_chunks("client-a")) == 1
    assert store.get_namespace_chunks("client-a")[0]["metadata"]["new_ledger"] == replacement

    result, prompts = _map(store, "client-a", _bill("doc-next"))
    assert result.draft.party_ledger == replacement
    assert result.draft.party_ledger != CORRECTED
    assert result.trace.party_ledger_chunk_id == correction["chunk_id"]
    assert result.trace.party_ledger_from_memory == replacement
    assert len(result.trace.retrieved_chunk_ids) == 1
    assert f"set party_ledger to {replacement} from memory chunk {correction['chunk_id']}" in prompts[0]


def test_similar_bill_map_output_is_valid_voucher_draft_json():
    store = Indexer()
    correction = _correction(store, "client-a", "doc-prev", CORRECTED)
    result, _prompts = _map(store, "client-a", _bill("doc-json"))
    payload = _assert_voucher_draft_json(result.draft, party_ledger=CORRECTED)
    assert payload["document_id"] == "doc-json"
    assert payload["attachment_document_id"] == "doc-json"
    assert payload["voucher_type"] == "purchase"
    assert payload["bill_wise"]["ref"] == "INV-doc-json"
    assert payload["bill_wise"]["ref_type"] == "new_ref"
    assert payload["lines"] == [{"ledger": "Purchases", "amount": 1180.0, "is_debit": True, "cost_centre": None}]
    assert payload["gst"]["treatment"] == "intra"
    assert payload["tds"]["applicable"] is False
    assert result.trace.party_ledger_chunk_id == correction["chunk_id"]
    assert result.draft.map_trace_id == result.trace.map_trace_id


def test_empty_history_map_output_is_valid_voucher_draft_json():
    result, _prompts = _map(Indexer(), "client-a", _bill("doc-json-empty"))
    payload = _assert_voucher_draft_json(result.draft, party_ledger=WRONG)
    assert payload["document_id"] == "doc-json-empty"
    assert payload["party_ledger"] == WRONG
    assert payload["lines"][0]["ledger"] == "Purchases"
    assert payload["lines"][0]["amount"] == 1180.0
    assert payload["bill_wise"]["ref"] == "INV-doc-json-empty"
    assert result.trace.retrieved_chunk_ids == []
    assert result.trace.party_ledger_from_memory is None
    assert result.draft.map_trace_id == result.trace.map_trace_id


def test_override_rejects_zero_score_and_reads_string_chunk_type():
    draft = {"party_ledger": WRONG, "document_id": "doc-unit"}
    zero = {
        "chunk_id": "zero", "chunk_type": "memory", "score": 0, "ranking_reasons": ["gstin"],
        "metadata": {"field": "party_ledger", "new_ledger": "Should Not Apply"}, "content": "",
    }
    bonus_only = {
        "chunk_id": "bonus", "chunk_type": "memory", "score": 2, "ranking_reasons": [],
        "metadata": {"field": "party_ledger", "new_ledger": "Should Not Apply"},
        "content": "party_ledger changed from Purchases to Should Not Apply",
    }
    ledger = {
        "chunk_id": "ledger", "chunk_type": "ledger", "score": 20, "ranking_reasons": ["gstin"],
        "metadata": {"field": "party_ledger", "new_ledger": "Should Not Apply"},
        "content": "party_ledger changed from Purchases to Should Not Apply",
    }
    assert apply_party_ledger_memory(draft, [zero, bonus_only, ledger]) is None
    assert draft["party_ledger"] == WRONG

    applied = apply_party_ledger_memory(draft, [zero, {
        "chunk_id": "kept", "chunk_type": "memory", "score": 10, "ranking_reasons": ["gstin"],
        "metadata": {"field": "party_ledger", "new_ledger": CORRECTED}, "content": "",
    }])
    assert applied == ("kept", CORRECTED)
    assert draft["party_ledger"] == CORRECTED

    enum_draft = {"party_ledger": WRONG}
    enum_chunk = {
        "chunk_id": "enum", "chunk_type": ChunkType.MEMORY, "score": 9,
        "ranking_reasons": ["description"], "metadata": {"field": "Party_Ledger", "new_ledger": OTHER_LEDGER},
        "content": "",
    }
    assert apply_party_ledger_memory(enum_draft, [enum_chunk]) == ("enum", OTHER_LEDGER)
    assert enum_draft["party_ledger"] == OTHER_LEDGER
