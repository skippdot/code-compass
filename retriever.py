"""Hybrid retrieval: dense (vectors) + sparse (BM25), fused with RRF.

Why hybrid: dense embeddings capture *meaning* ("sum two numbers" ~ `add`) but
blur on exact symbol names; BM25 nails exact tokens (`fetchUserById`) but is
blind to paraphrase. Fusing the two ranked lists gives both. We combine them
with Reciprocal Rank Fusion, which merges by *rank* (not raw score), so the two
incomparable score scales (cosine distance vs BM25 tf-idf) never need
normalizing.

Reranking (a cross-encoder over the fused top-N) is the natural next layer.
"""

import os
import re

import lancedb
from rank_bm25 import BM25Okapi

import config  # noqa: F401  -- loads .env
from embedder import Embedder, VoyageEmbedder
from indexer import DB_PATH
from reranker import Reranker, VoyageReranker


def _tokenize(text: str) -> list[str]:
    """Code-aware tokens: split on non-alnum, then camelCase, lowercased.

    So `fetchUserById` and `user_id` both yield ['fetch','user','by','id'] /
    ['user','id'], letting a natural-language query match symbol names.
    """
    tokens: list[str] = []
    for part in re.findall(r"[A-Za-z0-9]+", text):
        pieces = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+", part)
        tokens.extend(pieces or [part])
    return [t.lower() for t in tokens]


NAME_BOOST = 0.25  # relevance multiplier per query token matched in a symbol name

# Structural words too generic to be a useful symbol signal: a query about
# "views" / "documents" would otherwise boost every view/handler equally. The
# boost should fire on *distinctive* domain tokens (ollama, aging, reverse).
GENERIC_NAME_TOKENS = frozenset({
    "view", "views", "document", "documents", "model", "models", "form",
    "forms", "handler", "handlers", "service", "services", "command",
    "request", "response", "object", "base", "index", "data", "value",
})


def _name_match_count(query_tokens: set[str], name: str | None) -> int:
    """How many distinctive (5+ char, non-generic) query tokens hit the chunk's
    symbol name.

    A 2-line `_ollama_call` chunk is content-starved and ranks low on text
    similarity, yet a query literally naming `ollama` clearly wants it. Matching
    the *symbol* (with a prefix rule so `payments`~`payment`) rescues exactly
    those already-isolated-but-low-ranked named definitions.

    The 5-char floor plus GENERIC_NAME_TOKENS keep structural words (`view`,
    `document`, `data`) from boosting every handler on a query about views."""
    if not name:
        return 0
    name_tokens = set(_tokenize(name))
    matched = 0
    for qt in query_tokens:
        if len(qt) < 5 or qt in GENERIC_NAME_TOKENS:
            continue
        if any(
            qt == nt or (len(nt) >= 4 and (qt.startswith(nt) or nt.startswith(qt)))
            for nt in name_tokens
        ):
            matched += 1
    return matched


def _code_prior(row: dict) -> float:
    """Soft weight encoding 'implementation code is the source of truth'.

    A natural-language query matches prose (README, CHANGELOG) and test/
    migration boilerplate textually, but the real answer is usually the
    definition in core source. We down-weight — not exclude — the rest, so a
    test still surfaces when nothing better exists.
    """
    path = row["path"].lower()
    name = path.rsplit("/", 1)[-1]

    if path.endswith((".md", ".rst", ".txt")):
        weight = 0.3  # prose docs: rarely the source of truth for "how does X work"
    elif row["kind"] == "window":
        weight = 0.4  # other unparsed text: configs, data
    elif path.endswith((".html", ".htm")):
        weight = 0.6  # markup: a template can BE the answer (e.g. invoice.html)
    else:
        weight = 1.0  # parsed code definition

    if "/migrations/" in path:
        weight *= 0.5
    if "/tests/" in path or name.startswith("test_"):
        weight *= 0.7
    if "/management/commands/" in path or "seed" in name:
        weight *= 0.8
    return weight


def _rrf(ranked_lists: list[list[str]], k: int = 60) -> list[str]:
    """Reciprocal Rank Fusion: merge ranked lists of keys into one ranking.

    Each input list is keys ordered best-first. A key's fused score is the sum,
    over every list it appears in, of 1 / (k + rank), where `rank` is its
    0-based position in that list. Returns keys sorted by fused score, highest
    first.

    The constant k (=60, the canonical default) damps the influence of any
    single list's top ranks, so a key ranked #1 in one list doesn't
    automatically beat a key ranked #2 in *both*. A key missing from a list
    simply contributes nothing from it.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, key in enumerate(ranked):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=scores.get, reverse=True)


def _mmr(
    rows: list[dict],
    relevance: list[float],
    k: int,
    lam: float = 0.7,
) -> list[int]:
    """Greedy Maximal Marginal Relevance selection, diversifying by *file*.

    Each pick maximizes `lam*relevance - (1-lam)*penalty`, where penalty is 1
    if the chunk's file is already represented, else 0. So a slightly-weaker
    chunk from a *new* file can beat yet another chunk from an already-covered
    file — exactly the clustering the A/B showed (a PDF query collapsing into
    `views.py`).

    Diversity is deliberately file-based, NOT vector-based: pipeline stages
    (view -> service -> template) are semantically *similar*, so a cosine
    penalty would wrongly suppress the very cross-file chunks we want. `lam=1.0`
    disables diversity (pure relevance). Returns selected indices, best-first.
    """
    selected: list[int] = []
    seen_files: set[str] = set()
    remaining = list(range(len(rows)))
    while remaining and len(selected) < k:
        best_i, best_score = remaining[0], float("-inf")
        for i in remaining:
            penalty = 1.0 if rows[i]["path"] in seen_files else 0.0
            score = lam * relevance[i] - (1.0 - lam) * penalty
            if score > best_score:
                best_score, best_i = score, i
        selected.append(best_i)
        seen_files.add(rows[best_i]["path"])
        remaining.remove(best_i)
    return selected


class Retriever:
    """Searches one indexed table with hybrid dense+sparse retrieval."""

    def __init__(
        self,
        table_name: str,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
        db_path: str = DB_PATH,
        mmr_lambda: float = 0.7,
    ):
        self.table = lancedb.connect(db_path).open_table(table_name)
        self.embedder = embedder or VoyageEmbedder()
        self.reranker = reranker if reranker is not None else VoyageReranker()
        self.mmr_lambda = mmr_lambda  # 1.0 = pure relevance, lower = more diverse
        # BM25 needs the whole corpus in memory; fine for personal-repo scale.
        self.rows = self.table.to_arrow().to_pylist()
        self.bm25 = BM25Okapi([_tokenize(r["text"]) for r in self.rows])
        # symbol -> chunks that define it, for graph (callee) expansion
        self._defs: dict[str, list[int]] = {}
        for i, r in enumerate(self.rows):
            name = (r.get("name") or "").lower()
            if len(name) >= 4 and name not in GENERIC_NAME_TOKENS:
                self._defs.setdefault(name, []).append(i)

    @staticmethod
    def _key(row: dict) -> str:
        """Stable identity for a chunk across the two ranked lists."""
        return f"{row['path']}:{row['start_line']}"

    def _expand_pool(
        self, pool_rows: list[dict], expand_from: int = 6, cap: int = 8
    ) -> list[dict]:
        """Graph expansion: pull in cross-file *callees* of the strongest
        candidates — chunks defining a symbol that a top candidate references.

        Surfaces the next stage of a flow (view -> service -> renderer) even
        when retrieval only found the entry point. Added to the pool, not the
        result: the reranker still decides whether they earn a top-k slot."""
        have = {self._key(r) for r in pool_rows}
        extra: list[dict] = []
        for h in pool_rows[:expand_from]:
            hname = (h.get("name") or "").lower()
            # sorted() so expansion is deterministic: set iteration order over
            # strings varies per process (hash seed), which made eval flaky
            for ident in sorted(set(re.findall(r"[A-Za-z_][A-Za-z0-9_]+", h["text"]))):
                low = ident.lower()
                if len(low) < 4 or low == hname or low in GENERIC_NAME_TOKENS:
                    continue
                for idx in self._defs.get(low, []):
                    row = self.rows[idx]
                    if row["path"] == h["path"]:  # cross-file callees only
                        continue
                    key = self._key(row)
                    if key in have:
                        continue
                    have.add(key)
                    extra.append(row)
                    if len(extra) >= cap:
                        return pool_rows + extra
        return pool_rows + extra

    def search(
        self,
        query: str,
        k: int = 8,
        candidates: int = 50,
        pool: int = 30,
        graph: bool = True,
    ) -> list[dict]:
        """Hybrid recall (dense+BM25+RRF) -> rerank precision pass -> top k.

        `candidates` is how deep each retriever goes; `pool` is how many fused
        candidates the reranker re-scores. A larger pool trades latency/cost for
        the chance to rescue a chunk that fusion ranked low.
        """
        by_key = {self._key(r): r for r in self.rows}

        # dense: cosine vector search via LanceDB
        qv = self.embedder.embed_query(query)
        vec_hits = (
            self.table.search(qv).metric("cosine").limit(candidates).to_list()
        )
        vec_keys = [self._key(r) for r in vec_hits]

        # sparse: BM25 over the in-memory corpus
        scores = self.bm25.get_scores(_tokenize(query))
        top_idx = sorted(
            range(len(self.rows)), key=lambda i: scores[i], reverse=True
        )[:candidates]
        bm25_keys = [self._key(self.rows[i]) for i in top_idx]

        fused_keys = _rrf([vec_keys, bm25_keys])
        candidate_rows = [by_key[key] for key in fused_keys if key in by_key]
        pool_rows = candidate_rows[:pool]
        if graph:  # augment recall with cross-file callees of the top candidates
            pool_rows = self._expand_pool(pool_rows)

        # relevance per pool row: rerank score * code-vs-docs prior, or — with no
        # reranker — a fusion-rank proxy so the MMR step still has a signal.
        if self.reranker is None:
            relevance = [1.0 / (i + 1) for i in range(len(pool_rows))]
        else:
            scored = self.reranker.rerank(query, [r["text"] for r in pool_rows])
            relevance = [0.0] * len(pool_rows)
            for i, score in scored:
                relevance[i] = score * _code_prior(pool_rows[i])

        # symbol-name boost: lift named defs whose symbol matches the query, so a
        # content-starved but on-point chunk (e.g. `_ollama_call`) isn't buried.
        qtok = set(_tokenize(query))
        for i, row in enumerate(pool_rows):
            relevance[i] *= 1.0 + NAME_BOOST * _name_match_count(qtok, row.get("name"))

        # final cut via MMR: trade a little relevance for file/semantic diversity
        # so multi-file pipelines aren't crowded out by same-file near-duplicates.
        order = _mmr(pool_rows, relevance, k, self.mmr_lambda)
        return [pool_rows[i] for i in order]


def _format(hit: dict) -> str:
    loc = f"{hit['path']}:{hit['start_line']}-{hit['end_line']}"
    sym = f" {hit['kind']} {hit['name']}" if hit.get("name") else ""
    return f"{loc}{sym}"


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        sys.exit('usage: python retriever.py <table> "<query>"')
    r = Retriever(sys.argv[1])
    for hit in r.search(sys.argv[2]):
        print(_format(hit))
        print(os.linesep.join(hit["text"].splitlines()[:4]))
        print("---")
