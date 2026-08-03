"""Error event dataclass and classification enums."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class _StrEnum(str, Enum):
    """``str`` + ``Enum`` — ``enum.StrEnum`` is 3.11+ and we support 3.10."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


class ErrorSeverity(_StrEnum):
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ErrorCategory(_StrEnum):
    HTTP_4XX = "http_4xx"
    HTTP_5XX = "http_5xx"
    DATABASE = "database"
    PAYMENT = "payment"
    SMS = "sms"
    AUTH = "authentication"
    TASK = "task"
    NOTIFICATION = "notification"
    EXTERNAL = "external"
    VALIDATION = "validation"
    CONFIG = "configuration"
    UNKNOWN = "unknown"


class ErrorSource(_StrEnum):
    HTTP = "http"
    WEBSOCKET = "websocket"
    CELERY = "celery"
    RABBITMQ = "rabbitmq"
    TASK = "task"
    LOGGER = "logger"
    ASYNCIO = "asyncio"
    STARTUP = "startup"
    APSCHEDULER = "apscheduler"
    THREAD = "thread"
    EXCEPTHOOK = "excepthook"


_DEFAULT_APP_FRAME_RE = re.compile(r'File "(/app/[^"]+)", line (\d+), in (\w+)')

# Module-level compiled pattern (updated by config)
_app_frame_re: re.Pattern[str] = _DEFAULT_APP_FRAME_RE

#: Variable parts of a message, replaced before fingerprinting so that
#: "order 8f2c… not found" and "order 41ab… not found" group together
#: instead of filling the channel with one alert per id.
_NOISE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<uuid>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b\d[\d_.,:-]*\b"), "<n>"),
    (re.compile(r"'[^']*'"), "'<v>'"),
    (re.compile(r'"[^"]*"'), '"<v>"'),
    (re.compile(r"\s+"), " "),
)


def set_app_frame_pattern(pattern: str) -> None:
    """Override the regex used to extract app frames from tracebacks."""
    global _app_frame_re
    _app_frame_re = re.compile(pattern)


def normalize_message(message: str, limit: int = 200) -> str:
    """Strip the variable parts of a message for fingerprinting."""
    text = message[:limit]
    for pattern, replacement in _NOISE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text.strip().lower()


@dataclass
class ErrorEvent:
    """Represents a single error occurrence."""

    service_name: str
    error_type: str
    error_message: str
    traceback_text: str = ""
    endpoint: str = ""
    method: str = ""
    status_code: int = 0
    source: ErrorSource | str = ErrorSource.HTTP
    severity: ErrorSeverity = ErrorSeverity.ERROR
    category: ErrorCategory = ErrorCategory.UNKNOWN
    environment: str = ""
    extras: dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def fingerprint(self) -> str:
        """Dedup key.

        Built from the error type, the last application frame, the endpoint,
        and — crucially — a normalized message whenever there is no traceback.

        Without that last part every traceback-less record of a given type
        hashed identically: a logger error fingerprinted as
        ``md5("LogError:")`` no matter what it said, so unrelated failures
        merged and the channel showed whichever arrived first with a count
        beside it. The endpoint is included for the same reason — the same
        line failing on two routes is two different problems.
        """
        last_frame = ""
        for match in _app_frame_re.finditer(self.traceback_text):
            last_frame = f"{match.group(1)}:{match.group(2)}:{match.group(3)}"

        parts = [self.error_type, last_frame, self.endpoint or "", str(self.status_code or "")]
        if not last_frame:
            parts.append(normalize_message(self.error_message))

        raw = "\x1f".join(parts)
        return hashlib.md5(raw.encode("utf-8", errors="replace")).hexdigest()[:16]
