"""Smoke tests for embedder.py.

Pure tests (no network) validate the batching logic. The live test hits Voyage
and is skipped automatically if VOYAGE_API_KEY is absent.
"""

import math
import os

import config  # noqa: F401  -- side effect: loads .env into os.environ
from embedder import VoyageEmbedder


def _new() -> VoyageEmbedder:
    # build without touching the client constructor's API-key requirement
    e = VoyageEmbedder.__new__(VoyageEmbedder)
    return e


def test_batches_respect_count_limit():
    e = _new()
    e.MAX_BATCH = 3
    e.MAX_BATCH_TOKENS = 10_000_000
    texts = ["x"] * 7
    batches = list(e._make_batches(texts))
    assert [len(b) for b in batches] == [3, 3, 1]


def test_batches_respect_token_limit():
    e = _new()
    e.MAX_BATCH = 1000
    e.MAX_BATCH_TOKENS = 100  # ~400 chars
    texts = ["a" * 200] * 4  # ~50 tokens each -> 2 per batch
    batches = list(e._make_batches(texts))
    assert all(sum(e._est_tokens(t) for t in b) <= 100 for b in batches)
    assert sum(len(b) for b in batches) == 4  # nothing dropped


def test_oversized_first_chunk_yields_no_empty_batch():
    e = _new()
    e.MAX_BATCH = 128
    e.MAX_BATCH_TOKENS = 100
    giant = "a" * 10_000  # est tokens >> 100
    texts = [giant, "small", "small"]
    batches = list(e._make_batches(texts))
    assert [] not in batches, "must never yield an empty batch"
    assert batches[0] == [giant], "oversized chunk goes out alone"
    assert sum(len(b) for b in batches) == 3


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb)


def test_live_query_doc_relevance():
    if not os.environ.get("VOYAGE_API_KEY"):
        print("SKIP live test: no VOYAGE_API_KEY")
        return
    e = VoyageEmbedder()
    docs = [
        "def add(a, b):\n    return a + b",
        "def fetch_user(db, user_id):\n    return db.query(User).get(user_id)",
    ]
    doc_vecs = e.embed_documents(docs)
    assert len(doc_vecs) == 2
    assert all(len(v) == e.dim for v in doc_vecs)

    q = e.embed_query("function that sums two numbers")
    sims = [_cos(q, dv) for dv in doc_vecs]
    print(f"  sims: add={sims[0]:.3f}  fetch_user={sims[1]:.3f}")
    assert sims[0] > sims[1], "query should match the add() chunk best"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
