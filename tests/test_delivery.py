"""Delivery worker, sender resilience, and the non-blocking guarantee.

The theme: capturing an error must never cost the caller anything, and a
message Telegram refuses must not vanish.
"""

from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest

from oh_notifier.config import OhNotifierSettings, _set_settings
from oh_notifier.dispatcher import DeliveryWorker
from oh_notifier.event import ErrorEvent
from oh_notifier.notifier import TelegramNotifier, _pack
from oh_notifier.sender import TelegramSender, to_plain_text


@pytest.fixture(autouse=True)
def settings():
    s = OhNotifierSettings(
        service_name="test", environment="test",
        bot_token="t", chat_id="c", flush_interval=0.05,
    )
    _set_settings(s)
    return s


# -- worker -----------------------------------------------------------------


def test_worker_runs_flush_off_the_calling_thread():
    seen: list[str] = []
    done = threading.Event()

    async def flush() -> None:
        seen.append(threading.current_thread().name)
        done.set()

    worker = DeliveryWorker(flush=flush, interval=0.05)
    worker.start()
    try:
        worker.wake()
        assert done.wait(timeout=5), "flush never ran"
    finally:
        worker.stop(timeout=5)

    assert seen, "flush never ran"
    assert all(name != threading.current_thread().name for name in seen)
    assert seen[0] == "oh-notifier-delivery"


def test_worker_drains_on_stop():
    """Buffered alerts must survive shutdown — that is when the interesting
    failures happen."""
    calls = []

    async def flush() -> None:
        calls.append(1)

    worker = DeliveryWorker(flush=flush, interval=60.0)  # never ticks
    worker.start()
    worker.stop(timeout=5)
    assert calls, "no final drain"


def test_worker_survives_a_failing_flush():
    attempts = []
    done = threading.Event()

    async def flush() -> None:
        attempts.append(1)
        if len(attempts) >= 2:
            done.set()
        raise RuntimeError("boom")

    worker = DeliveryWorker(flush=flush, interval=0.05)
    worker.start()
    try:
        assert done.wait(timeout=5)
    finally:
        worker.stop(timeout=5)
    assert len(attempts) >= 2, "worker died on the first failure"


def test_wake_before_start_is_a_noop():
    async def flush() -> None:
        pass

    DeliveryWorker(flush=flush).wake()  # must not raise


def test_stop_without_start_is_a_noop():
    async def flush() -> None:
        pass

    DeliveryWorker(flush=flush).stop()  # must not raise


# -- capture is non-blocking ------------------------------------------------


def test_capture_does_not_block_the_caller():
    """capture() runs on request threads; it must do no I/O and no waiting."""
    notifier = TelegramNotifier(_settings_with_slow_send())
    notifier._worker.start()
    try:
        start = time.perf_counter()
        for i in range(200):
            notifier.capture(
                ErrorEvent(service_name="s", error_type=f"E{i}", error_message="m")
            )
        elapsed = time.perf_counter() - start
    finally:
        notifier._worker.stop(timeout=1)

    # 200 captures are dict updates; anything near the 1s+ of a network call
    # means work leaked onto the caller.
    assert elapsed < 0.5, f"capture blocked the caller for {elapsed:.3f}s"


def test_capture_from_many_threads_is_safe():
    notifier = TelegramNotifier(_settings_with_slow_send())
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(50):
                notifier.capture(
                    ErrorEvent(
                        service_name="s", error_type=f"E{n}-{i}", error_message="m"
                    )
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    items, _ = notifier._buffer.drain()
    assert len(items) == 8 * 50


def _settings_with_slow_send() -> OhNotifierSettings:
    return OhNotifierSettings(
        service_name="test", environment="test",
        bot_token="t", chat_id="c",
        flush_interval=60.0, rate_limit_interval=1.0,
    )


# -- buffer ceiling ---------------------------------------------------------


def test_drop_is_counted_not_silent():
    """A truncated storm must not read as calm."""
    notifier = TelegramNotifier(
        OhNotifierSettings(
            service_name="t", environment="test",
            max_pending_events=3, max_buffer_size=1000,
        )
    )
    for i in range(10):
        notifier.capture(
            ErrorEvent(service_name="s", error_type=f"E{i}", error_message="m")
        )

    items, dropped = notifier._buffer.drain()
    assert len(items) == 3
    assert dropped == 7


# -- sender -----------------------------------------------------------------


def _sender(handler, **kwargs) -> TelegramSender:
    s = TelegramSender(bot_token="t", chat_id="c", rate_limit=0.0, **kwargs)
    s._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return s


async def test_send_success():
    s = _sender(lambda r: httpx.Response(200, json={"ok": True}))
    assert await s.send("<b>hi</b>") is True
    assert s.stats.sent == 1


async def test_malformed_html_retries_as_plain_text():
    """Telegram answers 400 for markup we generated; a plain alert beats a
    lost one."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        payload = _json.loads(request.content)
        seen.append(payload)
        if payload.get("parse_mode") == "HTML":
            return httpx.Response(400, json={"description": "can't parse entities"})
        return httpx.Response(200, json={"ok": True})

    s = _sender(handler)
    assert await s.send("<pre>broken &am") is True
    assert len(seen) == 2
    assert "parse_mode" not in seen[1]
    assert "<pre>" not in seen[1]["text"]
    assert s.stats.html_rejected == 1


async def test_transport_error_retries_then_reports():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.ConnectError("no route to host")

    s = _sender(handler, max_attempts=3)
    s._backoff.base = 0.0
    assert await s.send("hi") is False
    assert attempts["n"] == 3
    assert s.stats.failed >= 1
    assert "ConnectError" in s.stats.last_error


async def test_429_honours_retry_after(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(d: float) -> None:
        slept.append(d)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429, json={"parameters": {"retry_after": 7}}
            )
        return httpx.Response(200, json={"ok": True})

    s = _sender(handler)
    assert await s.send("hi") is True
    assert 7 in slept


async def test_auth_failure_is_not_retried():
    """A bad token cannot be fixed by trying again."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"description": "unauthorized"})

    s = _sender(handler, max_attempts=3)
    assert await s.send("hi") is False
    assert calls["n"] == 1


async def test_unconfigured_sender_reports_failure_not_crash():
    s = TelegramSender(bot_token="", chat_id="", rate_limit=0.0)
    await s.start()
    assert await s.send("hi") is False
    await s.stop()


def test_to_plain_text_strips_markup():
    assert to_plain_text("<b>a</b>\n<pre>x &amp; y</pre>") == "a\nx & y"


# -- batching ---------------------------------------------------------------


def test_pack_combines_messages_that_fit():
    packed = _pack(["a" * 10, "b" * 10, "c" * 10], limit=100)
    assert len(packed) == 1


def test_pack_splits_when_over_limit():
    packed = _pack(["a" * 60, "b" * 60], limit=100)
    assert len(packed) == 2


def test_pack_never_drops_a_message():
    messages = [f"m{i}" * 20 for i in range(25)]
    packed = _pack(messages, limit=200)
    joined = "".join(packed)
    for message in messages:
        assert message in joined


def test_pack_leaves_single_message_alone():
    assert _pack(["only"], limit=10) == ["only"]
