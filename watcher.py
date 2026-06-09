"""Auto-reindex a repo on file save (incremental, so each trigger is cheap).

Pairs with the indexer's incremental mode: a save fires a (debounced) reindex,
which only re-embeds the file(s) that actually changed. Keeps the search index
live the way Augment's does, without a manual `python indexer.py` each time.

Run:  python watcher.py <repo_path> [table_name]
"""

import os
import sys
import threading
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from indexer import DB_PATH, index_repo

# Path segments whose writes are noise, not source edits. The index store is the
# critical one: a reindex writes into it, so watching it would re-trigger the
# reindex forever (an infinite loop that, with the local model, exhausts RAM).
_IGNORE_SEGMENTS = tuple(
    os.sep + d + os.sep for d in (".git", "__pycache__", ".venv", "node_modules")
)


class _DebouncedReindex(FileSystemEventHandler):
    """Coalesces a burst of save events into one reindex after `delay` quiet.

    `on_change` is injected (not hard-wired to index_repo) so the debounce
    logic can be tested without touching the filesystem or an embedder.
    `ignore_paths` are absolute prefixes whose events are dropped (the store).
    """

    def __init__(self, on_change, delay: float = 1.5, ignore_paths=()):
        self._on_change = on_change
        self._delay = delay
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._ignore_paths = tuple(os.path.abspath(p) for p in ignore_paths)

    def _ignored(self, path: str) -> bool:
        if any(seg in path for seg in _IGNORE_SEGMENTS):
            return True
        ap = os.path.abspath(path)
        return any(ap.startswith(p) for p in self._ignore_paths)

    def on_any_event(self, event):
        if event.is_directory or self._ignored(event.src_path):
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._on_change)
            self._timer.start()


def start_watch(repo_path: str, table_name: str | None = None) -> Observer:
    """Start a debounced auto-reindex observer and return it (non-blocking).

    The watchdog Observer runs on its own thread, so an in-process caller (the
    MCP server) can keep one alive for the life of the process without the
    blocking loop in `watch()`. All progress goes to stderr so it never
    corrupts an MCP server's stdout JSON-RPC stream.
    """
    repo_path = os.path.abspath(repo_path)

    def reindex():
        print("change detected -> reindexing...", file=sys.stderr)
        try:
            index_repo(repo_path, table_name)
        except Exception as exc:  # keep watching even if one run fails
            print(f"reindex failed: {exc}", file=sys.stderr)

    # ignore the index store so reindex writes don't re-trigger us (loop guard)
    handler = _DebouncedReindex(reindex, ignore_paths=[DB_PATH])
    observer = Observer()
    observer.schedule(handler, repo_path, recursive=True)
    observer.start()
    return observer


def watch(repo_path: str, table_name: str | None = None) -> None:
    """Blocking CLI form: watch until Ctrl-C."""
    observer = start_watch(repo_path, table_name)
    print(f"watching {os.path.abspath(repo_path)} (Ctrl-C to stop)",
          file=sys.stderr)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python watcher.py <repo_path> [table_name]")
    watch(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
