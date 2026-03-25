"""Utility functions: safe_create_task, setup_loop_exception_handler, sync_flush."""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Any

from oh_notifier.config import _get_settings_or_none
from oh_notifier.event import ErrorEvent, ErrorSource
from oh_notifier.notifier import TelegramNotifier

logger = logging.getLogger("oh_notifier.utils")


def safe_create_task(coro: Any, *, name: str | None = None) -> asyncio.Task[Any]:
    """Wrap asyncio.create_task with error capture on failure."""
    task = asyncio.create_task(coro, name=name)

    def _done_callback(t: asyncio.Task[Any]) -> None:
        try:
            exc = t.exception()
        except asyncio.CancelledError:
            return
        if exc is None:
            return
        try:
            notifier = TelegramNotifier.get_instance()
            if not notifier:
                return
            settings = _get_settings_or_none()
            event = ErrorEvent(
                service_name=settings.service_name if settings else "unknown",
                error_type=type(exc).__name__,
                error_message=str(exc),
                traceback_text="".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                ),
                source=ErrorSource.TASK,
                extras={"task_name": t.get_name()},
            )
            notifier.capture(event)
        except Exception:
            pass

    task.add_done_callback(_done_callback)
    return task


def setup_loop_exception_handler() -> None:
    """Set asyncio loop exception handler to capture unhandled async errors."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return

    def _handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        try:
            notifier = TelegramNotifier.get_instance()
            if not notifier:
                loop.default_exception_handler(context)
                return

            exc = context.get("exception")
            message = context.get("message", "Unhandled async exception")
            settings = _get_settings_or_none()

            tb_text = ""
            if exc:
                tb_text = "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )

            event = ErrorEvent(
                service_name=settings.service_name if settings else "unknown",
                error_type=type(exc).__name__ if exc else "AsyncError",
                error_message=message,
                traceback_text=tb_text,
                source=ErrorSource.ASYNCIO,
            )
            notifier.capture(event)
        except Exception:
            pass

        loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)


def sync_flush() -> None:
    """Synchronously flush the notifier buffer. For use in Celery workers."""
    try:
        notifier = TelegramNotifier.get_instance()
        if not notifier:
            return
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(notifier._flush_buffer())
        finally:
            loop.close()
    except Exception:
        pass
