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
| Serve | `server.py` | MCP stdio server: `codebase_search`, `list_indexed_repos`; auto-indexes a repo on first search and serves a fresh corpus (rebuilds when the on-disk index version changes) |
| Watch | `watcher.py` | auto-reindex on file save (debounced); runs standalone or in-process inside the server |

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

Register it once as an MCP server (works from any Claude Code session):

```bash
claude mcp add compass -- /abs/path/.venv/bin/python /abs/path/server.py
```

Then just call `codebase_search` from your agent — **no manual indexing step**:

```python
# first call for a new repo: pass its path; it indexes, then auto-stays-fresh
codebase_search("how does auth work", repo="myrepo", repo_path="/path/to/repo")
# later calls (same or future sessions): path is remembered
codebase_search("where are invoices rendered", repo="myrepo")
```

On the first search of a repo the server indexes it, remembers its path
(`store/repo_paths.json`), and starts a file-watcher that keeps the index live
for the rest of the session. A later session re-indexes **incrementally** (only
files whose content hash changed) on first use, so startup stays cheap. If you
omit `repo_path` for an unknown repo it falls back to the server's working
directory (refusing `$HOME` / the filesystem root).

Prefer to drive it yourself? The pieces still work standalone:

```bash
python indexer.py /path/to/repo myrepo     # index a repo as table "myrepo"
python watcher.py /path/to/repo myrepo     # auto-reindex on save (blocking)
```

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
