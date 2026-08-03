from __future__ import annotations

import contextvars
import os
import platform
from typing import Any

_request_ctx: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "_oh_request_ctx", default=None
)

_env_info: dict[str, str] = {}


def set_request_context(**kwargs: Any) -> None:
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
    try:
        ctx = dict(_env_info)
        current = _request_ctx.get()
        if current:
            ctx.update(current)
        return ctx
    except Exception:
        return dict(_env_info)


def reset_request_context() -> None:
    _request_ctx.set(None)


def get_env_info() -> dict[str, str]:
    return dict(_env_info)


def init_env_info(
    app_env: str | None = None,
    git_commit: str | None = None,
    environment_source: str | None = None,
) -> None:

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
