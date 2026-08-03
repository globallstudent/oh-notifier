"""Tracebacks must keep the part that explains the failure.

From a real alert. A `MissingGreenlet` arrived with its top two thirds full of
starlette/anyio/sqlalchemy plumbing, while the sentence that says what to do —

    greenlet_spawn has not been called; can't call await_only() here

— was cut off the bottom by Telegram's 4096-character limit. The alert was
long, detailed, and told the reader nothing.

The rules these pin down: the tail is sacred, library frames are filler, and
an alert says what it dropped rather than trailing off.
"""

from __future__ import annotations

import pytest

from oh_notifier.config import OhNotifierSettings, _set_settings
from oh_notifier.event import ErrorEvent
from oh_notifier.formatter import format_error_html, format_error_messages
from oh_notifier.traceback_util import chunk, condense, exception_summary

LIB = "/venv/lib/python3.13/site-packages"
CAUSE = (
    "sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called; "
    "can't call await_only() here."
)


def _traceback(library_frames: int = 40, app_frames: int = 1) -> str:
    lines = ["Traceback (most recent call last):"]
    for i in range(library_frames // 2):
        lines += [
            f'  File "{LIB}/starlette/base_{i}.py", line {i}, in call_{i}',
            "    await self.app(scope, receive, send)",
        ]
    for i in range(app_frames):
        lines += [
            f'  File "/app/app/admin/views/locations.py", line {427 + i}, in view_{i}',
            "    result = await db.execute(select(Region))",
        ]
    for i in range(library_frames // 2):
        lines += [
            f'  File "{LIB}/sqlalchemy/engine/base_{i}.py", line {i}, in exec_{i}',
            "    return self._execute_context(dialect, context)",
        ]
    lines.append(CAUSE)
    return "\n".join(lines)


@pytest.fixture(autouse=True)
def settings():
    s = OhNotifierSettings(service_name="core-service", environment="production")
    _set_settings(s)
    return s


# -- condense ---------------------------------------------------------------


def test_short_traceback_is_untouched():
    text = "Traceback (most recent call last):\n  ...\nValueError: nope"
    assert condense(text, 4096) == text


def test_the_cause_survives_condensing():
    """The one thing that must never be dropped."""
    condensed = condense(_traceback(library_frames=200), 1500)
    assert CAUSE in condensed
    assert len(condensed) <= 1500


def test_application_frames_survive_condensing():
    """A library frame is rarely actionable; an app frame always is."""
    condensed = condense(_traceback(library_frames=200, app_frames=3), 1500)
    assert "locations.py" in condensed


def test_library_frames_are_dropped_first():
    full = _traceback(library_frames=200)
    condensed = condense(full, 1500)
    assert condensed.count("site-packages") < full.count("site-packages")
    assert "library frame(s) hidden" in condensed


def test_elision_is_declared_not_silent():
    """A quietly shortened traceback reads like a complete one."""
    condensed = condense(_traceback(library_frames=100), 1200)
    assert "hidden" in condensed or "omitted" in condensed


def test_never_exceeds_the_budget():
    for budget in (300, 800, 1500, 4000):
        assert len(condense(_traceback(library_frames=300), budget)) <= budget


def test_tail_wins_when_even_it_will_not_fit():
    """Losing the beginning beats losing the end."""
    condensed = condense(_traceback(library_frames=200), 200)
    assert len(condensed) <= 200
    assert "MissingGreenlet" in condensed


def test_traceback_of_only_app_frames_is_kept_whole():
    text = _traceback(library_frames=0, app_frames=2)
    assert condense(text, 4096) == text


# -- exception_summary ------------------------------------------------------


def test_exception_summary_returns_the_final_line():
    assert exception_summary(_traceback()) == CAUSE


def test_exception_summary_skips_frame_and_caret_lines():
    text = (
        'Traceback (most recent call last):\n'
        '  File "/app/x.py", line 1, in f\n'
        "    boom()\n"
        "    ^^^^^^\n"
        "RuntimeError: it broke"
    )
    assert exception_summary(text) == "RuntimeError: it broke"


def test_exception_summary_of_empty_is_empty():
    assert exception_summary("") == ""


# -- chunking ---------------------------------------------------------------


def test_chunk_splits_on_line_boundaries():
    text = "\n".join(f"line {i}" * 5 for i in range(50))
    pieces = chunk(text, 200)
    assert all(len(p) <= 200 for p in pieces)
    assert "".join(p.replace("\n", "") for p in pieces) == text.replace("\n", "")


def test_chunk_returns_single_piece_when_it_fits():
    assert chunk("short", 100) == ["short"]


# -- end to end -------------------------------------------------------------


def test_cause_is_lifted_to_the_top_of_the_message():
    """So it is readable even when the traceback below had to shrink."""
    event = ErrorEvent(
        service_name="core-service",
        error_type="MissingGreenlet",
        error_message="v2 unhandled exception",
        traceback_text=_traceback(library_frames=200),
        environment="production",
    )
    first = format_error_messages(event)[0]
    assert "Cause:" in first
    assert "greenlet_spawn has not been called" in first


def test_cause_is_omitted_when_it_repeats_the_message():
    event = ErrorEvent(
        service_name="s",
        error_type="ValueError",
        error_message="RuntimeError: it broke",
        traceback_text='  File "/app/x.py", line 1, in f\nRuntimeError: it broke',
    )
    assert "Cause:" not in format_error_messages(event)[0]


def test_every_message_stays_within_the_telegram_limit(settings):
    event = ErrorEvent(
        service_name="core-service",
        error_type="MissingGreenlet",
        error_message="boom",
        traceback_text=_traceback(library_frames=400, app_frames=5),
        environment="production",
    )
    for message in format_error_messages(event):
        assert len(message) <= settings.max_message_len


def test_pre_tags_stay_balanced_across_messages():
    """Unbalanced markup is a 400 from Telegram and a lost alert."""
    event = ErrorEvent(
        service_name="core-service",
        error_type="MissingGreenlet",
        error_message="boom",
        traceback_text=_traceback(library_frames=400),
        environment="production",
    )
    for message in format_error_messages(event):
        assert message.count("<pre>") == message.count("</pre>")


def test_no_traceback_produces_exactly_one_message():
    event = ErrorEvent(service_name="s", error_type="Alert", error_message="hi")
    assert len(format_error_messages(event)) == 1


def test_format_error_html_still_returns_a_single_string():
    """The old entry point stays usable — services and tests call it."""
    event = ErrorEvent(
        service_name="s",
        error_type="E",
        error_message="m",
        traceback_text=_traceback(),
    )
    assert isinstance(format_error_html(event), str)
