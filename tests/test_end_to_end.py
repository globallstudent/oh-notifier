"""End-to-end: capture on the caller's thread → delivered by the worker.

Unit tests cover the pieces; this proves they are actually wired together,
which is the part that silently broke before — the overflow flush reached for
``asyncio.get_event_loop()`` from a worker thread, raised, and was swallowed.
"""

from __future__ import annotations

import json
import threading

import httpx
import pytest

import oh_notifier
from oh_notifier.config import _set_settings
from oh_notifier.event import ErrorEvent
from oh_notifier.notifier import TelegramNotifier


class _Telegram:
    """Stand-in Bot API that records what it was sent."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.received = threading.Event()

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.messages.append(payload["text"])
        self.received.set()
        return httpx.Response(200, json={"ok": True})


@pytest.fixture
def telegram(monkeypatch):
    for var in ("OH_NOTIFIER_ENV", "APP_ENV", "ENVIRONMENT", "ENV"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APP_ENV", "production")

    fake = _Telegram()
    real_client = httpx.AsyncClient

    def _client_factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.MockTransport(fake.handler))

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory)
    yield fake

    TelegramNotifier._instance = None
    _set_settings(None)  # type: ignore[arg-type]


async def test_captured_error_reaches_telegram(telegram):
    notifier = oh_notifier.configure(
        bot_token="token",
        chat_id="-100123",
        service_name="core-service",
        flush_interval=0.05,
        rate_limit_interval=0.0,
        load_dotenv=False,
    )
    await oh_notifier.start()
    try:
        notifier.capture(
            ErrorEvent(
                service_name="core-service",
                error_type="ProgrammingError",
                error_message='relation "regions" does not exist',
                endpoint="/api/v1/specializations",
                method="GET",
                status_code=500,
            )
        )
        assert telegram.received.wait(timeout=5), "nothing delivered"
    finally:
        await oh_notifier.stop()

    body = telegram.messages[0]
    assert "core-service" in body
    assert "[PRODUCTION]" in body           # not [DEVELOPMENT]
    assert "ProgrammingError" in body
    assert "regions" in body
    assert "/api/v1/specializations" in body


async def test_capture_from_a_plain_thread_is_delivered(telegram):
    """Sync endpoints and library logging run off the event loop; that path
    used to drop the immediate flush entirely."""
    oh_notifier.configure(
        bot_token="token",
        chat_id="-100123",
        service_name="worker",
        flush_interval=0.05,
        rate_limit_interval=0.0,
        load_dotenv=False,
    )
    await oh_notifier.start()
    try:
        def background() -> None:
            oh_notifier.send_alert("failed in a thread", error_type="ThreadError")

        t = threading.Thread(target=background)
        t.start()
        t.join()

        assert telegram.received.wait(timeout=5), "thread capture never delivered"
    finally:
        await oh_notifier.stop()

    assert "ThreadError" in telegram.messages[0]


async def test_alerting_disabled_for_this_environment_sends_nothing(telegram):
    """One image, several environments: demo can stay quiet via env alone."""
    oh_notifier.configure(
        bot_token="token",
        chat_id="-100123",
        service_name="demo-service",
        flush_interval=0.05,
        rate_limit_interval=0.0,
        load_dotenv=False,
        environments_enabled=frozenset({"demo"}),  # but APP_ENV=production
    )
    await oh_notifier.start()
    try:
        oh_notifier.send_alert("should not be delivered")
        assert not telegram.received.wait(timeout=0.6)
    finally:
        await oh_notifier.stop()

    assert telegram.messages == []


async def test_burst_is_batched_into_few_messages(telegram):
    """Twenty distinct errors used to mean twenty API calls, each behind a
    serialized rate-limit sleep."""
    oh_notifier.configure(
        bot_token="token",
        chat_id="-100123",
        service_name="core-service",
        flush_interval=0.05,
        rate_limit_interval=0.0,
        load_dotenv=False,
    )
    notifier = TelegramNotifier.get_instance()
    assert notifier is not None
    await oh_notifier.start()
    try:
        for i in range(20):
            notifier.capture(
                ErrorEvent(
                    service_name="core-service",
                    error_type=f"Error{i}",
                    error_message=f"failure number {i}",
                )
            )
        assert telegram.received.wait(timeout=5)
    finally:
        await oh_notifier.stop()

    assert len(telegram.messages) < 20, "batching did not reduce API calls"
    combined = "".join(telegram.messages)
    for i in range(20):
        assert f"Error{i}" in combined, f"Error{i} was lost in batching"
