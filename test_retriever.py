"""Tests for retriever.py. RRF/tokenizer are offline; hybrid search is live."""

import os

from retriever import (
    Retriever,
    _code_prior,
    _mmr,
    _name_match_count,
    _rrf,
    _tokenize,
)


def test_name_match_count_targets_symbol():
    q = set(_tokenize("the ollama and anthropic shim for payments aging report"))
    assert _name_match_count(q, "_ollama_call") >= 1     # exact 'ollama'
    assert _name_match_count(q, "report_aging") >= 2      # 'report' + 'aging'
    assert _name_match_count(q, "Payment") == 1           # 'payments' ~ 'payment'
    assert _name_match_count(q, "render_pdf_bytes") == 0  # no overlap
    assert _name_match_count(q, None) == 0


def test_graph_expand_pulls_cross_file_callee():
    r = Retriever.__new__(Retriever)
    r.rows = [
        {"path": "a.py", "start_line": 1, "name": "make_pdf",
         "text": "def make_pdf():\n    return render_invoice()"},
        {"path": "b.py", "start_line": 1, "name": "render_invoice",
         "text": "def render_invoice():\n    return 1"},
        {"path": "a.py", "start_line": 9, "name": "sibling",
         "text": "def sibling():\n    pass"},
    ]
    r._defs = {"make_pdf": [0], "render_invoice": [1], "sibling": [2]}
    out = r._expand_pool([r.rows[0]], cap=8)  # only make_pdf in the pool
    paths = {x["path"] for x in out}
    assert "b.py" in paths            # cross-file callee render_invoice pulled in
    assert out[0]["path"] == "a.py"   # original pool preserved, expansions appended


def test_name_match_count_ignores_generic_tokens():
    # a query about "views"/"documents" must NOT boost every view/handler
    q = set(_tokenize("which documents can a user view and edit across views"))
    assert _name_match_count(q, "settings_view") == 0
    assert _name_match_count(q, "document_form_view") == 0


def test_mmr_breaks_same_file_clustering():
    rows = [
        {"path": "views.py"},
        {"path": "views.py"},
        {"path": "render.py"},
    ]
    relevance = [1.0, 0.95, 0.8]
    # pure relevance (lam=1.0) takes both top views.py chunks
    assert [rows[i]["path"] for i in _mmr(rows, relevance, k=2, lam=1.0)] == \
        ["views.py", "views.py"]
    # with diversity (lam=0.5) the 2nd pick switches to the other file
    assert [rows[i]["path"] for i in _mmr(rows, relevance, k=2, lam=0.5)] == \
        ["views.py", "render.py"]


def test_code_prior_ranks_source_over_docs_and_boilerplate():
    src = _code_prior({"path": "invoices/services/pdf.py", "kind": "function_definition"})
    doc = _code_prior({"path": "README.md", "kind": "window"})
    test = _code_prior({"path": "invoices/tests/test_logic.py", "kind": "class_definition"})
    migration = _code_prior({"path": "invoices/migrations/0001.py", "kind": "class_definition"})
    assert src == 1.0
    assert doc < src and test < src and migration < src
    assert migration < test  # migrations are the most boilerplate-y


def test_tokenizer_splits_identifiers():
    assert _tokenize("fetchUserById") == ["fetch", "user", "by", "id"]
    assert _tokenize("user_id") == ["user", "id"]
    assert _tokenize("HTTPServerError") == ["http", "server", "error"]


def test_rrf_agreement_beats_single_top():
    # 'a' is #2 in both lists; 'x' and 'y' are each #1 in only one list.
    # Agreement across lists should win.
    fused = _rrf([["x", "a"], ["y", "a"]])
    assert fused[0] == "a"


def test_rrf_orders_by_summed_reciprocal_rank():
    fused = _rrf([["a", "b", "c"], ["b", "c", "a"]])
    assert fused == ["b", "a", "c"]  # b: best combined rank


def test_rrf_empty():
    assert _rrf([]) == []
    assert _rrf([[], []]) == []


def test_live_hybrid_search():
    if not os.environ.get("VOYAGE_API_KEY"):
        print("SKIP live test: no VOYAGE_API_KEY")
        return
    r = Retriever("self")  # the engine's own repo, indexed earlier
    hits = r.search("greedy batching to respect token limits", k=3)
    assert hits, "expected results"
    paths = {h["path"] for h in hits}
    print(f"  hybrid top-3: {[f'''{h['path']}:{h['start_line']}''' for h in hits]}")
    # the batching logic lives in embedder.py
    assert any("embedder" in p for p in paths)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
