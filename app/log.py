"""Logging for the OCR app: console, rotating file, and a ring buffer for the UI."""
from __future__ import annotations

import logging
import logging.handlers
import os
import time
from collections import deque

LOG = logging.getLogger("mirage")
RING: deque[dict] = deque(maxlen=500)


class RingHandler(logging.Handler):
    """Keeps the last few hundred records so the web UI can show them."""

    def emit(self, record: logging.LogRecord) -> None:
        RING.append(
            {
                "time": time.strftime("%H:%M:%S", time.localtime(record.created)),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
        )


def setup(path: str | None = None) -> logging.Logger:
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
    if LOG.handlers:
        return LOG

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", "%H:%M:%S"))
    LOG.addHandler(console)

    ring = RingHandler()
    ring.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", "%H:%M:%S"))
    LOG.addHandler(ring)

    target = path or os.environ.get("OCR_LOG_FILE", "logs/app.log")
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(target, maxBytes=4_000_000, backupCount=3)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(name)s %(message)s"))
    LOG.addHandler(file_handler)
    return LOG


def recent(limit: int = 200) -> list[dict]:
    return list(RING)[-limit:]
