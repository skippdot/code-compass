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


def test_cwd_fallback_for_a_project_dir():
    restore = _with_paths({})
    orig = os.getcwd
    os.getcwd = lambda: "/work/myproj"
    try:
        assert server._resolve_path("anything", None) == "/work/myproj"
    finally:
        os.getcwd = orig
        restore()


def test_refuses_home_and_root():
    restore = _with_paths({})
    orig = os.getcwd
    try:
        os.getcwd = lambda: os.path.expanduser("~")
        assert server._resolve_path("r", None) is None
        os.getcwd = lambda: os.sep
        assert server._resolve_path("r", None) is None
    finally:
        os.getcwd = orig
        restore()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
