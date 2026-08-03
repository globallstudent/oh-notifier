"""ContextVar-based request context for per-request metadata."""

from __future__ import annotations

import contextvars
import os
import platform
from typing import Any

#: ``None`` rather than ``{}``. A mutable default is shared by every context
#: that has not set its own, and the old code did ``ctx.update(...)`` on
#: whatever ``get()`` returned — so a task that inherited a parent's dict
#: mutated the parent's copy. Concretely: one request's ``user_id`` could
#: surface on another request's alert. Each write now installs a fresh dict,
#: which is what makes contextvars copy-on-write in practice.
_request_ctx: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "_oh_request_ctx", default=None
)

_env_info: dict[str, str] = {}


def set_request_context(**kwargs: Any) -> None:
    """Add context to the current request (user_id, order_id, etc.)."""
    try:
        updates = {k: str(v) for k, v in kwargs.items() if v is not None}
        if not updates:
            return
        current = _request_ctx.get()
        merged = dict(current) if current else {}
        merged.update(updates)
        _request_ctx.set(merged)
    except Exception:
        pass


def get_request_context() -> dict[str, str]:
    """Return merged env info + per-request context."""
    try:
        ctx = dict(_env_info)
        current = _request_ctx.get()
        if current:
            ctx.update(current)
        return ctx
    except Exception:
        return dict(_env_info)


def reset_request_context() -> None:
    """Reset per-request context. Called at start of each request."""
    _request_ctx.set(None)


def get_env_info() -> dict[str, str]:
    """The process-wide metadata captured at startup."""
    return dict(_env_info)


def init_env_info(
    app_env: str | None = None,
    git_commit: str | None = None,
    environment_source: str | None = None,
) -> None:
    """Call once at startup to store host/env/commit and device metadata.

    Everything here is a cheap local read. The previous version also did a
    ``socket.gethostbyname(socket.gethostname())``, a blocking DNS lookup on
    the startup path that can stall for seconds when resolution is slow — a
    real risk in a cluster, and of little value there since the pod name and
    node name below identify the workload better than a pod IP does.
    """
    _env_info["hostname"] = platform.node()
    if app_env:
        _env_info["env"] = app_env
    if environment_source:
        _env_info["env_source"] = environment_source

    # Device metadata
    _env_info["os"] = f"{platform.system()} {platform.release()}"
    _env_info["python"] = platform.python_version()
    _env_info["arch"] = platform.machine()

    # Container/K8s identity
    for var, key in (
        ("HOSTNAME", "pod"),
        ("POD_NAMESPACE", "namespace"),
        ("NODE_NAME", "node"),
        ("POD_IP", "ip"),
    ):
        value = os.environ.get(var, "")
        if value:
            _env_info[key] = value

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

    commit = git_commit or os.environ.get("GIT_COMMIT", "")
    if commit:
        _env_info["git_commit"] = commit[:12]

    version = os.environ.get("SERVICE_VERSION") or os.environ.get("IMAGE_TAG", "")
    if version:
        _env_info["version"] = version
