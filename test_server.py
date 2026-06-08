"""Unit tests for server.py auto-index path resolution.

Pure (no network, no embedder): they only exercise `_resolve_path`'s precedence
and the safety guard that refuses to auto-index $HOME / the filesystem root.
"""

import os

import server


def _with_paths(mapping):
    """Swap server._load_paths for `mapping`; return a restore() callable."""
    orig = server._load_paths
    server._load_paths = lambda: mapping
    return lambda: setattr(server, "_load_paths", orig)


def test_explicit_path_wins_over_saved():
    restore = _with_paths({"r": "/saved/r"})
    try:
        assert server._resolve_path("r", "/tmp/explicit") == "/tmp/explicit"
    finally:
        restore()


def test_saved_mapping_used_when_no_arg():
    restore = _with_paths({"r": "/saved/r"})
    try:
        assert server._resolve_path("r", None) == "/saved/r"
    finally:
        restore()


def test_resolve_path_does_not_guess_cwd():
    # the cwd fallback lives in _safe_cwd, not _resolve_path — so an unknown
    # repo with no explicit/saved path resolves to None (no clobber risk).
    restore = _with_paths({})
    try:
        assert server._resolve_path("unknown", None) is None
    finally:
        restore()


def test_safe_cwd_returns_project_dir():
    orig = os.getcwd
    os.getcwd = lambda: "/work/myproj"
    try:
        assert server._safe_cwd() == "/work/myproj"
    finally:
        os.getcwd = orig


def test_safe_cwd_refuses_home_and_root():
    orig = os.getcwd
    try:
        os.getcwd = lambda: os.path.expanduser("~")
        assert server._safe_cwd() is None
        os.getcwd = lambda: os.sep
        assert server._safe_cwd() is None
    finally:
        os.getcwd = orig


def test_existing_repo_not_reindexed_from_guessed_cwd():
    """Regression: a repo already on disk with no explicit/saved path must be
    searched as-is, never re-indexed from the guessed cwd (which would clobber
    it with the wrong directory)."""
    calls = []
    orig_index, orig_repos = server.index_repo, server._repos
    restore = _with_paths({})  # no saved path
    server.index_repo = lambda *a, **k: calls.append(a)  # tripwire
    server._repos = lambda: ["existing"]
    server._indexed_from.pop("existing", None)
    try:
        assert server._ensure_ready("existing", None) is None
        assert calls == [], "must not reindex an existing repo from a guessed cwd"
        assert "existing" in server._indexed_from
    finally:
        server.index_repo, server._repos = orig_index, orig_repos
        server._indexed_from.pop("existing", None)
        restore()


def test_explicit_path_reindexes_even_after_search_as_is():
    """An explicit repo_path is honored even after the repo was first touched
    as search-as-is — the caller's path must not be silently dropped."""
    calls = []
    orig = (server.index_repo, server._repos, server._save_path,
            server._start_watcher)
    server.index_repo = lambda p, r: calls.append((p, r))
    server._repos = lambda: ["r"]
    server._save_path = lambda *a: None
    server._start_watcher = lambda *a: None
    server._indexed_from.pop("r", None)
    try:
        # first: no path -> searched as-is, nothing indexed
        assert server._ensure_ready("r", None) is None
        assert calls == []
        # then: explicit path -> must reindex from it now
        assert server._ensure_ready("r", "/proj/r") is None
        assert calls == [("/proj/r", "r")]
        # repeat same path -> no-op (watcher covers later changes)
        assert server._ensure_ready("r", "/proj/r") is None
        assert calls == [("/proj/r", "r")]
    finally:
        (server.index_repo, server._repos, server._save_path,
         server._start_watcher) = orig
        server._indexed_from.pop("r", None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
