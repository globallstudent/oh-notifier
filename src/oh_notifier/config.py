"""Configuration for oh-notifier.

Every field can be supplied three ways, in decreasing precedence: the
``configure()`` argument, an ``OH_NOTIFIER_*`` environment variable (with an
optional ``_<ENVIRONMENT>`` suffix for per-environment overrides), and the
default here. Environment variables win over defaults but never over an
explicit argument, so a service that hard-codes a value keeps it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from oh_notifier.env import (
    UNKNOWN_ENVIRONMENT,
    env_bool,
    env_float,
    env_int,
    env_str,
)

_DEFAULT_SENSITIVE_KEYS = frozenset({
    "password", "token", "secret", "card_number", "number",
    "cvv", "cvc", "pin", "otp", "code", "confirm_code",
    "authorization", "api_key", "apikey", "private_key",
    "session", "cookie", "refresh_token", "access_token",
})

_DEFAULT_SKIP_LOGGERS = frozenset({
    "oh_notifier", "httpx", "httpcore", "urllib3",
})


@dataclass
class OhNotifierSettings:
    """All configurable values for oh-notifier."""

    bot_token: str = ""
    chat_id: str = ""
    service_name: str = "unknown"
    environment: str = UNKNOWN_ENVIRONMENT
    enabled: bool = True
    timezone: str = "UTC"

    #: Where ``environment`` came from — "argument", an env var name, or
    #: "unset". Recorded so startup can warn when nothing set it, which is
    #: how every production alert came to be labelled [DEVELOPMENT].
    environment_source: str = "unset"

    #: When non-empty, only these environments alert. Lets a demo or staging
    #: deployment run the same image with alerting off, without a code change.
    environments_enabled: frozenset[str] = field(default_factory=frozenset)

    # Buffer / dedup
    dedup_window: float = 300.0
    max_buffer_size: int = 50
    flush_interval: float = 2.0
    rate_limit_interval: float = 1.0

    #: Hard ceiling on distinct buffered errors. Past this, new fingerprints
    #: are dropped and counted; the count rides along on the next message so
    #: a storm is never silently truncated.
    max_pending_events: int = 500

    # Telegram
    max_message_len: int = 4096
    send_timeout: float = 10.0
    max_send_attempts: int = 3

    #: Pack several errors into one Telegram message when they fit. One API
    #: call per burst instead of one per error, each of which previously cost
    #: a serialized ``rate_limit_interval`` sleep.
    batch_messages: bool = True

    # Traceback frame matching (Docker default)
    app_frame_pattern: str = r'File "(/app/[^"]+)", line (\d+), in (\w+)'

    # Sensitive data masking
    sensitive_keys: frozenset[str] = field(
        default_factory=lambda: _DEFAULT_SENSITIVE_KEYS
    )

    # Loggers to skip (prevent recursion)
    skip_loggers: frozenset[str] = field(
        default_factory=lambda: _DEFAULT_SKIP_LOGGERS
    )

    # Minimum log level to capture (ERROR by default, set WARNING to catch more)
    min_log_level: int = logging.ERROR

    #: Report 5xx responses even when nothing propagated an exception —
    #: an error handler that returns a 500 is still a 500.
    capture_http_5xx: bool = True
    #: 4xx is normally client noise; opt in per service.
    capture_http_4xx: bool = False

    #: Cap on request-body bytes retained for an alert, so a large upload
    #: cannot be buffered in full just in case it errors.
    max_body_bytes: int = 16384

    def alerting_allowed(self) -> bool:
        """Whether this environment should emit at all."""
        if not self.enabled:
            return False
        if self.environments_enabled and self.environment not in self.environments_enabled:
            return False
        return True

    def apply_env_overrides(self) -> None:
        """Fill unset fields from ``OH_NOTIFIER_*``.

        Called after the explicit arguments are in place; each lookup passes
        the current value as the default, so an argument always wins.
        """
        env = self.environment

        self.bot_token = env_str("BOT_TOKEN", self.bot_token, environment=env)
        self.chat_id = env_str("CHAT_ID", self.chat_id, environment=env)
        self.service_name = env_str("SERVICE_NAME", self.service_name, environment=env)
        self.timezone = env_str("TIMEZONE", self.timezone, environment=env)
        self.enabled = env_bool("ENABLED", self.enabled, environment=env)

        allowlist = env_str("ENVIRONMENTS", "", environment=env)
        if allowlist:
            self.environments_enabled = frozenset(
                part.strip().lower() for part in allowlist.split(",") if part.strip()
            )

        self.dedup_window = env_float("DEDUP_WINDOW", self.dedup_window, environment=env)
        self.max_buffer_size = env_int(
            "MAX_BUFFER_SIZE", self.max_buffer_size, environment=env
        )
        self.flush_interval = env_float(
            "FLUSH_INTERVAL", self.flush_interval, environment=env
        )
        self.rate_limit_interval = env_float(
            "RATE_LIMIT_INTERVAL", self.rate_limit_interval, environment=env
        )
        self.max_pending_events = env_int(
            "MAX_PENDING_EVENTS", self.max_pending_events, environment=env
        )
        self.send_timeout = env_float("SEND_TIMEOUT", self.send_timeout, environment=env)
        self.max_send_attempts = env_int(
            "MAX_SEND_ATTEMPTS", self.max_send_attempts, environment=env
        )
        self.batch_messages = env_bool(
            "BATCH_MESSAGES", self.batch_messages, environment=env
        )
        self.capture_http_5xx = env_bool(
            "CAPTURE_HTTP_5XX", self.capture_http_5xx, environment=env
        )
        self.capture_http_4xx = env_bool(
            "CAPTURE_HTTP_4XX", self.capture_http_4xx, environment=env
        )
        self.max_body_bytes = env_int(
            "MAX_BODY_BYTES", self.max_body_bytes, environment=env
        )

        level = env_str("MIN_LOG_LEVEL", "", environment=env)
        if level:
            self.min_log_level = _parse_level(level, self.min_log_level)

        extra_sensitive = env_str("SENSITIVE_KEYS", "", environment=env)
        if extra_sensitive:
            self.sensitive_keys = self.sensitive_keys | frozenset(
                part.strip().lower() for part in extra_sensitive.split(",") if part.strip()
            )


#: Spelled out rather than via ``logging.getLevelNamesMapping()``, which is
#: 3.11+ while this package supports 3.10.
_LEVEL_NAMES = {
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


def _parse_level(value: str, default: int) -> int:
    """Accept either a level name ("WARNING") or a number ("30")."""
    cleaned = value.strip().upper()
    if cleaned in _LEVEL_NAMES:
        return _LEVEL_NAMES[cleaned]
    try:
        return int(cleaned)
    except ValueError:
        return default


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
