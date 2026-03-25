"""Tests for ErrorEvent and enums."""

from oh_notifier.event import (
    ErrorCategory,
    ErrorEvent,
    ErrorSeverity,
    ErrorSource,
)


def test_error_event_creation():
    event = ErrorEvent(
        service_name="test",
        error_type="ValueError",
        error_message="bad value",
    )
    assert event.service_name == "test"
    assert event.severity == ErrorSeverity.ERROR
    assert event.category == ErrorCategory.UNKNOWN
    assert event.source == ErrorSource.HTTP


def test_fingerprint_same_error():
    tb = 'File "/app/services/payment.py", line 42, in process\nValueError: bad'
    e1 = ErrorEvent(service_name="s", error_type="ValueError", error_message="m1", traceback_text=tb)
    e2 = ErrorEvent(service_name="s", error_type="ValueError", error_message="m2", traceback_text=tb)
    assert e1.fingerprint == e2.fingerprint


def test_fingerprint_different_location():
    tb1 = 'File "/app/a.py", line 1, in func_a\nError'
    tb2 = 'File "/app/b.py", line 2, in func_b\nError'
    e1 = ErrorEvent(service_name="s", error_type="Error", error_message="m", traceback_text=tb1)
    e2 = ErrorEvent(service_name="s", error_type="Error", error_message="m", traceback_text=tb2)
    assert e1.fingerprint != e2.fingerprint


def test_fingerprint_no_traceback():
    e1 = ErrorEvent(service_name="s", error_type="Error", error_message="m1")
    e2 = ErrorEvent(service_name="s", error_type="Error", error_message="m2")
    assert e1.fingerprint == e2.fingerprint  # same type, no frame = same fp


def test_enums_are_strings():
    assert str(ErrorSeverity.CRITICAL) == "critical"
    assert str(ErrorCategory.PAYMENT) == "payment"
    assert str(ErrorSource.CELERY) == "celery"
