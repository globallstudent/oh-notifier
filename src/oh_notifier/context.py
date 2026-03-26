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
    """Call once at startup to store hostname/env/commit and device metadata."""
    import socket

    _env_info["hostname"] = platform.node()
    _env_info["env"] = app_env or os.environ.get("APP_ENV", "DEVELOPMENT")

    # Device metadata
    _env_info["os"] = f"{platform.system()} {platform.release()}"
    _env_info["python"] = platform.python_version()
    _env_info["arch"] = platform.machine()

    # Network identity
    try:
        _env_info["ip"] = socket.gethostbyname(socket.gethostname())
    except Exception:
        pass

    # Container/K8s detection
    pod_name = os.environ.get("HOSTNAME", "")
    if pod_name:
        _env_info["pod"] = pod_name
    k8s_namespace = os.environ.get("POD_NAMESPACE", "")
    if k8s_namespace:
        _env_info["namespace"] = k8s_namespace
    node_name = os.environ.get("NODE_NAME", "")
    if node_name:
        _env_info["node"] = node_name

    # Container ID from cgroup (Docker/K8s)
    try:
        with open("/proc/self/cgroup", "r") as f:
            for line in f:
                if "docker" in line or "containerd" in line or "kubepods" in line:
                    container_id = line.strip().split("/")[-1][:12]
                    if container_id:
                        _env_info["container_id"] = container_id
                    break
    except Exception:
        pass

    if git_commit or os.environ.get("GIT_COMMIT"):
        _env_info["git_commit"] = (git_commit or os.environ.get("GIT_COMMIT", ""))[:12]
