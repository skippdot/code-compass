"""Test the watcher's debounce — a burst of events triggers exactly one run.

Deterministic: we exercise the handler directly instead of relying on OS file
events (FSEvents/inotify timing is flaky in tests).
"""

import time

from watcher import _DebouncedReindex


class _Event:
    def __init__(self, path, is_directory=False):
        self.src_path = path
        self.is_directory = is_directory


def test_burst_of_events_coalesces_to_one_reindex():
    calls = []
    h = _DebouncedReindex(lambda: calls.append(1), delay=0.2)

    for _ in range(8):  # editor save storm
        h.on_any_event(_Event("/repo/a.py"))
        time.sleep(0.02)  # all within the debounce window
    assert calls == []  # nothing fired yet
    time.sleep(0.35)
    assert calls == [1], "burst should collapse into a single reindex"

    # a later save triggers another, independent reindex
    h.on_any_event(_Event("/repo/a.py"))
    time.sleep(0.35)
    assert calls == [1, 1]


def test_git_and_directory_events_are_ignored():
    calls = []
    h = _DebouncedReindex(lambda: calls.append(1), delay=0.15)
    h.on_any_event(_Event("/repo/.git/index"))      # git churn
    h.on_any_event(_Event("/repo/subdir", is_directory=True))
    time.sleep(0.3)
    assert calls == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn) and not isinstance(fn, type):
            fn()
            print(f"PASS {name}")
