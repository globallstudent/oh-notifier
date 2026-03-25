"""Python logging handler that sends ERROR+ records to Telegram."""

from __future__ import annotations

import logging
import traceback

from oh_notifier.config import _get_settings_or_none
from oh_notifier.event import ErrorEvent, ErrorSource
from oh_notifier.notifier import TelegramNotifier

# Structured logging keys to extract from log records
_LOGGER_EXTRA_KEYS = frozenset({
    "method", "error_code", "error_message", "elapsed_ms",
    "status_code", "request_id", "attempt", "response_body",
    "hamkor_method", "hamkor_request_id", "hamkor_status",
    "hamkor_response", "hamkor_error", "order_id",
    "card_number_last4", "body", "content_type", "content_length",
})


class OhLoggingHandler(logging.Handler):
    """Logging handler that sends log records to Telegram via oh-notifier."""

    def __init__(self, level: int | None = None) -> None:
        settings = _get_settings_or_none()
        effective_level = level or (settings.min_log_level if settings else logging.ERROR)
        super().__init__(level=effective_level)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            settings = _get_settings_or_none()
            if not settings:
                return

            # Prevent recursion
            if record.name in settings.skip_loggers or record.name.startswith("oh_notifier"):
                return

            notifier = TelegramNotifier.get_instance()
            if not notifier:
                return

            tb_text = ""
            if record.exc_info and record.exc_info[1]:
                tb_text = "".join(traceback.format_exception(*record.exc_info))

            error_type = (
                type(record.exc_info[1]).__name__
                if record.exc_info and record.exc_info[1]
                else "LogError"
            )

            extras: dict[str, str] = {"logger": record.name}

            for key in _LOGGER_EXTRA_KEYS:
                val = getattr(record, key, None)
                if val is not None:
                    target_key = "response_body" if key == "body" else key
                    extras[target_key] = str(val)

            event = ErrorEvent(
                service_name=settings.service_name,
                error_type=error_type,
                error_message=record.getMessage(),
                traceback_text=tb_text,
                source=ErrorSource.LOGGER,
                extras=extras,
            )
            notifier.capture(event)
        except Exception:
            pass
