"""Sensitive data masking utilities."""

from __future__ import annotations

import json
from typing import Any

from oh_notifier.config import _get_settings_or_none


def _get_sensitive_keys() -> frozenset[str]:
    settings = _get_settings_or_none()
    if settings:
        return settings.sensitive_keys
    return frozenset({
        "password", "token", "secret", "card_number", "number",
        "cvv", "cvc", "pin", "otp", "code", "confirm_code",
    })


def mask_sensitive(data: dict[str, Any]) -> dict[str, str]:
    """Mask sensitive keys in a dict."""
    sensitive = _get_sensitive_keys()
    masked: dict[str, str] = {}
    for key, val in data.items():
        lower_key = key.lower()
        if any(s in lower_key for s in sensitive):
            masked[key] = "***"
        elif isinstance(val, dict):
            masked[key] = str(mask_sensitive(val))
        else:
            masked[key] = str(val)
    return masked


def summarize_body(body_bytes: bytes, max_len: int = 500) -> str:
    """Parse JSON body, mask sensitive fields, truncate."""
    try:
        data = json.loads(body_bytes)
        if isinstance(data, dict):
            data = mask_sensitive(data)
        text = json.dumps(data, ensure_ascii=False, default=str)
    except (json.JSONDecodeError, UnicodeDecodeError):
        text = body_bytes.decode("utf-8", errors="replace")
    if len(text) > max_len:
        text = text[:max_len] + "..."
    return text
