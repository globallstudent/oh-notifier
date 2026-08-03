"""Process-wide capture hooks and helpers."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import traceback
from types import TracebackType
from typing import Any

from oh_notifier.config import _get_settings_or_none
from oh_notifier.event import ErrorEvent, ErrorSource
from oh_notifier.notifier import TelegramNotifier

logger = logging.getLogger("oh_notifier.utils")


def _service_name() -> str:
    settings = _get_settings_or_none()
    return settings.service_name if settings else "unknown"


def _capture(
    exc: BaseException | None,
    *,
    source: ErrorSource,
    message: str = "",
    error_type: str = "",
    extras: dict[str, str] | None = None,
) -> None:
    """Build and enqueue an event. Never raises."""
    try:
        notifier = TelegramNotifier.get_instance()
        if not notifier:
            return
        tb_text = ""
        if exc is not None:
            tb_text = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
        notifier.capture(
            ErrorEvent(
                service_name=_service_name(),
                error_type=error_type or (type(exc).__name__ if exc else "Error"),
                error_message=message or (str(exc) if exc else ""),
                traceback_text=tb_text,
                source=source,
                extras=extras or {},
            )
        )
    except Exception:
        pass


def safe_create_task(coro: Any, *, name: str | None = None) -> asyncio.Task[Any]:
    """Wrap asyncio.create_task with error capture on failure."""
    task = asyncio.create_task(coro, name=name)

    def _done_callback(t: asyncio.Task[Any]) -> None:
        try:
            exc = t.exception()
        except asyncio.CancelledError:
            return
        except Exception:
            return
        if exc is None:
            return
        _capture(exc, source=ErrorSource.TASK, extras={"task_name": t.get_name()})

    task.add_done_callback(_done_callback)
    return task


def setup_loop_exception_handler(
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Capture unhandled errors on the running event loop."""
    try:
        target = loop or asyncio.get_running_loop()
    except RuntimeError:
        try:
            target = asyncio.get_event_loop_policy().get_event_loop()
        except Exception:
            return

    def _handler(lp: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        _capture(
            exc,
            source=ErrorSource.ASYNCIO,
            message=context.get("message", "Unhandled async exception"),
            error_type=type(exc).__name__ if exc else "AsyncError",
        )
        lp.default_exception_handler(context)

    target.set_exception_handler(_handler)


def setup_excepthooks() -> None:
    """Capture exceptions that kill a thread or reach the interpreter.

    Neither path goes through ``logging``, so without these hooks a crash in
    a plain ``threading.Thread`` — or one that unwinds ``main`` — produced a
    traceback on stderr and no alert at all.
    """
    previous_hook = sys.excepthook

    def _sys_hook(
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            _capture(exc, source=ErrorSource.EXCEPTHOOK)
            _flush_now()
        previous_hook(exc_type, exc, tb)

    sys.excepthook = _sys_hook

    previous_thread_hook = threading.excepthook

    def _thread_hook(args: Any) -> None:
        exc_value = getattr(args, "exc_value", None)
        exc_type = getattr(args, "exc_type", None)
        if exc_value is not None and exc_type is not None and not issubclass(
            exc_type, (KeyboardInterrupt, SystemExit)
        ):
            _capture(
                exc_value,
                source=ErrorSource.THREAD,
                extras={"thread": getattr(getattr(args, "thread", None), "name", "") or ""},
            )
        previous_thread_hook(args)

    threading.excepthook = _thread_hook


def _flush_now() -> None:
    """Ask the delivery worker to send immediately. Does not wait."""
    notifier = TelegramNotifier.get_instance()
    if notifier:
        notifier.request_flush()


def sync_flush() -> None:
    """Request a flush from synchronous code (Celery tasks, scripts).

    Only signals the delivery worker; the caller is never blocked. The old
    implementation built a fresh event loop and drove the existing
    ``httpx.AsyncClient`` on it — a client is bound to the loop that created
    it, so this hung or raised, and it ran inline on every Celery task
    failure, stalling the task for up to the HTTP timeout.
    """
    try:
        _flush_now()
    except Exception:
        pass
