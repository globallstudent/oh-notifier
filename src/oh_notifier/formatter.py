"""Format ErrorEvent as Telegram HTML message."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from oh_notifier.config import _get_settings_or_none
from oh_notifier.env import UNKNOWN_ENVIRONMENT
from oh_notifier.event import ErrorEvent, ErrorSeverity
from oh_notifier.traceback_util import chunk as chunk_text
from oh_notifier.traceback_util import condense as condense_traceback
from oh_notifier.traceback_util import exception_summary

if TYPE_CHECKING:
    from oh_notifier.config import OhNotifierSettings

_SEVERITY_ICONS = {
    ErrorSeverity.CRITICAL: "\U0001f534",  # red circle
    ErrorSeverity.ERROR: "\U0001f7e0",     # orange circle
    ErrorSeverity.WARNING: "\U0001f7e1",   # yellow circle
    ErrorSeverity.INFO: "\U0001f535",      # blue circle
}

_SEP = "\n" + "─" * 20

# Fields already handled in specific sections — skip from "Other" section
_HANDLED_FIELDS = frozenset({
    "env", "env_source", "hostname", "request_id", "user_id", "phone", "role",
    "admin_role", "client_ip", "user_agent", "request_body",
    "response_body", "hamkor_method", "hamkor_request_id",
    "hamkor_status", "hamkor_response", "hamkor_error",
    "logger", "order_id", "card_number_last4", "endpoint",
    "method", "status_code", "error_code", "elapsed_ms", "attempt",
    "os", "python", "arch", "ip", "pod", "namespace", "node",
    "container_id", "git_commit", "version",
})


_TRUNC_MARKER = "\n... truncated ...\n"


def _smart_truncate(text: str, max_len: int) -> str:
    """Truncate keeping 30% head + 70% tail (the tail is where the raise is)."""
    if len(text) <= max_len:
        return text
    head_len = int(max_len * 0.3)
    tail_len = max_len - head_len - len(_TRUNC_MARKER)
    if tail_len <= 0:
        return text[:max_len]
    return text[:head_len] + _TRUNC_MARKER + text[-tail_len:]


def _esc(val: Any, max_len: int = 200) -> str:
    """Truncate first, then escape.

    Order matters: escaping first and cutting after can slice an entity in
    half (``&amp;`` → ``&am``), which Telegram answers with a 400 and the
    whole alert is lost.
    """
    return html.escape(str(val)[:max_len])


def _env_label(env: str | None, env_source: str | None) -> str:
    """Render the environment tag, flagging the case where nothing set it.

    Every production alert this project sent read ``[DEVELOPMENT]`` because
    ``APP_ENV`` was unset and the default filled it in. An unresolved
    environment now says so rather than naming one at random.
    """
    if not env or env == UNKNOWN_ENVIRONMENT:
        return "[ENV UNKNOWN — set APP_ENV]"
    label = f"[{html.escape(env.upper())}]"
    if env_source == "argument-default":
        label += " <i>(defaulted)</i>"
    return label


def format_notice(settings: OhNotifierSettings, lines: list[str]) -> str:
    """A standalone message for notifier-level problems (drops, send failures)."""
    parts = [
        f"<b>⚠️ oh-notifier — {_esc(settings.service_name)}</b>",
        _env_label(settings.environment, settings.environment_source),
        _SEP,
    ]
    parts.extend(f"• {_esc(line, 400)}" for line in lines)
    return "\n".join(parts)


def format_error_html(event: ErrorEvent, count: int = 1) -> str:
    """Format error event as Telegram HTML message with grouped sections."""
    settings = _get_settings_or_none()
    max_msg_len = settings.max_message_len if settings else 4096

    severity_icon = _SEVERITY_ICONS.get(event.severity, "\U0001f7e0")
    count_str = f" x{count}" if count > 1 else ""
    e = event.extras

    parts: list[str] = []

    # -- Header --
    parts.append(f"<b>{severity_icon} {_esc(event.service_name)}{count_str}</b>")
    env = event.environment or e.get("env")
    hostname = e.get("hostname")
    env_str = _env_label(env, e.get("env_source"))
    host_str = _esc(hostname) if hostname else ""
    parts.append(f"{env_str} {host_str}".strip())

    # -- Device info (compact) --
    device_parts: list[str] = []
    if e.get("pod"):
        device_parts.append(f"pod:{_esc(e['pod'])}")
    if e.get("node"):
        device_parts.append(f"node:{_esc(e['node'])}")
    if e.get("ip"):
        device_parts.append(f"ip:{_esc(e['ip'])}")
    if e.get("version"):
        device_parts.append(f"ver:{_esc(e['version'])}")
    if e.get("os"):
        device_parts.append(_esc(e["os"]))
    if e.get("container_id"):
        device_parts.append(f"ctr:{_esc(e['container_id'])}")
    if device_parts:
        parts.append(f"<i>{' | '.join(device_parts)}</i>")

    # -- Error Section --
    parts.append(_SEP)
    parts.append(f"<b>Error:</b> <code>{_esc(event.error_type)}</code>")
    parts.append(f"<b>Message:</b> {_esc(event.error_message, 500)}")

    # The exception line from the BOTTOM of the traceback, lifted to the top.
    # For wrapped failures the useful sentence lives at the very end — a
    # MissingGreenlet alert once arrived with "greenlet_spawn has not been
    # called; can't call await_only() here" cut off the bottom, leaving the
    # reader three screens of framework plumbing and no cause. Shown only
    # when it differs from the message already printed above.
    cause = exception_summary(event.traceback_text)
    if cause and cause[:80] not in event.error_message:
        parts.append(f"<b>Cause:</b> {_esc(cause, 400)}")

    if event.category:
        parts.append(f"<b>Category:</b> {_esc(event.category)}")

    if event.endpoint:
        ep = _esc(event.endpoint)
        if event.method:
            ep = f"{_esc(event.method)} {ep}"
        if event.status_code:
            ep = f"{ep} → {event.status_code}"
        parts.append(f"<b>Endpoint:</b> <code>{ep}</code>")

    error_code = e.get("error_code")
    if error_code:
        parts.append(f"<b>Error Code:</b> <code>{_esc(error_code)}</code>")

    # -- User & Request Section --
    user_id = e.get("user_id")
    phone = e.get("phone")
    role = e.get("role")
    request_id = e.get("request_id")
    client_ip = e.get("client_ip")
    user_agent = e.get("user_agent")

    if any((user_id, phone, request_id, client_ip)):
        parts.append(_SEP)
        parts.append("<b>Request</b>")
        if request_id:
            parts.append(f"  <b>ID:</b> <code>{_esc(request_id)}</code>")
        if user_id or phone:
            user_line: list[str] = []
            if user_id:
                user_line.append(f"<code>{_esc(user_id)}</code>")
            if phone:
                user_line.append(f"({_esc(phone)})")
            if role:
                user_line.append(f"[{_esc(role)}]")
            parts.append(f"  <b>User:</b> {' '.join(user_line)}")
        if client_ip:
            parts.append(f"  <b>IP:</b> {_esc(client_ip)}")
        if user_agent:
            parts.append(f"  <b>UA:</b> {_esc(user_agent, 100)}")

    # -- Order / Business Context --
    order_id = e.get("order_id")
    card_last4 = e.get("card_number_last4")
    if order_id or card_last4:
        parts.append(_SEP)
        parts.append("<b>Context</b>")
        if order_id:
            parts.append(f"  <b>Order:</b> <code>{_esc(order_id)}</code>")
        if card_last4:
            parts.append(f"  <b>Card:</b> ****{_esc(card_last4)}")

    # -- Provider Section --
    hamkor_method = e.get("hamkor_method")
    hamkor_request_id = e.get("hamkor_request_id")
    hamkor_status = e.get("hamkor_status")
    hamkor_response = e.get("hamkor_response")
    hamkor_error = e.get("hamkor_error")
    elapsed_ms = e.get("elapsed_ms")

    if any((hamkor_method, hamkor_request_id, hamkor_status)):
        parts.append(_SEP)
        parts.append("<b>Provider</b>")
        if hamkor_method:
            parts.append(f"  <b>Method:</b> <code>{_esc(hamkor_method)}</code>")
        if hamkor_request_id:
            parts.append(f"  <b>Request ID:</b> <code>{_esc(hamkor_request_id)}</code>")
        if hamkor_status:
            parts.append(f"  <b>HTTP Status:</b> {_esc(hamkor_status)}")
        if elapsed_ms:
            parts.append(f"  <b>Duration:</b> {_esc(elapsed_ms)}ms")
        if hamkor_error:
            parts.append(f"  <b>Error:</b> {_esc(hamkor_error, 300)}")

    # -- Request Body --
    request_body = e.get("request_body")
    if request_body:
        parts.append(_SEP)
        parts.append("<b>Request Body</b>")
        parts.append(f"<pre>{_esc(request_body, 400)}</pre>")

    # -- Response Body --
    response_body = e.get("response_body")
    if response_body:
        parts.append(_SEP)
        parts.append("<b>Response Body</b>")
        parts.append(f"<pre>{_esc(response_body, 500)}</pre>")

    # -- Provider Raw Response --
    if hamkor_response and not response_body:
        parts.append(_SEP)
        parts.append("<b>Provider Response</b>")
        parts.append(f"<pre>{_esc(hamkor_response, 500)}</pre>")

    # -- Other extras --
    other_extras = {k: v for k, v in e.items() if k not in _HANDLED_FIELDS}
    if other_extras:
        parts.append(_SEP)
        for key, val in other_extras.items():
            parts.append(f"<b>{_esc(key)}:</b> {_esc(val)}")

    # -- Footer --
    parts.append(_SEP)
    try:
        tz_name = settings.timezone if settings else "UTC"
        tz = ZoneInfo(tz_name)
        local_ts = event.timestamp.astimezone(tz)
        ts = local_ts.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        ts = event.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")

    logger_name = e.get("logger", "")
    source = str(event.source) if event.source not in ("http", "websocket") else ""
    footer_parts = [ts]
    if logger_name:
        footer_parts.append(_esc(logger_name))
    if source:
        footer_parts.append(f"[{_esc(source)}]")
    parts.append("  ".join(footer_parts))

    header = "\n".join(parts)
    if len(header) > max_msg_len:
        # The header alone overflows (huge bodies/extras) — cut it safely.
        return _cut_to_valid(header, max_msg_len)
    if not event.traceback_text:
        return header

    # -- Traceback --
    # Escaping inflates the text (&lt; etc.), so condense against a budget
    # measured AFTER escaping, then escape the result.
    overhead = len(_SEP) + len("<pre></pre>") + 2
    available = max_msg_len - len(header) - overhead
    if available < 120:
        return header

    tb = condense_traceback(event.traceback_text, available)
    escaped = html.escape(tb)
    while len(escaped) > available and len(tb) > 40:
        tb = condense_traceback(tb, int(len(tb) * 0.8))
        escaped = html.escape(tb)

    return f"{header}{_SEP}\n<pre>{escaped}</pre>"


def format_error_messages(
    event: ErrorEvent, count: int = 1, max_parts: int = 3
) -> list[str]:
    """Render an event as one or more Telegram messages.

    The first carries the full context; any remainder of the traceback
    follows in continuation messages instead of being thrown away. Capped at
    ``max_parts`` so a single pathological traceback cannot flood the channel
    — and when the cap bites, the message says how much was dropped rather
    than trailing off.
    """
    settings = _get_settings_or_none()
    max_msg_len = settings.max_message_len if settings else 4096

    first = format_error_html(event, count)
    if not event.traceback_text:
        return [first]

    # What of the traceback actually made it into the first message?
    shown = ""
    if "<pre>" in first:
        shown = html.unescape(first.rsplit("<pre>", 1)[1].split("</pre>")[0])

    full = condense_traceback(event.traceback_text, max_msg_len * max_parts)
    if not shown or shown not in full:
        return [first]

    remainder = full[full.index(shown) + len(shown) :].lstrip("\n")
    if not remainder.strip():
        return [first]

    messages = [first]
    body_budget = max_msg_len - 80  # room for the "part N" heading
    pieces = chunk_text(remainder, body_budget)

    for index, piece in enumerate(pieces[: max_parts - 1], start=2):
        messages.append(
            f"<b>↳ traceback (part {index})</b>\n<pre>{html.escape(piece)}</pre>"
        )

    dropped = len(pieces) - (max_parts - 1)
    if dropped > 0:
        messages[-1] += f"\n<i>({dropped} further section(s) omitted)</i>"

    return messages


def _cut_to_valid(text: str, limit: int) -> str:
    """Trim to ``limit`` on a line boundary, closing <pre> if it was open."""
    marker = "\n... cut ..."
    budget = limit - len(marker) - len("</pre>")
    cut = text[:budget]
    newline = cut.rfind("\n")
    if newline > 0:
        cut = cut[:newline]
    if cut.count("<pre>") > cut.count("</pre>"):
        return cut + marker + "</pre>"
    return cut + marker
