"""Retrieval cases: starter catalog, score filter, isolation, caps, and upserts."""
import pytest

from rag.chunk_types import ChunkType
from rag.indexer import Indexer
from rag.library import STARTER_REFS, load_starter, seed_client_library
from rag.retriever import Retriever

GSTIN = "29ABCDE1234F1Z5"


def test_starter_seed_row_count_is_eighteen():
    rows = load_starter()
    assert len(rows) == 18
    assert len(STARTER_REFS) == 18
    assert len({row["ref"] for row in rows}) == 18
    assert len({row["content"] for row in rows}) == 18
    assert all(row["content"].strip() for row in rows)
    assert all(row["chunk_type"] == "hard_rule" for row in rows)
    assert all(row["metadata"]["scope"] == "library" for row in rows)


def test_starter_refs_match_the_catalog_in_order():
    rows = load_starter()
    assert [row["ref"] for row in rows] == list(STARTER_REFS)
    assert set(STARTER_REFS) == {row["ref"] for row in rows}
    for ref in (
        "library:doc:purchase_invoice",
        "library:doc:debit_note",
        "library:doc:credit_note",
        "library:gst:intra",
        "library:gst:inter",
        "library:hsn:8471",
        "library:sac:9983",
        "library:tds:194c",
        "library:tds:194i",
    ):
        assert ref in STARTER_REFS


def test_seeded_namespace_stores_each_starter_ref_once():
    indexer = Indexer()
    assert seed_client_library(indexer, "client-a") == 18
    chunks = indexer.get_namespace_chunks("client-a")
    assert [chunk["chunk_id"] for chunk in chunks] == list(STARTER_REFS)
    assert len({chunk["chunk_id"] for chunk in chunks}) == 18
    assert all(chunk["chunk_type"] == ChunkType.HARD_RULE for chunk in chunks)
    assert all(chunk["metadata"]["scope"] == "library" for chunk in chunks)
    assert all(chunk["content"].strip() for chunk in chunks)
    by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    assert "CGST" in by_id["library:gst:intra"]["content"]
    assert by_id["library:hsn:8471"]["metadata"]["hsn"] == "8471"
    assert by_id["library:doc:purchase_invoice"]["metadata"]["doc_type"] == "purchase_invoice"


def test_reseed_does_not_duplicate_starter_refs():
    indexer = Indexer()
    assert seed_client_library(indexer, "client-a") == 18
    assert seed_client_library(indexer, "client-a") == 18
    chunks = indexer.get_namespace_chunks("client-a")
    assert len(chunks) == 18
    assert [chunk["chunk_id"] for chunk in chunks] == list(STARTER_REFS)
    assert len({chunk["chunk_id"] for chunk in chunks}) == len(chunks)


def test_starter_seed_is_isolated_per_client():
    indexer = Indexer()
    assert seed_client_library(indexer, "client-a") == 18
    assert seed_client_library(indexer, "client-b") == 18
    indexer.upsert("client-a", "extra-ledger", ChunkType.LEDGER, "only client a")
    assert len(indexer.get_namespace_chunks("client-a")) == 19
    b_ids = [chunk["chunk_id"] for chunk in indexer.get_namespace_chunks("client-b")]
    assert b_ids == list(STARTER_REFS)
    assert "extra-ledger" not in b_ids
    assert indexer.get_namespace_chunks("client-c") == []
    assert all(chunk["content"] != "only client a" for chunk in indexer.get_namespace_chunks("client-b"))


def test_positive_score_filter_drops_zero_score_chunks():
    indexer = Indexer()
    for i in range(6):
        indexer.upsert("client-a", f"noise-{i}", ChunkType.LEDGER, f"unrelated ledger {i}")
    indexer.upsert(
        "client-a", "hit", ChunkType.VENDOR,
        f"Vendor {GSTIN} bearings", {"gstin": GSTIN},
    )
    rows = Retriever(indexer).retrieve("client-a", GSTIN, "", ["bearings"], "", "", "")
    assert [row["chunk_id"] for row in rows] == ["hit"]
    assert rows[0]["score"] > 0
    assert "gstin" in rows[0]["ranking_reasons"]
    assert "description" in rows[0]["ranking_reasons"]
    assert all(not row["chunk_id"].startswith("noise-") for row in rows)
    assert all(row["score"] > 0 for row in rows)


def test_zero_score_ledgers_drop_while_unmatched_memory_stays_positive():
    indexer = Indexer()
    indexer.upsert("client-a", "noise", ChunkType.LEDGER, "zebra xylophone")
    indexer.upsert("client-a", "mem", ChunkType.MEMORY, "nothing shared here")
    indexer.upsert("client-a", "bill", ChunkType.POSTED_BILL, "nothing shared here")
    rows = Retriever(indexer).retrieve(
        "client-a", GSTIN, "Quilted Cotton", ["calico"], "5208", "50", "debit_note",
    )
    assert [row["chunk_id"] for row in rows] == ["mem", "bill"]
    assert rows[0]["score"] == 2
    assert rows[1]["score"] == 1
    assert rows[0]["ranking_reasons"] == []
    assert rows[1]["ranking_reasons"] == []
    assert "noise" not in {row["chunk_id"] for row in rows}
    assert all(row["score"] > 0 for row in rows)


def test_empty_history_returns_no_chunks():
    rows = Retriever(Indexer()).retrieve(
        "client-a", GSTIN, "SKF Bearings", ["Bearings"], "8482", "1180", "purchase_invoice",
    )
    assert rows == []


def test_foreign_vendor_memories_cannot_crowd_out_a_known_vendor_match():
    indexer = Indexer()
    indexer.upsert("client-a", "vendor:current", ChunkType.VENDOR, GSTIN, {"gstin": GSTIN})
    for i in range(10):
        indexer.upsert("client-a", f"memory:other:{i}", ChunkType.MEMORY,
                       "bearings spindle assembly mechanism HSN 8482",
                       {"gstin": "24BBBBB0000B1Z8", "hsn": "8482"})
    rows = Retriever(indexer).retrieve(
        "client-a", GSTIN, "", ["bearings spindle assembly mechanism"], "8482", "", "", top_k=1,
    )
    assert [row["chunk_id"] for row in rows] == ["vendor:current"]


def test_missing_gstin_drops_gstin_only_chunks():
    indexer = Indexer()
    indexer.upsert("client-a", "gstin-only", ChunkType.VENDOR, GSTIN, {"gstin": GSTIN})
    with_gstin = Retriever(indexer).retrieve("client-a", GSTIN, "", [], "", "", "")
    assert [row["chunk_id"] for row in with_gstin] == ["gstin-only"]
    assert "gstin" in with_gstin[0]["ranking_reasons"]
    assert with_gstin[0]["score"] >= 8

    missing = Retriever(indexer).retrieve(
        "client-a", "", "Quilted Cotton", ["calico"], "5208", "50", "purchase_invoice",
    )
    assert missing == []


def test_missing_gstin_still_matches_name_description_and_hsn():
    indexer = Indexer()
    indexer.upsert("client-a", "gstin-only", ChunkType.VENDOR, GSTIN, {"gstin": GSTIN})
    indexer.upsert(
        "client-a", "named", ChunkType.VENDOR, "Acme Steels consulting engagement",
        {"vendor_name": "Acme Steels", "hsn": "998314"},
    )
    rows = Retriever(indexer).retrieve("client-a", "", "Acme Steels", ["consulting"], "9983", "", "")
    assert [row["chunk_id"] for row in rows] == ["named"]
    assert "gstin" not in rows[0]["ranking_reasons"]
    assert "vendor_name" in rows[0]["ranking_reasons"]
    assert "description" in rows[0]["ranking_reasons"]
    assert "hsn" in rows[0]["ranking_reasons"]
    assert rows[0]["score"] > 0


def test_duplicate_ref_upsert_replaces_the_row_retrieve_sees():
    indexer = Indexer()
    indexer.upsert("client-a", "vendor:1", ChunkType.VENDOR, "old bearings text", {"gstin": GSTIN, "rev": 1})
    indexer.upsert("client-a", "vendor:1", ChunkType.VENDOR, "new spindle text", {"gstin": GSTIN, "rev": 2})
    assert len(indexer.get_namespace_chunks("client-a")) == 1
    assert indexer.get_namespace_chunks("client-a")[0]["content"] == "new spindle text"
    assert indexer.get_namespace_chunks("client-a")[0]["metadata"]["rev"] == 2

    current = Retriever(indexer).retrieve("client-a", GSTIN, "", ["spindle"], "", "", "")
    assert [row["chunk_id"] for row in current] == ["vendor:1"]
    assert current[0]["content"] == "new spindle text"
    assert current[0]["metadata"]["rev"] == 2
    assert "bearings" not in current[0]["content"]
    assert Retriever(indexer).retrieve("client-a", "", "", ["bearings"], "", "", "") == []


def test_top_k_keeps_the_highest_ranked_matches():
    indexer = Indexer()
    for i in range(12):
        indexer.upsert(
            "client-a", f"m{i:02d}", ChunkType.VENDOR, f"shared {GSTIN}", {"gstin": GSTIN},
        )
    for i in range(10):
        indexer.upsert("client-a", f"zero-{i}", ChunkType.LEDGER, f"xylophone {i}")
    rows = Retriever(indexer).retrieve("client-a", GSTIN, "", [], "", "", "", top_k=5)
    assert [row["chunk_id"] for row in rows] == [f"m{i:02d}" for i in range(5)]
    assert len({row["score"] for row in rows}) == 1
    assert rows[0]["score"] > 0
    assert all(not row["chunk_id"].startswith("zero-") for row in rows)
    best = Retriever(indexer).retrieve("client-a", GSTIN, "", [], "", "", "", top_k=1)
    assert [row["chunk_id"] for row in best] == ["m00"]


def test_default_top_k_is_eight_and_does_not_pad():
    indexer = Indexer()
    for i in range(12):
        indexer.upsert(
            "client-a", f"m{i:02d}", ChunkType.VENDOR, f"shared {GSTIN}", {"gstin": GSTIN},
        )
    indexer.upsert("client-a", "zero", ChunkType.LEDGER, "xylophone")
    window = Retriever(indexer).retrieve("client-a", GSTIN, "", [], "", "", "")
    assert len(window) == 8
    assert [row["chunk_id"] for row in window] == [f"m{i:02d}" for i in range(8)]
    assert "zero" not in {row["chunk_id"] for row in window}
    assert all(row["score"] > 0 for row in window)

    sparse = Indexer()
    for i in range(3):
        sparse.upsert("client-a", f"hit-{i}", ChunkType.VENDOR, f"vendor {GSTIN}", {"gstin": GSTIN})
    for i in range(6):
        sparse.upsert("client-a", f"miss-{i}", ChunkType.LEDGER, f"unrelated {i}")
    short = Retriever(sparse).retrieve("client-a", GSTIN, "", [], "", "", "")
    assert [row["chunk_id"] for row in short] == ["hit-0", "hit-1", "hit-2"]
    assert len(short) < 8
    assert all(row["score"] > 0 for row in short)
    assert all(not row["chunk_id"].startswith("miss-") for row in short)


def test_retrieve_is_isolated_by_client():
    indexer = Indexer()
    indexer.upsert(
        "client-a", "shared-ref", ChunkType.VENDOR, f"alpha private {GSTIN}", {"gstin": GSTIN},
    )
    indexer.upsert(
        "client-b", "shared-ref", ChunkType.VENDOR, f"beta private {GSTIN}", {"gstin": GSTIN},
    )
    indexer.upsert(
        "client-b", "only-b", ChunkType.MEMORY, "beta only marker 27BBBBB0000B1Z8",
        {"gstin": "27BBBBB0000B1Z8"},
    )
    retriever = Retriever(indexer)
    client_a = retriever.retrieve("client-a", GSTIN, "", [], "", "", "")
    client_b = retriever.retrieve("client-b", GSTIN, "", [], "", "", "")
    private = retriever.retrieve("client-b", "27BBBBB0000B1Z8", "", [], "", "", "")

    assert [row["chunk_id"] for row in client_a] == ["shared-ref"]
    assert client_a[0]["content"] == f"alpha private {GSTIN}"
    assert all(row["content"] != f"alpha private {GSTIN}" for row in client_b)
    shared_b = next(row for row in client_b if row["chunk_id"] == "shared-ref")
    assert shared_b["content"] == f"beta private {GSTIN}"
    assert all(row["chunk_id"] != "only-b" for row in client_a)
    assert [row["chunk_id"] for row in private] == ["only-b"]
    assert all("alpha private" not in row["content"] for row in private)
    with pytest.raises(ValueError, match="client_id"):
        retriever.retrieve("   ", GSTIN, "", [], "", "", "")
