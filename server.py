"""MCP stdio server — exposes the engine as a `codebase_search` tool.

This is the layer that makes the whole thing usable from Claude Code (or any
MCP client), exactly like Augment's codebase-retrieval: a natural-language
query in, ranked code chunks out — but running locally on your own index.

Register it once:
    claude mcp add my-engine -- /abs/path/.venv/bin/python /abs/path/server.py
then call the `codebase_search` tool from any session.
"""

import lancedb
from mcp.server.fastmcp import FastMCP

import config  # noqa: F401  -- loads .env (VOYAGE_API_KEY) before clients init
from indexer import DB_PATH
from retriever import Retriever, _format

mcp = FastMCP("my-context-engine")

# Retrievers are expensive to build (they load the whole corpus for BM25), so
# cache one per table for the life of the server process.
_retrievers: dict[str, Retriever] = {}


def _get(repo: str) -> Retriever:
    if repo not in _retrievers:
        _retrievers[repo] = Retriever(repo)
    return _retrievers[repo]


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
