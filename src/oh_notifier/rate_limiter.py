"""Dedup buffer with fingerprint-based grouping."""

from __future__ import annotations

import threading
import time

from oh_notifier.categorizer import categorize
from oh_notifier.context import get_request_context
from oh_notifier.event import ErrorEvent


class ErrorBuffer:

    def __init__(
        self,
        dedup_window: float = 300.0,
        max_size: int = 50,
        max_pending: int = 500,
    ) -> None:
        self._dedup_window = dedup_window
        self._max_size = max_size
        self._max_pending = max_pending
        # fingerprint -> (event, count, first_seen_monotonic)
        self._buffer: dict[str, tuple[ErrorEvent, int, float]] = {}
        self._lock = threading.Lock()
        self._dropped = 0

    def add(self, event: ErrorEvent) -> bool:
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

        with self._lock:
            existing = self._buffer.get(fp)
            if existing is not None:
                existing_event, count, first_seen = existing
                if (now - first_seen) > self._dedup_window:
                    self._buffer[fp] = (event, 1, now)
                else:
                    self._buffer[fp] = (existing_event, count + 1, first_seen)
            else:
                if len(self._buffer) >= self._max_pending:
                    # Hard ceiling. Count the loss so the next message can
                    # say so — a silently truncated storm reads like calm.
                    self._dropped += 1
                    return True
                self._buffer[fp] = (event, 1, now)

            return len(self._buffer) >= self._max_size

    def drain(self) -> tuple[list[tuple[ErrorEvent, int]], int]:
        """Atomically take the buffer. Returns ``(items, dropped_since_last)``."""
        with self._lock:
            if not self._buffer and not self._dropped:
                return [], 0
            items = [(ev, count) for ev, count, _ in self._buffer.values()]
            dropped = self._dropped
            self._buffer.clear()
            self._dropped = 0
        return items, dropped

    def is_empty(self) -> bool:
        with self._lock:
            return not self._buffer

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._buffer)
