"""Core TelegramNotifier — orchestrates buffer, formatter, and sender."""

from __future__ import annotations

import asyncio
import logging

from oh_notifier.config import OhNotifierSettings
from oh_notifier.event import ErrorEvent
from oh_notifier.formatter import format_error_html
from oh_notifier.rate_limiter import ErrorBuffer
from oh_notifier.sender import TelegramSender

logger = logging.getLogger("oh_notifier")


class TelegramNotifier:
    """Singleton error notifier with buffered, deduped Telegram delivery."""

    _instance: TelegramNotifier | None = None

    def __init__(self, settings: OhNotifierSettings) -> None:
        self._settings = settings
        self._buffer = ErrorBuffer(
            dedup_window=settings.dedup_window,
            max_size=settings.max_buffer_size,
        )
        self._sender = TelegramSender(
            bot_token=settings.bot_token,
            chat_id=settings.chat_id,
            rate_limit=settings.rate_limit_interval,
        )
        self._flush_task: asyncio.Task[None] | None = None
        self._running = False

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

    async def start(self) -> None:
        """Start the background flush loop."""
        if not self._settings.enabled:
            logger.info("oh-notifier is disabled")
            return
        await self._sender.start()
        self._running = True
        self._flush_task = asyncio.create_task(
            self._flush_loop(), name="oh-notifier-flush"
        )
        logger.info("oh-notifier started")

    async def stop(self) -> None:
        """Stop flush loop, do a final flush, close sender."""
        self._running = False
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        try:
            await self._flush_buffer()
        except Exception:
            pass
        await self._sender.stop()
        logger.info("oh-notifier stopped")

    def capture(self, event: ErrorEvent) -> None:
        """Thread-safe: enqueue an error event into the buffer with dedup."""
        if not self._settings.enabled:
            return
        overflow = self._buffer.add(event)
        if overflow:
            self._schedule_immediate_flush()

    def _schedule_immediate_flush(self) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(self._flush_buffer())
                )
        except RuntimeError:
            pass

    async def _flush_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._settings.flush_interval)
                await self._flush_buffer()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _flush_buffer(self) -> None:
        items = self._buffer.drain()
        for event, count in items:
            try:
                message = format_error_html(event, count)
                await self._sender.send(message)
            except Exception:
                pass
