"""Format ErrorEvent as Telegram HTML message."""

from __future__ import annotations

import html
from typing import Any
from zoneinfo import ZoneInfo

from oh_notifier.config import _get_settings_or_none
from oh_notifier.event import ErrorEvent, ErrorSeverity

_SEVERITY_ICONS = {
    ErrorSeverity.CRITICAL: "\U0001f534",  # red circle
    ErrorSeverity.ERROR: "\U0001f7e0",     # orange circle
    ErrorSeverity.WARNING: "\U0001f7e1",   # yellow circle
    ErrorSeverity.INFO: "\U0001f535",      # blue circle
}

_SEP = "\n" + "\u2500" * 20

# Fields already handled in specific sections — skip from "Other" section
_HANDLED_FIELDS = frozenset({
    "env", "hostname", "request_id", "user_id", "phone", "role",
    "admin_role", "client_ip", "user_agent", "request_body",
    "response_body", "hamkor_method", "hamkor_request_id",
    "hamkor_status", "hamkor_response", "hamkor_error",
    "logger", "order_id", "card_number_last4", "endpoint",
    "method", "status_code", "error_code", "elapsed_ms", "attempt",
    "os", "python", "arch", "ip", "pod", "namespace", "node",
    "container_id", "git_commit",
})


def _smart_truncate(text: str, max_len: int) -> str:
    """Truncate keeping 30% head + 70% tail (tail is most useful)."""
    if len(text) <= max_len:
        return text
    head_len = int(max_len * 0.3)
    tail_len = max_len - head_len - 20
    return text[:head_len] + "\n... truncated ...\n" + text[-tail_len:]


def _esc(val: Any, max_len: int = 200) -> str:
    """HTML-escape and truncate a value."""
    return html.escape(str(val)[:max_len])


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
    env = e.get("env")
    hostname = e.get("hostname")
    if env or hostname:
        env_str = f"[{_esc(env.upper())}]" if env else ""
        host_str = _esc(hostname) if hostname else ""
        parts.append(f"{env_str} {host_str}".strip())

    # -- Device info (compact) --
    device_parts: list[str] = []
    if e.get("pod"):
        device_parts.append(f"pod:{_esc(e['pod'])}")
    if e.get("node"):
        device_parts.append(f"node:{_esc(e['node'])}")
    if e.get("ip") and not e.get("pod"):
        device_parts.append(f"ip:{_esc(e['ip'])}")
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

    if event.endpoint:
        ep = _esc(event.endpoint)
        if event.method:
            ep = f"{_esc(event.method)} {ep}"
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
        parts.append(_SEP)
        tb = html.escape(event.traceback_text)
        header_len = sum(len(p) for p in parts) + 30
        available = max_msg_len - header_len - 50
        if available > 100:
            tb = _smart_truncate(tb, available)
        else:
            tb = tb[:200]
        parts.append(f"<pre>{tb}</pre>")

    result = "\n".join(parts)

    # Final safety cut
    if len(result) > max_msg_len:
        result = result[: max_msg_len - 20] + "\n... cut ...</pre>"

    return result
