"""ContextVar-based request context for per-request metadata."""

from __future__ import annotations

import contextvars
import os
import platform
from typing import Any

_request_ctx: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "_oh_request_ctx", default={}
)

_env_info: dict[str, str] = {}


def set_request_context(**kwargs: Any) -> None:
    """Add context to the current request (user_id, order_id, etc.)."""
    try:
        ctx = _request_ctx.get()
        if not ctx:
            ctx = {}
            _request_ctx.set(ctx)
        ctx.update({k: str(v) for k, v in kwargs.items() if v is not None})
    except Exception:
        pass


def get_request_context() -> dict[str, str]:
    """Return merged env info + per-request context."""
    try:
        ctx = dict(_env_info)
        ctx.update(_request_ctx.get())
        return ctx
    except Exception:
        return dict(_env_info)


def reset_request_context() -> None:
    """Reset per-request context. Called at start of each request."""
    _request_ctx.set({})


def init_env_info(
    app_env: str | None = None,
    git_commit: str | None = None,
) -> None:
    """Call once at startup to store hostname/env/commit."""
    _env_info["hostname"] = platform.node()
    _env_info["env"] = app_env or os.environ.get("APP_ENV", "DEVELOPMENT")
    if git_commit or os.environ.get("GIT_COMMIT"):
        _env_info["git_commit"] = (git_commit or os.environ.get("GIT_COMMIT", ""))[:12]
