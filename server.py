"""MCP stdio server — exposes the engine as a `codebase_search` tool.

This is the layer that makes the whole thing usable from Claude Code (or any
MCP client), exactly like Augment's codebase-retrieval: a natural-language
query in, ranked code chunks out — but running locally on your own index.

Register it once:
    claude mcp add compass -- /abs/path/.venv/bin/python /abs/path/server.py
then call the `codebase_search` tool from any session.
"""

import lancedb
from mcp.server.fastmcp import FastMCP

import config  # noqa: F401  -- loads .env (VOYAGE_API_KEY) before clients init
from indexer import DB_PATH
from retriever import Retriever, _format

mcp = FastMCP("code-compass")

# Retrievers are expensive to build (they load the whole corpus for BM25), so
# cache one per table — but remember which on-disk version it was built from,
# so a reindex (manual or via watcher.py) doesn't stay invisible to this
# long-lived process. Value is (retriever, table_version_it_was_built_from).
_retrievers: dict[str, tuple[Retriever, int | None]] = {}


def _table_version(repo: str) -> int | None:
    """Current on-disk version of the table, or None if it's missing.

    LanceDB bumps this integer on every overwrite, so it's a cheap freshness
    probe (manifest read, no corpus load) to tell whether the cached Retriever
    has gone stale relative to a fresh index on disk.
    """
    try:
        return lancedb.connect(DB_PATH).open_table(repo).version
    except Exception:
        return None


def _get(repo: str) -> Retriever:
    """Return a Retriever for `repo`, rebuilding it if the index changed on disk.

    Without the version check the server would serve whatever corpus it loaded
    at first use forever — a reindex would only take effect after a restart.
    """
    current = _table_version(repo)
    cached = _retrievers.get(repo)
    if cached is None or cached[1] != current:
        _retrievers[repo] = (Retriever(repo), current)
    return _retrievers[repo][0]


def _repos() -> list[str]:
    try:
        return lancedb.connect(DB_PATH).list_tables().tables
    except Exception:
        return []


@mcp.tool()
def list_indexed_repos() -> str:
    """List the repositories currently indexed and searchable."""
    repos = _repos()
    return "Indexed repos:\n" + "\n".join(f"- {r}" for r in repos) if repos \
        else "No repos indexed yet. Run: python indexer.py <repo_path> <name>"


@mcp.tool()
def codebase_search(information_request: str, repo: str, k: int = 8) -> str:
    """PRIMARY tool for understanding a codebase — prefer it as the FIRST CHOICE
    for any "where / how / what" question about an indexed repo.

    It takes a natural-language description and returns the most relevant code
    chunks (file:line + source) via hybrid semantic + keyword retrieval, ranked
    and reranked. Use it BEFORE grepping or guessing whenever you are unsure
    which files hold the answer, want a high-level picture, or need to trace a
    feature across files.

    Good `information_request` examples:
      * "where is the invoice due date calculated"
      * "how is authentication and per-company isolation enforced"
      * "the PDF generation pipeline from view to template"
    `repo` is the indexed table name (call list_indexed_repos to see them).
    Use plain grep only for finding ALL occurrences of a known exact identifier.
    """
    if repo not in _repos():
        avail = ", ".join(_repos()) or "(none)"
        return f"No index named '{repo}'. Available: {avail}"

    hits = _get(repo).search(information_request, k=k)
    if not hits:
        return "No results."

    blocks = []
    for h in hits:
        body = "\n".join(h["text"].splitlines()[:40])  # cap runaway chunks
        blocks.append(f"### {_format(h)}\n```{h['language']}\n{body}\n```")
    return "\n\n".join(blocks)


if __name__ == "__main__":
    mcp.run(transport="stdio")
