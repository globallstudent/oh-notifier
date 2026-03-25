"""oh-notifier: Lightweight error monitoring for Python services."""

from __future__ import annotations

import logging
from typing import Any

from oh_notifier.config import OhNotifierSettings, _set_settings
from oh_notifier.context import init_env_info, set_request_context, get_request_context
from oh_notifier.event import (
    ErrorCategory,
    ErrorEvent,
    ErrorSeverity,
    ErrorSource,
    set_app_frame_pattern,
)
from oh_notifier.notifier import TelegramNotifier

__version__ = "0.1.0"

__all__ = [
    "configure",
    "start",
    "stop",
    "send_alert",
    "send_warning",
    "send_info",
    "set_request_context",
    "get_request_context",
    "ErrorEvent",
    "ErrorSeverity",
    "ErrorCategory",
    "ErrorSource",
]


def configure(
    bot_token: str = "",
    chat_id: str = "",
    service_name: str = "unknown",
    environment: str = "development",
    enabled: bool = True,
    timezone: str = "UTC",
    dedup_window: float = 300.0,
    max_buffer_size: int = 50,
    flush_interval: float = 2.0,
    rate_limit_interval: float = 1.0,
    min_log_level: int = logging.ERROR,
    sensitive_keys: frozenset[str] | None = None,
    app_frame_pattern: str | None = None,
) -> TelegramNotifier:
    """Configure oh-notifier. Call once at startup before anything else."""
    settings = OhNotifierSettings(
        bot_token=bot_token,
        chat_id=chat_id,
        service_name=service_name,
        environment=environment,
        enabled=enabled,
        timezone=timezone,
        dedup_window=dedup_window,
        max_buffer_size=max_buffer_size,
        flush_interval=flush_interval,
        rate_limit_interval=rate_limit_interval,
        min_log_level=min_log_level,
    )

    if sensitive_keys is not None:
        settings.sensitive_keys = sensitive_keys

    if app_frame_pattern:
        settings.app_frame_pattern = app_frame_pattern
        set_app_frame_pattern(app_frame_pattern)

    _set_settings(settings)
    init_env_info(app_env=environment)

    return TelegramNotifier.initialize(settings)


async def start() -> None:
    """Start the notifier background flush loop."""
    notifier = TelegramNotifier.get_instance()
    if notifier:
        await notifier.start()


async def stop() -> None:
    """Stop the notifier and flush remaining errors."""
    notifier = TelegramNotifier.get_instance()
    if notifier:
        await notifier.stop()


def send_alert(
    error_message: str,
    *,
    error_type: str = "Alert",
    source: ErrorSource = ErrorSource.LOGGER,
    extras: dict[str, str] | None = None,
    **kwargs: Any,
) -> None:
    """Send an error-level alert to Telegram."""
    _send(ErrorSeverity.ERROR, error_type, error_message, source, extras, kwargs)


def send_warning(
    error_message: str,
    *,
    error_type: str = "Warning",
    source: ErrorSource = ErrorSource.LOGGER,
    extras: dict[str, str] | None = None,
    **kwargs: Any,
) -> None:
    """Send a warning-level alert to Telegram."""
    _send(ErrorSeverity.WARNING, error_type, error_message, source, extras, kwargs)


def send_info(
    error_message: str,
    *,
    error_type: str = "Info",
    source: ErrorSource = ErrorSource.LOGGER,
    extras: dict[str, str] | None = None,
    **kwargs: Any,
) -> None:
    """Send an info-level notification to Telegram."""
    _send(ErrorSeverity.INFO, error_type, error_message, source, extras, kwargs)


def _send(
    severity: ErrorSeverity,
    error_type: str,
    error_message: str,
    source: ErrorSource,
    extras: dict[str, str] | None,
    kwargs: dict[str, Any],
) -> None:
    notifier = TelegramNotifier.get_instance()
    if not notifier:
        return

    merged_extras = dict(extras or {})
    merged_extras.update({k: str(v) for k, v in kwargs.items() if v is not None})

    event = ErrorEvent(
        service_name=notifier.service_name,
        error_type=error_type,
        error_message=error_message,
        source=source,
        severity=severity,
        extras=merged_extras,
    )
    notifier.capture(event)
