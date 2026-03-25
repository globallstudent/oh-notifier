"""Configuration singleton for oh-notifier."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field


@dataclass
class OhNotifierSettings:
    """All configurable values for oh-notifier."""

    bot_token: str = ""
    chat_id: str = ""
    service_name: str = "unknown"
    environment: str = "development"
    enabled: bool = True
    timezone: str = "UTC"

    # Buffer / dedup
    dedup_window: float = 300.0
    max_buffer_size: int = 50
    flush_interval: float = 2.0
    rate_limit_interval: float = 1.0

    # Telegram
    max_message_len: int = 4096

    # Traceback frame matching (Docker default)
    app_frame_pattern: str = r'File "(/app/[^"]+)", line (\d+), in (\w+)'

    # Sensitive data masking
    sensitive_keys: frozenset[str] = field(default_factory=lambda: frozenset({
        "password", "token", "secret", "card_number", "number",
        "cvv", "cvc", "pin", "otp", "code", "confirm_code",
    }))

    # Loggers to skip (prevent recursion)
    skip_loggers: frozenset[str] = field(default_factory=lambda: frozenset({
        "oh_notifier", "httpx", "httpcore", "urllib3",
    }))

    # Minimum log level to capture (ERROR by default, set WARNING to catch more)
    min_log_level: int = logging.ERROR


_settings: OhNotifierSettings | None = None


def get_settings() -> OhNotifierSettings:
    """Get the current settings. Raises if configure() not called."""
    if _settings is None:
        raise RuntimeError(
            "oh-notifier not configured. Call oh_notifier.configure() first."
        )
    return _settings


def _set_settings(settings: OhNotifierSettings) -> None:
    """Set the global settings (called by configure())."""
    global _settings
    _settings = settings


def _get_settings_or_none() -> OhNotifierSettings | None:
    """Get settings without raising. For internal use in silent-fail paths."""
    return _settings
