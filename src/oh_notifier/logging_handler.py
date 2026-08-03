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
    "task_name", "task_id", "queue_name", "routing_key", "job_id",
})

_LEVEL_TO_SOURCE = {
    logging.CRITICAL: ErrorSource.LOGGER,
    logging.ERROR: ErrorSource.LOGGER,
    logging.WARNING: ErrorSource.LOGGER,
}


class OhLoggingHandler(logging.Handler):
    """Logging handler that sends log records to Telegram via oh-notifier."""

    def __init__(self, level: int | None = None) -> None:
        settings = _get_settings_or_none()
        effective_level = (
            level if level is not None
            else (settings.min_log_level if settings else logging.ERROR)
        )
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

            exc = record.exc_info[1] if record.exc_info else None
            tb_text = ""
            if record.exc_info and exc is not None:
                tb_text = "".join(traceback.format_exception(*record.exc_info))

            if exc is not None:
                error_type = type(exc).__name__
            else:
                # "LogError" for everything made every traceback-less record
                # look alike. The level plus the logger's own name is far more
                # useful at a glance, and feeds a better fingerprint.
                error_type = f"{record.levelname.title()}:{record.name.split('.')[-1]}"

            extras: dict[str, str] = {"logger": record.name}
            if record.funcName:
                extras["func"] = f"{record.module}.{record.funcName}:{record.lineno}"

            for key in _LOGGER_EXTRA_KEYS:
                val = getattr(record, key, None)
                if val is not None:
                    target_key = "response_body" if key == "body" else key
                    extras[target_key] = str(val)

            status_code = 0
            raw_status = getattr(record, "status_code", None)
            if raw_status is not None:
                try:
                    status_code = int(raw_status)
                except (TypeError, ValueError):
                    status_code = 0

            event = ErrorEvent(
                service_name=settings.service_name,
                error_type=error_type,
                error_message=record.getMessage(),
                traceback_text=tb_text,
                status_code=status_code,
                source=_LEVEL_TO_SOURCE.get(record.levelno, ErrorSource.LOGGER),
                environment=settings.environment,
                extras=extras,
            )
            notifier.capture(event)
        except Exception:
            pass
