"""Request-context isolation and message rendering."""

from __future__ import annotations

import asyncio

import pytest

from oh_notifier.config import OhNotifierSettings, _set_settings
from oh_notifier.context import (
    get_request_context,
    reset_request_context,
    set_request_context,
)
from oh_notifier.env import UNKNOWN_ENVIRONMENT
from oh_notifier.event import ErrorEvent, ErrorSeverity
from oh_notifier.formatter import format_error_html


@pytest.fixture(autouse=True)
def settings():
    s = OhNotifierSettings(service_name="test", environment="production")
    _set_settings(s)
    reset_request_context()
    return s


# -- context isolation ------------------------------------------------------


async def test_child_task_cannot_mutate_the_parent_context():
    """The leak this fixes: one request's user_id appearing on another's alert.

    The old code called ``ctx.update()`` on whatever the ContextVar returned.
    Child tasks inherit the *same dict object*, so a child's write landed in
    the parent's context — and from there onto an unrelated alert.
    """
    set_request_context(user_id="parent")

    async def child() -> None:
        set_request_context(user_id="child", order_id="o-1")

    await asyncio.create_task(child())

    assert get_request_context()["user_id"] == "parent"
    assert "order_id" not in get_request_context()


async def test_sibling_tasks_are_isolated():
    seen: dict[str, str] = {}

    async def request(name: str) -> None:
        reset_request_context()
        set_request_context(user_id=name)
        await asyncio.sleep(0)  # force interleaving
        seen[name] = get_request_context()["user_id"]

    await asyncio.gather(*(request(f"u{i}") for i in range(5)))

    assert seen == {f"u{i}": f"u{i}" for i in range(5)}


def test_reset_clears_previous_request():
    set_request_context(user_id="first")
    reset_request_context()
    assert "user_id" not in get_request_context()


def test_none_values_are_skipped():
    set_request_context(user_id="u", phone=None)
    ctx = get_request_context()
    assert ctx["user_id"] == "u"
    assert "phone" not in ctx


# -- formatting -------------------------------------------------------------


def _event(**kwargs) -> ErrorEvent:
    base = dict(
        service_name="core-service",
        error_type="ValueError",
        error_message="something failed",
    )
    base.update(kwargs)
    return ErrorEvent(**base)


def test_unknown_environment_says_so(settings):
    """Never invent an environment name — that is how production spent months
    reporting itself as [DEVELOPMENT]."""
    settings.environment = UNKNOWN_ENVIRONMENT
    out = format_error_html(_event(environment=UNKNOWN_ENVIRONMENT))
    assert "ENV UNKNOWN" in out
    assert "DEVELOPMENT" not in out


def test_defaulted_environment_is_marked():
    out = format_error_html(_event(environment="development", extras={
        "env_source": "argument-default",
    }))
    assert "[DEVELOPMENT]" in out
    assert "defaulted" in out


def test_environment_is_shown_uppercase():
    assert "[PRODUCTION]" in format_error_html(_event(environment="production"))


def test_html_is_escaped():
    out = format_error_html(_event(error_message="<script>alert(1)</script>"))
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_long_traceback_stays_within_the_telegram_limit(settings):
    event = _event(traceback_text="x" * 50_000)
    out = format_error_html(event)
    assert len(out) <= settings.max_message_len


def test_truncation_leaves_balanced_pre_tags(settings):
    """A cut that lands mid-tag makes Telegram reject the whole message."""
    settings.max_message_len = 900
    out = format_error_html(_event(traceback_text="y" * 20_000))
    assert out.count("<pre>") == out.count("</pre>"), out[-200:]


def test_truncation_does_not_split_an_html_entity(settings):
    """Escaping before truncating could slice '&amp;' into '&am' — a 400."""
    settings.max_message_len = 1200
    out = format_error_html(_event(traceback_text="& < > " * 3000))
    tail = out[-80:]
    assert "&am" not in tail.replace("&amp;", "")
    assert "&l" not in tail.replace("&lt;", "").replace("&amp;", "")


def test_counts_are_rendered():
    assert " x7" in format_error_html(_event(), count=7)


def test_severity_icon_reflects_severity():
    critical = format_error_html(_event(severity=ErrorSeverity.CRITICAL))
    info = format_error_html(_event(severity=ErrorSeverity.INFO))
    assert "\U0001f534" in critical.splitlines()[0]
    assert "\U0001f535" in info.splitlines()[0]


def test_endpoint_shows_status_code():
    out = format_error_html(
        _event(endpoint="/api/v1/orders", method="POST", status_code=500)
    )
    assert "POST /api/v1/orders" in out
    assert "500" in out
