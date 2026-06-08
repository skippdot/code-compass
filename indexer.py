"""Index a repository into a LanceDB table.

Ties the two built layers together: walk a repo -> chunk each file
(`chunker.chunk_file`) -> embed every chunk (`embedder`) -> store vectors plus
metadata in LanceDB. The retriever then searches that table.

Run directly:  python indexer.py <repo_path> [table_name]
"""

import hashlib
import os
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import asdict

import lancedb

import config  # noqa: F401  -- loads .env so VOYAGE_API_KEY is present
from chunker import CHUNKER_VERSION, chunk_file
from embedder import Embedder, make_embedder

DB_PATH = os.path.join(os.path.dirname(__file__), "store", "lancedb")

# Directories never worth indexing: VCS, deps, build output, caches, our store.
# `locale` holds gettext .po translation catalogs — they match queries lexically
# ("invoice", "currency") yet are never the source of truth, so they swamp real
# code in results. Excluded for the same reason Augment de-prioritizes them.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env",
    "__pycache__", ".idea", ".vscode", "dist", "build", "target",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "store", ".next",
    "locale",
}

MAX_FILE_BYTES = 1_000_000  # skip files above ~1MB: minified/generated/data

# Generated / non-source-of-truth file types that pollute retrieval.
SKIP_EXTS = (".po", ".mo", ".pot", ".min.js", ".min.css", ".map", ".lock")


def _skip_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.endswith(".egg-info")


def _skip_file(name: str) -> bool:
    # never index secrets — they'd become retrievable from the vector store
    if name == ".env" or name.startswith(".env."):
        return True
    return name.endswith(SKIP_EXTS)


def _read_text(full: str) -> str | None:
    """Read a file as UTF-8 text, or None if too big / binary / unreadable."""
    try:
        if os.path.getsize(full) > MAX_FILE_BYTES:
            return None
        with open(full, encoding="utf-8") as f:
            return f.read()
    except (UnicodeDecodeError, OSError):
        return None


def _git_files(root: str) -> list[str] | None:
    """Files git would consider here, or None if `root` isn't a git repo.

    `git ls-files` is the source of truth for .gitignore: --cached lists tracked
    files, --others --exclude-standard adds untracked-but-not-ignored ones, and
    everything in .gitignore / .git/info/exclude / global excludes is dropped
    for free. Beats reimplementing gitignore semantics by hand.
    """
    try:
        result = subprocess.run(
            ["git", "-C", root, "ls-files", "-z",
             "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return [p for p in result.stdout.split("\0") if p]


def _iter_source_files(root: str) -> Iterator[tuple[str, str]]:
    """Yield (relative_path, source_text) for each indexable file under `root`.

    Prefers git's view of the repo (so .gitignore is respected exactly); falls
    back to walking + SKIP_DIRS when `root` isn't a git repo. Either way, secret
    files (.env*), generated types (SKIP_EXTS), oversized and binary files are
    dropped.
    """
    git_files = _git_files(root)
    if git_files is not None:
        for rel in git_files:
            if _skip_file(os.path.basename(rel)):
                continue
            text = _read_text(os.path.join(root, rel))
            if text is not None:
                yield rel, text
        return

    for dirpath, dirnames, filenames in os.walk(root):
        # prune in place so os.walk skips these subtrees entirely
        dirnames[:] = [d for d in dirnames if not _skip_dir(d)]
        for filename in filenames:
            if _skip_file(filename):
                continue
            text = _read_text(os.path.join(dirpath, filename))
            if text is not None:
                yield os.path.relpath(os.path.join(dirpath, filename), root), text


def _file_hash(text: str) -> str:
    # include the chunker version so logic changes invalidate reused chunks
    return hashlib.sha1(f"v{CHUNKER_VERSION}\0{text}".encode()).hexdigest()


def index_repo(
    repo_path: str,
    table_name: str | None = None,
    embedder: Embedder | None = None,
) -> int:
    """Index `repo_path` into a LanceDB table; return the chunk count.

    Incremental: chunks of files whose content hash is unchanged since the last
    run are reused as-is (no re-embedding); only changed/new files are embedded.
    Deleted files drop out naturally since we only carry forward current files.
    """
    repo_path = os.path.abspath(repo_path)
    table_name = table_name or os.path.basename(repo_path.rstrip("/"))
    embedder = embedder or make_embedder()
    db = lancedb.connect(DB_PATH)

    # load the previous index (if any), grouped by file, to reuse unchanged work
    prev_rows: dict[str, list[dict]] = defaultdict(list)
    prev_hash: dict[str, str | None] = {}
    if table_name in db.list_tables().tables:
        for row in db.open_table(table_name).to_arrow().to_pylist():
            prev_rows[row["path"]].append(row)
            prev_hash[row["path"]] = row.get("file_hash")

    reused_rows: list[dict] = []
    pending: list[tuple] = []  # (Chunk, file_hash) awaiting embedding
    reused_files = changed_files = 0
    for relpath, source in _iter_source_files(repo_path):
        h = _file_hash(source)
        if prev_hash.get(relpath) == h:
            reused_rows.extend(prev_rows[relpath])
            reused_files += 1
        else:
            file_chunks = chunk_file(relpath, source)
            if file_chunks:  # empty files yield nothing — never "changed"
                changed_files += 1
                pending.extend((chunk, h) for chunk in file_chunks)

    new_rows: list[dict] = []
    if pending:
        print(f"embedding {len(pending)} chunks from {changed_files} "
              f"changed/new files...", file=sys.stderr)
        vectors = embedder.embed_documents([c.text for c, _ in pending])
        new_rows = [
            {"vector": v, "file_hash": h, **asdict(c)}
            for v, (c, h) in zip(vectors, pending, strict=True)
        ]

    rows = reused_rows + new_rows
    if not rows:
        print(f"no indexable chunks found in {repo_path}", file=sys.stderr)
        return 0

    db.create_table(table_name, data=rows, mode="overwrite")
    print(f"indexed '{table_name}': {len(rows)} chunks "
          f"({reused_files} files reused, {changed_files} re-embedded)",
          file=sys.stderr)
    return len(rows)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python indexer.py <repo_path> [table_name]")
    index_repo(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
