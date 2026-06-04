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

from indexer import index_repo


class _DebouncedReindex(FileSystemEventHandler):
    """Coalesces a burst of save events into one reindex after `delay` quiet.

    `on_change` is injected (not hard-wired to index_repo) so the debounce
    logic can be tested without touching the filesystem or an embedder.
    """

    def __init__(self, on_change, delay: float = 1.5):
        self._on_change = on_change
        self._delay = delay
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def on_any_event(self, event):
        # ignore directory events and git's own churn (.git writes constantly)
        if event.is_directory or os.sep + ".git" + os.sep in event.src_path:
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._on_change)
            self._timer.start()


def watch(repo_path: str, table_name: str | None = None) -> None:
    repo_path = os.path.abspath(repo_path)

    def reindex():
        print("change detected -> reindexing...")
        try:
            index_repo(repo_path, table_name)
        except Exception as exc:  # keep watching even if one run fails
            print(f"reindex failed: {exc}")

    handler = _DebouncedReindex(reindex)
    observer = Observer()
    observer.schedule(handler, repo_path, recursive=True)
    observer.start()
    print(f"watching {repo_path} (Ctrl-C to stop)")
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
