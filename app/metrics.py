"""Speed monitor: how long each stage takes, and how it trends over time."""
from __future__ import annotations

import threading
from collections import deque


class Monitor:
    """Totals and recent history for every request the app has handled."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests = 0
        self.pages = 0
        self.windows = 0
        self.windows_skipped = 0
        self.read_ms = 0
        self.render_ms = 0
        self.refine_ms = 0
        self.merge_ms = 0
        self.lines = 0
        self.history: deque[dict] = deque(maxlen=30)
        self.last: dict | None = None

    def record_page(self, timing: dict) -> None:
        with self._lock:
            self.pages += 1
            self.windows += timing.get("windows", 0)
            self.windows_skipped += timing.get("windows_skipped", 0)
            self.read_ms += timing.get("read_ms", 0)
            self.render_ms += timing.get("render_ms", 0)
            self.refine_ms += timing.get("refine_ms", 0)
            self.merge_ms += timing.get("merge_ms", 0)
            self.lines += timing.get("lines", 0)

    def record_request(self, summary: dict) -> None:
        with self._lock:
            self.requests += 1
            self.last = summary
            self.history.append(summary)

    def snapshot(self) -> dict:
        with self._lock:
            windows = max(1, self.windows)
            pages = max(1, self.pages)
            return {
                "requests": self.requests,
                "pages": self.pages,
                "windows": self.windows,
                "windows_skipped": self.windows_skipped,
                "lines": self.lines,
                "avg_ms_per_page": round((self.read_ms + self.render_ms) / pages),
                "avg_ms_per_window": round(self.read_ms / windows),
                "total_read_ms": self.read_ms,
                "total_render_ms": self.render_ms,
                "total_refine_ms": self.refine_ms,
                "total_merge_ms": self.merge_ms,
                "last": self.last,
                "history": list(self.history),
            }


MONITOR = Monitor()
