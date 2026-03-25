"""Tests for ErrorBuffer dedup and drain."""

from oh_notifier.config import OhNotifierSettings, _set_settings
from oh_notifier.event import ErrorEvent
from oh_notifier.rate_limiter import ErrorBuffer


def setup_module():
    _set_settings(OhNotifierSettings(service_name="test"))


def test_add_and_drain():
    buf = ErrorBuffer(dedup_window=300, max_size=50)
    event = ErrorEvent(service_name="s", error_type="E", error_message="m")
    buf.add(event)
    items = buf.drain()
    assert len(items) == 1
    assert items[0][1] == 1  # count = 1


def test_dedup_same_fingerprint():
    buf = ErrorBuffer(dedup_window=300, max_size=50)
    tb = 'File "/app/x.py", line 1, in f\nError'
    e1 = ErrorEvent(service_name="s", error_type="E", error_message="m1", traceback_text=tb)
    e2 = ErrorEvent(service_name="s", error_type="E", error_message="m2", traceback_text=tb)
    buf.add(e1)
    buf.add(e2)
    items = buf.drain()
    assert len(items) == 1
    assert items[0][1] == 2  # count = 2


def test_different_fingerprints():
    buf = ErrorBuffer(dedup_window=300, max_size=50)
    e1 = ErrorEvent(
        service_name="s", error_type="ValueError", error_message="m",
        traceback_text='File "/app/a.py", line 1, in f\nE',
    )
    e2 = ErrorEvent(
        service_name="s", error_type="TypeError", error_message="m",
        traceback_text='File "/app/b.py", line 2, in g\nE',
    )
    buf.add(e1)
    buf.add(e2)
    items = buf.drain()
    assert len(items) == 2


def test_drain_clears_buffer():
    buf = ErrorBuffer(dedup_window=300, max_size=50)
    buf.add(ErrorEvent(service_name="s", error_type="E", error_message="m"))
    buf.drain()
    assert buf.is_empty()
    assert buf.drain() == []


def test_overflow_returns_true():
    buf = ErrorBuffer(dedup_window=300, max_size=3)
    for i in range(5):
        overflow = buf.add(ErrorEvent(
            service_name="s", error_type=f"E{i}", error_message="m",
            traceback_text=f'File "/app/{i}.py", line {i}, in f\nE',
        ))
    assert overflow is True
