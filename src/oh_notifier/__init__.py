"""oh-notifier: Lightweight error monitoring for Python services."""

from __future__ import annotations

import logging
import os
from typing import Any

from oh_notifier.config import OhNotifierSettings, _set_settings
from oh_notifier.context import (
    get_request_context,
    init_env_info,
    set_request_context,
)
from oh_notifier.env import (
    UNKNOWN_ENVIRONMENT,
    load_dotenv_files,
    resolve_environment,
)
from oh_notifier.event import (
    ErrorCategory,
    ErrorEvent,
    ErrorSeverity,
    ErrorSource,
    set_app_frame_pattern,
)
from oh_notifier.notifier import TelegramNotifier

__version__ = "0.2.1"

logger = logging.getLogger("oh_notifier")

__all__ = [
    "configure",
    "start",
    "stop",
    "send_alert",
    "send_warning",
    "send_info",
    "set_request_context",
    "get_request_context",
    "stats",
    "ErrorEvent",
    "ErrorSeverity",
    "ErrorCategory",
    "ErrorSource",
]


def configure(
    bot_token: str = "",
    chat_id: str = "",
    service_name: str = "unknown",
    environment: str | None = None,
    enabled: bool = True,
    timezone: str = "UTC",
    dedup_window: float = 300.0,
    max_buffer_size: int = 50,
    flush_interval: float = 2.0,
    rate_limit_interval: float = 1.0,
    min_log_level: int = logging.ERROR,
    sensitive_keys: frozenset[str] | None = None,
    app_frame_pattern: str | None = None,
    *,
    load_dotenv: bool = True,
    dotenv_dir: str | os.PathLike[str] | None = None,
    environments_enabled: frozenset[str] | None = None,
    git_commit: str | None = None,
) -> TelegramNotifier:
    """Configure oh-notifier. Call once at startup before anything else.

    Resolution order for every value: this call's arguments, then
    ``OH_NOTIFIER_*`` environment variables (optionally suffixed with the
    environment name for per-environment overrides), then defaults.

    The environment itself is resolved from ``OH_NOTIFIER_ENV``, ``APP_ENV``,
    ``ENVIRONMENT`` or ``ENV``. If none are set the result is ``unknown`` and
    a warning is logged — the previous silent ``"development"`` default is
    why every production alert this project ever sent was labelled
    ``[DEVELOPMENT]``.
    """
    resolved_env, source = resolve_environment(environment)

    applied_files: list[str] = []
    if load_dotenv:
        try:
            applied_files = load_dotenv_files(resolved_env, dotenv_dir)
        except Exception:
            applied_files = []
        if applied_files:
            # A .env may itself define APP_ENV; re-resolve so it counts.
            resolved_env, source = resolve_environment(environment)

    settings = OhNotifierSettings(
        bot_token=bot_token,
        chat_id=chat_id,
        service_name=service_name,
        environment=resolved_env,
        environment_source=source,
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
    if environments_enabled is not None:
        settings.environments_enabled = environments_enabled

    settings.apply_env_overrides()

    if app_frame_pattern:
        settings.app_frame_pattern = app_frame_pattern
    set_app_frame_pattern(settings.app_frame_pattern)

    _set_settings(settings)
    init_env_info(
        app_env=settings.environment,
        git_commit=git_commit,
        environment_source=source,
    )

    if settings.environment == UNKNOWN_ENVIRONMENT:
        logger.warning(
            "oh-notifier could not determine the environment — set APP_ENV "
            "(or OH_NOTIFIER_ENV). Alerts will be labelled 'ENV UNKNOWN'."
        )
    if applied_files:
        logger.info("oh-notifier loaded env files: %s", ", ".join(applied_files))

    return TelegramNotifier.initialize(settings)


async def start() -> None:
    """Start the notifier background delivery worker."""
    notifier = TelegramNotifier.get_instance()
    if notifier:
        await notifier.start()


async def stop() -> None:
    """Stop the notifier and flush remaining errors."""
    notifier = TelegramNotifier.get_instance()
    if notifier:
        await notifier.stop()


def stats() -> dict[str, int | str]:
    """Delivery counters — safe to expose from a health endpoint."""
    notifier = TelegramNotifier.get_instance()
    return notifier.stats if notifier else {}


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
        environment=notifier.settings.environment,
        extras=merged_extras,
    )
    notifier.capture(event)
