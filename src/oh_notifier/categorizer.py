"""Auto-classify ErrorEvent severity and category."""

from __future__ import annotations

from oh_notifier.event import ErrorCategory, ErrorEvent, ErrorSeverity, ErrorSource


def categorize(event: ErrorEvent) -> ErrorEvent:
    """Set event.severity and event.category based on heuristics. Returns the event."""
    event.category = _detect_category(event)
    event.severity = _detect_severity(event)
    return event


def _detect_category(event: ErrorEvent) -> ErrorCategory:
    et = event.error_type.lower()
    extras = event.extras
    tb = event.traceback_text.lower()

    # Payment provider errors
    if any(k in et for k in ("payment", "hamkor", "card", "transaction", "payme", "click")):
        return ErrorCategory.PAYMENT
    if extras.get("hamkor_method"):
        return ErrorCategory.PAYMENT

    # SMS errors
    if any(k in et for k in ("sms", "eskiz", "playmobile")):
        return ErrorCategory.SMS

    # Auth errors
    if any(k in et for k in ("auth", "jwt", "token", "credential", "permission")):
        return ErrorCategory.AUTH
    if event.status_code in (401, 403):
        return ErrorCategory.AUTH

    # Database errors
    if any(k in et for k in (
        "operationalerror", "integrityerror", "programmingerror",
        "databaseerror", "asyncpg", "sqlalchemy",
    )):
        return ErrorCategory.DATABASE
    if "sqlalchemy" in tb or "asyncpg" in tb:
        return ErrorCategory.DATABASE

    # Validation errors
    if any(k in et for k in ("validation", "pydantic", "valueerror")):
        return ErrorCategory.VALIDATION

    # Task/scheduler errors
    if event.source in (ErrorSource.CELERY, ErrorSource.TASK, ErrorSource.APSCHEDULER):
        return ErrorCategory.TASK

    # Notification errors
    if any(k in et for k in ("firebase", "fcm", "notification", "push")):
        return ErrorCategory.NOTIFICATION

    # External service errors
    if any(k in et for k in (
        "connecterror", "readtimeout", "timeoutexception",
        "connectionerror", "httperror", "httpstatuserror",
    )):
        return ErrorCategory.EXTERNAL

    # HTTP status code based
    if event.status_code:
        if 400 <= event.status_code < 500:
            return ErrorCategory.HTTP_4XX
        if event.status_code >= 500:
            return ErrorCategory.HTTP_5XX

    return ErrorCategory.UNKNOWN


def _detect_severity(event: ErrorEvent) -> ErrorSeverity:
    # Info is only set explicitly via send_info()
    if event.severity == ErrorSeverity.INFO:
        return ErrorSeverity.INFO

    # Critical: database, unhandled 500s, startup failures
    if event.category == ErrorCategory.DATABASE:
        return ErrorSeverity.CRITICAL
    if event.source == ErrorSource.STARTUP:
        return ErrorSeverity.CRITICAL
    if event.status_code >= 500:
        return ErrorSeverity.CRITICAL

    # Warning: client errors, auth failures
    if event.category == ErrorCategory.HTTP_4XX:
        return ErrorSeverity.WARNING
    if event.category == ErrorCategory.AUTH and event.status_code in (401, 403):
        return ErrorSeverity.WARNING
    if event.category == ErrorCategory.VALIDATION:
        return ErrorSeverity.WARNING

    return ErrorSeverity.ERROR
