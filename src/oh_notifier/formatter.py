"""Format ErrorEvent as Telegram HTML message."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from oh_notifier.config import _get_settings_or_none
from oh_notifier.env import UNKNOWN_ENVIRONMENT
from oh_notifier.event import ErrorEvent, ErrorSeverity

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

    # -- Traceback --
    if event.traceback_text:
        header_len = sum(len(p) + 1 for p in parts)
        # Room for the separator, the <pre> wrapper and a safety margin.
        available = max_msg_len - header_len - 60
        if available > 100:
            # Truncate the RAW traceback, then escape — escaping first and
            # cutting after can split an entity and get the message rejected.
            tb = html.escape(_smart_truncate(event.traceback_text, available))
            parts.append(_SEP)
            parts.append(f"<pre>{tb}</pre>")

    result = "\n".join(parts)

    if len(result) > max_msg_len:
        # Cut on a line boundary so we never end mid-tag or mid-entity;
        # the old code appended a bare "</pre>" which produced markup
        # Telegram rejects outright.
        result = _cut_to_valid(result, max_msg_len)

    return result


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
