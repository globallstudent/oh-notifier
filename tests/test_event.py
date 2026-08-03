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


def test_fingerprint_no_traceback_separates_distinct_messages():
    """Traceback-less records must NOT all collapse into one alert.

    This asserted the opposite until the message was added to the key. With
    no frame to hash, every record of a given type produced the same
    fingerprint — so unrelated logger errors merged and the channel showed
    whichever arrived first with a count beside it, hiding the rest.
    """
    e1 = ErrorEvent(service_name="s", error_type="Error", error_message="disk full")
    e2 = ErrorEvent(service_name="s", error_type="Error", error_message="bad gateway")
    assert e1.fingerprint != e2.fingerprint


def test_fingerprint_no_traceback_groups_same_message():
    """The same failure with a different id is still one problem."""
    e1 = ErrorEvent(
        service_name="s", error_type="Error",
        error_message="order 8f2c1a3e-0000-4000-8000-000000000001 not found",
    )
    e2 = ErrorEvent(
        service_name="s", error_type="Error",
        error_message="order 41ab99cd-0000-4000-8000-000000000002 not found",
    )
    assert e1.fingerprint == e2.fingerprint


def test_fingerprint_separates_endpoints():
    """The same line failing on two routes is two different problems."""
    tb = 'File "/app/a.py", line 1, in f\nError'
    e1 = ErrorEvent(
        service_name="s", error_type="Error", error_message="m",
        traceback_text=tb, endpoint="/orders",
    )
    e2 = ErrorEvent(
        service_name="s", error_type="Error", error_message="m",
        traceback_text=tb, endpoint="/nurses",
    )
    assert e1.fingerprint != e2.fingerprint


def test_enums_are_strings():
    assert str(ErrorSeverity.CRITICAL) == "critical"
    assert str(ErrorCategory.PAYMENT) == "payment"
    assert str(ErrorSource.CELERY) == "celery"
