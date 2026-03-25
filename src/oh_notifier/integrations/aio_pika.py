"""aio-pika RabbitMQ consumer error capture."""

from __future__ import annotations

import functools
import logging
import traceback
from typing import Any, Callable

from oh_notifier.config import _get_settings_or_none
from oh_notifier.event import ErrorEvent, ErrorSource
from oh_notifier.notifier import TelegramNotifier

logger = logging.getLogger("oh_notifier.aio_pika")


def safe_consumer_handler(
    handler: Callable | None = None,
    *,
    queue_name: str = "",
    exchange_name: str = "",
) -> Callable:
    """Decorator that wraps an aio_pika message handler with error capture.

    Usage:
        @safe_consumer_handler(queue_name="orders", exchange_name="orders")
        async def handle_order_created(data: dict, message: Any) -> None:
            ...

    Or without arguments:
        @safe_consumer_handler
        async def handle_order_created(data: dict, message: Any) -> None:
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                try:
                    notifier = TelegramNotifier.get_instance()
                    if notifier:
                        settings = _get_settings_or_none()

                        extras: dict[str, str] = {
                            "handler": func.__name__,
                            "queue_name": queue_name,
                            "exchange_name": exchange_name,
                        }

                        # Try to extract routing_key from message arg
                        for arg in args:
                            if hasattr(arg, "routing_key"):
                                extras["routing_key"] = str(arg.routing_key)
                            if hasattr(arg, "message_id"):
                                extras["message_id"] = str(arg.message_id)

                        event = ErrorEvent(
                            service_name=settings.service_name if settings else "unknown",
                            error_type=type(exc).__name__,
                            error_message=str(exc),
                            traceback_text=traceback.format_exc(),
                            source=ErrorSource.RABBITMQ,
                            extras=extras,
                        )
                        notifier.capture(event)
                except Exception:
                    pass
                raise  # Re-raise so consumer can nack properly

        return wrapper

    if handler is not None:
        return decorator(handler)
    return decorator
