# code compass

A self-hosted **code-context search engine**, exposed over [MCP](https://modelcontextprotocol.io) — a do-it-yourself clone of Augment's `codebase-retrieval` tool. Ask a natural-language question about a codebase ("where is the invoice due date calculated", "how does auth work end to end") and get back the most relevant code chunks with `file:line` locations, ranked by hybrid semantic + lexical retrieval and a reranker.

Runs locally on your own repos. No data leaves your machine except embedding/rerank API calls (and those can be swapped for local models).

## Why

Augment is winding down its IDE plugins; the part worth keeping is the context engine. This rebuilds it from parts, then **benchmarks it head-to-head against Augment** on real repos (see [`AB_REPORT.md`](AB_REPORT.md)) — reaching comparable top-8 recall on deep, multi-file queries.

## How it works

```
index:   git repo ──▶ tree-sitter chunking ──▶ embeddings ──▶ LanceDB
                       (gitignore-aware,         (voyage-code-3
                        incremental)              or local Qwen3)

search:  query ──▶ dense (cosine) ─┐
                                    ├─▶ RRF fusion ──▶ code-prior + symbol-name
              ──▶ BM25 (lexical) ──┘                  boost ──▶ MMR (file-diverse)
                                                       + graph-expansion ──▶ rerank ──▶ top-k
```

| Layer | File | What |
|-------|------|------|
| Chunking | `chunker.py`, `_ts.py` | tree-sitter, syntax-aware; large classes split, tiny members grouped; window fallback |
| Embeddings | `embedder.py` | `VoyageEmbedder` (voyage-code-3) or `LocalEmbedder` (Qwen3-Embedding-0.6B), swappable |
| Index | `indexer.py` | walk (respects `.gitignore` via `git ls-files`), chunk, embed, store; incremental by content hash |
| Retrieve | `retriever.py` | hybrid dense+BM25, RRF fusion, code-vs-docs prior, symbol-name boost, file-diverse MMR, cross-file graph-expansion |
| Rerank | `reranker.py` | `VoyageReranker` (rerank-2.5-lite) or `LocalReranker` (cross-encoder), swappable |
| Serve | `server.py` | MCP stdio server: `codebase_search`, `list_indexed_repos` |
| Watch | `watcher.py` | auto-reindex on file save (debounced) |

## Install

Uses [uv](https://github.com/astral-sh/uv).

```bash
uv venv && uv pip install -e .
echo "VOYAGE_API_KEY=..." > .env        # free tier at voyageai.com; or use LocalEmbedder
```

Optional extras: `uv pip install -e '.[local]'` (offline embed/rerank), `'.[watch]'` (file-watcher).

### Run fully offline (no API)

Set two env vars and re-index (vectors are model-specific):

```bash
uv pip install -e '.[local]'
export CODE_COMPASS_EMBED=local    # Qwen3-Embedding-0.6B on MPS/CPU, no API
export CODE_COMPASS_RERANK=local   # cross-encoder reranker  (or: none)
python indexer.py /path/to/repo myrepo
```

On the CoIR cosqa benchmark the local embedder is within noise of voyage-code-3 (NDCG@10 0.359 vs 0.364), so offline costs little quality. Defaults stay Voyage (`CODE_COMPASS_EMBED=voyage`).

## Use

```bash
python indexer.py /path/to/repo myrepo     # index a repo as table "myrepo"
python -c "import config; from retriever import Retriever, _format; \
           [print(_format(h)) for h in Retriever('myrepo').search('how does auth work')]"
```

Register as an MCP server (works from any Claude Code session):

```bash
claude mcp add compass -- /abs/path/.venv/bin/python /abs/path/server.py
```

Then call `codebase_search(information_request, repo)` from your agent.

## Benchmark

Automated eval (`eval/run_eval.py`, 24 queries × 3 repos):

| | recall@8 | hit@8 | recall@3 |
|---|---|---|---|
| overall | **0.979** | **1.000** | **0.896** |

Head-to-head vs Augment's `codebase-retrieval` on the same repos & queries: started at 3 Augment wins / 2 ties; after noise-filtering → code-prior → file-diverse MMR → smart chunking → symbol-name boost → graph-expansion, reached **5 ties / 0 clear Augment wins** on deep queries. Full story, including measured **negative** results (module-boost, feature-cohesion, and the stronger reranker that made things *worse*), in [`AB_REPORT.md`](AB_REPORT.md).

## What didn't work (kept honest)

The benchmark caught several "obvious" upgrades that regressed and were reverted:
- a module/wiring-file boost (displaced real answers; couldn't surface un-retrieved files);
- feature-directory recall expansion (same);
- swapping `rerank-2.5-lite` → `rerank-2.5` full — *worse* on the eval despite vendor "+7-8%".

Lesson: a stronger model on someone else's benchmark ≠ better on yours. Keep your own deterministic eval.

## Limitations

- Graph-expansion is heuristic (symbol-name matching), not a resolved LSP/SCIP call graph.
- Tuned/benchmarked on three repos; coarse directory-level eval labels.
- Voyage API by default (cheap, code leaves your machine for embedding); switch to the `local` extra for fully offline.

## License

MIT
