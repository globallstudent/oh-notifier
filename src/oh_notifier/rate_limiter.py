"""Dedup buffer with fingerprint-based grouping."""

from __future__ import annotations

import threading
import time

from oh_notifier.categorizer import categorize
from oh_notifier.context import get_request_context
from oh_notifier.event import ErrorEvent


class ErrorBuffer:
    """Thread-safe error buffer with dedup by fingerprint."""

    def __init__(self, dedup_window: float = 300.0, max_size: int = 50) -> None:
        self._dedup_window = dedup_window
        self._max_size = max_size
        # fingerprint -> (event, count, first_seen_monotonic)
        self._buffer: dict[str, tuple[ErrorEvent, int, float]] = {}
        self._lock = threading.Lock()

    def add(self, event: ErrorEvent) -> bool:
        """Add event to buffer with dedup. Returns True if buffer overflow."""
        # Auto-merge request context
        try:
            ctx = get_request_context()
            if ctx:
                merged = dict(ctx)
                merged.update(event.extras)
                event.extras = merged
        except Exception:
            pass

        # Auto-categorize
        try:
            categorize(event)
        except Exception:
            pass

        fp = event.fingerprint
        now = time.monotonic()
        overflow = False

        with self._lock:
            if fp in self._buffer:
                existing_event, count, first_seen = self._buffer[fp]
                if (now - first_seen) > self._dedup_window:
                    self._buffer[fp] = (event, 1, now)
                else:
                    self._buffer[fp] = (existing_event, count + 1, first_seen)
            else:
                self._buffer[fp] = (event, 1, now)

            if len(self._buffer) > self._max_size:
                overflow = True

        return overflow

    def drain(self) -> list[tuple[ErrorEvent, int]]:
        """Atomically grab all buffer contents. Returns list of (event, count)."""
        with self._lock:
            if not self._buffer:
                return []
            items = [(ev, count) for ev, count, _ in self._buffer.values()]
            self._buffer.clear()
        return items

    def is_empty(self) -> bool:
        with self._lock:
            return not self._buffer
