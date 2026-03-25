"""Tests for HTML message formatting."""

from oh_notifier.config import OhNotifierSettings, _set_settings
from oh_notifier.event import ErrorEvent, ErrorSeverity
from oh_notifier.formatter import format_error_html


def setup_module():
    _set_settings(OhNotifierSettings(service_name="test-svc", timezone="UTC"))


def test_basic_format():
    event = ErrorEvent(
        service_name="test-svc",
        error_type="ValueError",
        error_message="bad value",
    )
    html = format_error_html(event)
    assert "test-svc" in html
    assert "ValueError" in html
    assert "bad value" in html


def test_format_with_count():
    event = ErrorEvent(
        service_name="svc",
        error_type="Error",
        error_message="msg",
    )
    html = format_error_html(event, count=5)
    assert "x5" in html


def test_format_with_extras():
    event = ErrorEvent(
        service_name="svc",
        error_type="Error",
        error_message="msg",
        extras={"user_id": "abc-123", "phone": "+998901234567"},
    )
    html = format_error_html(event)
    assert "abc-123" in html
    assert "+998901234567" in html


def test_format_with_traceback():
    tb = 'Traceback:\n  File "/app/main.py", line 10, in handler\nValueError: bad'
    event = ErrorEvent(
        service_name="svc",
        error_type="ValueError",
        error_message="bad",
        traceback_text=tb,
    )
    html = format_error_html(event)
    assert "<pre>" in html
    assert "main.py" in html


def test_format_max_length():
    event = ErrorEvent(
        service_name="svc",
        error_type="Error",
        error_message="x" * 5000,
        traceback_text="y" * 5000,
    )
    html = format_error_html(event)
    assert len(html) <= 4096


def test_severity_icon():
    event = ErrorEvent(
        service_name="svc",
        error_type="Error",
        error_message="msg",
        severity=ErrorSeverity.CRITICAL,
    )
    html = format_error_html(event)
    assert "\U0001f534" in html  # red circle
