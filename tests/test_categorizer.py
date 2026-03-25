"""Tests for auto-categorization."""

from oh_notifier.categorizer import categorize
from oh_notifier.event import (
    ErrorCategory,
    ErrorEvent,
    ErrorSeverity,
    ErrorSource,
)


def _make(error_type="Error", status_code=0, source=ErrorSource.HTTP, extras=None, tb=""):
    return ErrorEvent(
        service_name="test",
        error_type=error_type,
        error_message="test message",
        status_code=status_code,
        source=source,
        extras=extras or {},
        traceback_text=tb,
    )


def test_payment_by_type():
    e = categorize(_make(error_type="HamkorBankError"))
    assert e.category == ErrorCategory.PAYMENT


def test_payment_by_extras():
    e = categorize(_make(extras={"hamkor_method": "pay.create"}))
    assert e.category == ErrorCategory.PAYMENT


def test_auth_by_status_code():
    e = categorize(_make(status_code=401))
    assert e.category == ErrorCategory.AUTH
    assert e.severity == ErrorSeverity.WARNING


def test_database_by_type():
    e = categorize(_make(error_type="ProgrammingError"))
    assert e.category == ErrorCategory.DATABASE
    assert e.severity == ErrorSeverity.CRITICAL


def test_database_by_traceback():
    e = categorize(_make(tb="sqlalchemy.exc.OperationalError: connection refused"))
    assert e.category == ErrorCategory.DATABASE


def test_http_5xx():
    e = categorize(_make(status_code=500))
    assert e.category == ErrorCategory.HTTP_5XX
    assert e.severity == ErrorSeverity.CRITICAL


def test_http_4xx():
    e = categorize(_make(status_code=404))
    assert e.category == ErrorCategory.HTTP_4XX
    assert e.severity == ErrorSeverity.WARNING


def test_celery_task():
    e = categorize(_make(source=ErrorSource.CELERY))
    assert e.category == ErrorCategory.TASK


def test_sms_error():
    e = categorize(_make(error_type="EskizSMSError"))
    assert e.category == ErrorCategory.SMS


def test_notification_error():
    e = categorize(_make(error_type="FirebaseError"))
    assert e.category == ErrorCategory.NOTIFICATION


def test_validation():
    e = categorize(_make(error_type="ValidationError"))
    assert e.category == ErrorCategory.VALIDATION
    assert e.severity == ErrorSeverity.WARNING


def test_external_service():
    e = categorize(_make(error_type="ConnectError"))
    assert e.category == ErrorCategory.EXTERNAL


def test_unknown_fallback():
    e = categorize(_make(error_type="WeirdError"))
    assert e.category == ErrorCategory.UNKNOWN
    assert e.severity == ErrorSeverity.ERROR
