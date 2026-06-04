"""Tests for indexer.py: .gitignore respect + incremental reuse.

Offline — a FakeEmbedder stands in for Voyage and counts how many chunks it was
asked to embed, which is exactly what "incremental" must minimize.
"""

import os
import subprocess
import tempfile

import indexer
from indexer import _iter_source_files, index_repo


class FakeEmbedder:
    dim = 8

    def __init__(self):
        self.embedded = 0  # total texts embedded across calls

    def embed_documents(self, texts):
        self.embedded += len(texts)
        return [[float(len(t) % (i + 2)) for i in range(self.dim)] for t in texts]

    def embed_query(self, text):
        return [0.0] * self.dim


def _write(root, rel, content):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(rel) else None
    with open(path, "w") as f:
        f.write(content)


def _git_repo(root):
    subprocess.run(["git", "init", "-q", root], check=True)


def test_gitignore_is_respected():
    with tempfile.TemporaryDirectory() as d:
        _git_repo(d)
        _write(d, "keep.py", "def foo():\n    return 1\n")
        _write(d, "debug.log", "noise that matches queries\n")
        _write(d, ".gitignore", "*.log\n")
        paths = dict(_iter_source_files(d))
        assert "keep.py" in paths
        assert "debug.log" not in paths  # gitignored -> never indexed


def test_incremental_only_reembeds_changed_files():
    prev_db = indexer.DB_PATH
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as db:
        indexer.DB_PATH = os.path.join(db, "lancedb")
        try:
            _git_repo(d)
            _write(d, "a.py", "def a():\n    return 1\n")
            _write(d, "b.py", "def b():\n    return 2\n")

            emb = FakeEmbedder()
            index_repo(d, "t", emb)
            first = emb.embedded
            assert first > 0

            # re-index with no changes -> zero new embeddings
            emb2 = FakeEmbedder()
            index_repo(d, "t", emb2)
            assert emb2.embedded == 0, "unchanged files must be reused"

            # change one file -> only its chunks re-embed
            _write(d, "a.py", "def a():\n    return 999\n")
            emb3 = FakeEmbedder()
            index_repo(d, "t", emb3)
            assert 0 < emb3.embedded < first, "only the changed file re-embeds"
        finally:
            indexer.DB_PATH = prev_db


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
