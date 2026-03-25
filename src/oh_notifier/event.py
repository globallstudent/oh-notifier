"""Error event dataclass and classification enums."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class ErrorSeverity(StrEnum):
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ErrorCategory(StrEnum):
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
    UNKNOWN = "unknown"


class ErrorSource(StrEnum):
    HTTP = "http"
    WEBSOCKET = "websocket"
    CELERY = "celery"
    RABBITMQ = "rabbitmq"
    TASK = "task"
    LOGGER = "logger"
    ASYNCIO = "asyncio"
    STARTUP = "startup"
    APSCHEDULER = "apscheduler"


_DEFAULT_APP_FRAME_RE = re.compile(r'File "(/app/[^"]+)", line (\d+), in (\w+)')

# Module-level compiled pattern (updated by config)
_app_frame_re: re.Pattern[str] = _DEFAULT_APP_FRAME_RE


def set_app_frame_pattern(pattern: str) -> None:
    """Override the regex used to extract app frames from tracebacks."""
    global _app_frame_re
    _app_frame_re = re.compile(pattern)


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
    extras: dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def fingerprint(self) -> str:
        """Dedup key: md5 of error type + last app frame location."""
        last_frame = ""
        for match in _app_frame_re.finditer(self.traceback_text):
            last_frame = f"{match.group(1)}:{match.group(2)}:{match.group(3)}"
        raw = f"{self.error_type}:{last_frame}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]
