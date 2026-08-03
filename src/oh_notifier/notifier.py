"""Core TelegramNotifier — orchestrates buffer, formatter, and sender."""

from __future__ import annotations

import asyncio
import atexit
import logging

from oh_notifier.config import OhNotifierSettings
from oh_notifier.dispatcher import DeliveryWorker
from oh_notifier.event import ErrorEvent
from oh_notifier.formatter import format_error_html, format_notice
from oh_notifier.rate_limiter import ErrorBuffer
from oh_notifier.sender import TelegramSender

logger = logging.getLogger("oh_notifier")


class TelegramNotifier:

    _instance: TelegramNotifier | None = None

    def __init__(self, settings: OhNotifierSettings) -> None:
        self._settings = settings
        self._buffer = ErrorBuffer(
            dedup_window=settings.dedup_window,
            max_size=settings.max_buffer_size,
            max_pending=settings.max_pending_events,
        )
        self._sender = TelegramSender(
            bot_token=settings.bot_token,
            chat_id=settings.chat_id,
            rate_limit=settings.rate_limit_interval,
            timeout=settings.send_timeout,
            max_attempts=settings.max_send_attempts,
        )
        self._worker = DeliveryWorker(
            flush=self._flush_buffer,
            setup=self._sender.start,
            teardown=self._sender.stop,
            interval=settings.flush_interval,
        )
        self._flush_lock = asyncio.Lock()
        self._atexit_registered = False

    @classmethod
    def initialize(cls, settings: OhNotifierSettings) -> TelegramNotifier:
        """Create and set the singleton instance."""
        instance = cls(settings)
        cls._instance = instance
        return instance

    @classmethod
    def get_instance(cls) -> TelegramNotifier | None:
        return cls._instance

    @property
    def service_name(self) -> str:
        return self._settings.service_name

    @property
    def settings(self) -> OhNotifierSettings:
        return self._settings

    @property
    def stats(self) -> dict[str, int | str]:
        """Delivery counters — useful from a health endpoint."""
        s = self._sender.stats
        return {
            "sent": s.sent,
            "failed": s.failed,
            "retried": s.retried,
            "html_rejected": s.html_rejected,
            "pending": self._buffer.pending,
            "last_error": s.last_error,
        }

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start the background delivery worker."""
        if not self._settings.alerting_allowed():
            logger.info(
                "oh-notifier is not alerting (enabled=%s environment=%s)",
                self._settings.enabled,
                self._settings.environment,
            )
            return
        if not self._sender.configured:
            logger.warning(
                "oh-notifier has no bot_token/chat_id — errors are buffered but "
                "cannot be delivered"
            )
        self._worker.start()
        if not self._atexit_registered:
            atexit.register(self._atexit_flush)
            self._atexit_registered = True
        logger.info(
            "oh-notifier started (environment=%s via %s)",
            self._settings.environment,
            self._settings.environment_source,
        )

    async def stop(self) -> None:
        """Stop the worker after a final drain."""
        await self._worker.astop()
        logger.info("oh-notifier stopped")

    def _atexit_flush(self) -> None:
        """Last-chance drain if stop() was never called (crash, SIGKILL-less exit)."""
        try:
            self._worker.stop(timeout=5.0)
        except Exception:
            pass

    # -- capture -----------------------------------------------------------

    def request_flush(self) -> None:
        """Ask the worker to deliver now. Thread-safe, never blocks."""
        self._worker.wake()

    def capture(self, event: ErrorEvent) -> None:
        """Thread-safe, non-blocking: enqueue an error event with dedup."""
        if not self._settings.alerting_allowed():
            return
        if not event.environment:
            event.environment = self._settings.environment
        try:
            if self._buffer.add(event):
                self._worker.wake()
        except Exception:
            # Capturing an error must never raise into application code.
            pass

    # -- delivery ----------------------------------------------------------

    async def _flush_buffer(self) -> None:
        """Drain the buffer and deliver. Serialized against itself."""
        async with self._flush_lock:
            items, dropped = self._buffer.drain()
            if not items and not dropped:
                return

            messages = [format_error_html(event, count) for event, count in items]

            failed, last_error = self._sender.stats.snapshot_and_reset_failures()
            notices: list[str] = []
            if dropped:
                notices.append(
                    f"{dropped} further distinct error(s) dropped — buffer ceiling "
                    f"({self._settings.max_pending_events}) reached"
                )
            if failed:
                notices.append(f"{failed} earlier alert(s) failed to send: {last_error}")
            if notices:
                messages.append(format_notice(self._settings, notices))

            if self._settings.batch_messages:
                messages = _pack(messages, self._settings.max_message_len)

            for message in messages:
                try:
                    await self._sender.send(message)
                except Exception:
                    pass


def _pack(messages: list[str], limit: int) -> list[str]:
    if len(messages) <= 1:
        return messages

    joiner = "\n\n"
    packed: list[str] = []
    current = ""
    for message in messages:
        if not current:
            current = message
        elif len(current) + len(joiner) + len(message) <= limit:
            current = f"{current}{joiner}{message}"
        else:
            packed.append(current)
            current = message
    if current:
        packed.append(current)
    return packed
