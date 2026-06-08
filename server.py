"""MCP stdio server — exposes the engine as a `codebase_search` tool.

This is the layer that makes the whole thing usable from Claude Code (or any
MCP client), exactly like Augment's codebase-retrieval: a natural-language
query in, ranked code chunks out — but running locally on your own index.

Register it once:
    claude mcp add compass -- /abs/path/.venv/bin/python /abs/path/server.py
then call the `codebase_search` tool from any session.
"""

import contextlib
import json
import os
import sys
import threading

import lancedb
from mcp.server.fastmcp import FastMCP

import config  # noqa: F401  -- loads .env (VOYAGE_API_KEY) before clients init
from indexer import DB_PATH, index_repo
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


# --- auto-index: index a repo on first search of the session, then keep it
# fresh with an in-process watcher for the life of this server (== the session,
# since each Claude session spawns its own stdio server subprocess). -----------

# repos already brought up to date during THIS process; guards against
# re-walking the tree on every search (the watcher handles changes after).
_initialized: set[str] = set()
_watchers: dict[str, object] = {}  # repo -> watchdog Observer (process-lived)
_index_lock = threading.Lock()

# remembers where each table was indexed from, so a later session can reindex
# (incrementally — only changed files re-embed) without re-specifying the path.
_PATHS_FILE = os.path.join(os.path.dirname(DB_PATH), "repo_paths.json")


def _load_paths() -> dict[str, str]:
    try:
        with open(_PATHS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_path(repo: str, path: str) -> None:
    paths = _load_paths()
    paths[repo] = path
    os.makedirs(os.path.dirname(_PATHS_FILE), exist_ok=True)
    with open(_PATHS_FILE, "w") as f:
        json.dump(paths, f, indent=2)


def _resolve_path(repo: str, repo_path: str | None) -> str | None:
    """A *known* path to index `repo` from, or None if we don't have one.

    Explicit arg wins (the caller is naming exactly what to index, even over a
    stale mapping); otherwise the path this table was indexed from in a past
    session. Deliberately does NOT guess the cwd — that fallback lives in
    `_ensure_ready` and only applies to repos not yet on disk, so we never
    clobber an existing index by re-indexing a guessed directory.
    """
    if repo_path:
        return os.path.abspath(repo_path)
    saved = _load_paths().get(repo)
    return os.path.abspath(saved) if saved else None


def _safe_cwd() -> str | None:
    """The server's working dir as a last-resort index target, unless it's a
    dangerous root ($HOME / filesystem root) that isn't a project."""
    cwd = os.path.abspath(os.getcwd())
    if cwd in (os.path.expanduser("~"), os.path.sep):
        return None
    return cwd


def _start_watcher(repo: str, path: str) -> None:
    """Spawn an in-process auto-reindex watcher for `path`, once per repo."""
    if repo in _watchers:
        return
    try:
        from watcher import start_watch  # lazy: `watch` extra is optional
    except Exception:
        return  # not installed — indexing still works, just no live refresh
    with contextlib.suppress(Exception):
        _watchers[repo] = start_watch(path, repo)


def _ensure_ready(repo: str, repo_path: str | None) -> str | None:
    """Make `repo` searchable; return an error message, or None on success.

    On the first touch this session: resolve a path, index it (incremental, so
    a restart only re-embeds files that changed), persist the path, and start a
    watcher. Subsequent calls are no-ops — the watcher + version-poll keep it
    fresh. A repo already on disk but with no known path stays searchable; it
    just can't be auto-refreshed here.
    """
    if repo in _initialized:
        return None
    with _index_lock:
        if repo in _initialized:  # another call won the race while we waited
            return None
        path = _resolve_path(repo, repo_path)
        if path is None:
            # already on disk (e.g. indexed manually): search as-is — never
            # reindex from a guessed cwd, which could clobber it with the
            # wrong directory.
            if repo in _repos():
                _initialized.add(repo)
                return None
            # brand-new repo and no path given: last-resort guess the cwd.
            path = _safe_cwd()
            if path is None:
                return (f"No index named '{repo}' and no path to build it from. "
                        f"Retry as codebase_search(..., repo_path='/path/to/repo').")
        try:
            # index_repo prints progress to stderr; redirect any stray stdout
            # so it can never corrupt the MCP JSON-RPC stream on stdout.
            with contextlib.redirect_stdout(sys.stderr):
                index_repo(path, repo)
        except Exception as exc:
            return f"indexing {path} failed: {exc}"
        _save_path(repo, path)
        _start_watcher(repo, path)
        _initialized.add(repo)
    return None


@mcp.tool()
def list_indexed_repos() -> str:
    """List the repositories currently indexed and searchable."""
    repos = _repos()
    return "Indexed repos:\n" + "\n".join(f"- {r}" for r in repos) if repos \
        else "No repos indexed yet. Run: python indexer.py <repo_path> <name>"


@mcp.tool()
def codebase_search(
    information_request: str, repo: str, repo_path: str | None = None, k: int = 8
) -> str:
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
    `repo` is the index/table name (call list_indexed_repos to see them).

    Auto-index: to search a repo that isn't indexed yet, pass `repo_path` (its
    absolute path) once — it will be indexed on this first call and then kept
    live by a file-watcher for the rest of the session. On later sessions the
    path is remembered, so the same `repo` re-indexes incrementally (only
    changed files) on first use; `repo_path` is then optional.
    Use plain grep only for finding ALL occurrences of a known exact identifier.
    """
    err = _ensure_ready(repo, repo_path)
    if err:
        return err

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
