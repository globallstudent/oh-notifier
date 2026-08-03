from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "UNKNOWN_ENVIRONMENT",
    "canonical_environment",
    "env_bool",
    "env_float",
    "env_int",
    "env_str",
    "load_dotenv_files",
    "resolve_environment",
]

UNKNOWN_ENVIRONMENT = "unknown"

_ALIASES = {
    "prod": "production",
    "production": "production",
    "live": "production",
    "stage": "staging",
    "staging": "staging",
    "dev": "development",
    "develop": "development",
    "development": "development",
    "test": "test",
    "testing": "test",
    "local": "local",
    "demo": "demo",
}

_ENV_VARS = ("OH_NOTIFIER_ENV", "APP_ENV", "ENVIRONMENT", "ENV")

_TRUE = frozenset({"1", "true", "yes", "on", "y", "t"})
_FALSE = frozenset({"0", "false", "no", "off", "n", "f"})


def canonical_environment(value: str | None) -> str:
    if not value:
        return UNKNOWN_ENVIRONMENT
    cleaned = value.strip().lower()
    if not cleaned:
        return UNKNOWN_ENVIRONMENT
    return _ALIASES.get(cleaned, cleaned)


def resolve_environment(explicit: str | None = None) -> tuple[str, str]:
    if explicit and explicit.strip().lower() not in ("development", ""):
        return canonical_environment(explicit), "argument"

    for var in _ENV_VARS:
        raw = os.environ.get(var)
        if raw and raw.strip():
            return canonical_environment(raw), var

    if explicit and explicit.strip():
        return canonical_environment(explicit), "argument-default"

    return UNKNOWN_ENVIRONMENT, "unset"


# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------


def _parse_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[7:].lstrip()
    key, sep, value = line.partition("=")
    if not sep:
        return None
    key = key.strip()
    if not key:
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


def load_dotenv_files(
    environment: str,
    base_dir: str | os.PathLike[str] | None = None,
    *,
    override: bool = False,
) -> list[str]:
    root = Path(base_dir) if base_dir is not None else Path.cwd()
    candidates = [
        root / f".env.{environment}.local",
        root / f".env.{environment}",
        root / ".env.local",
        root / ".env",
    ]

    applied: list[str] = []
    for path in candidates:
        try:
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue

        for line in content.splitlines():
            parsed = _parse_line(line)
            if parsed is None:
                continue
            key, value = parsed
            if override or key not in os.environ:
                os.environ[key] = value
        applied.append(str(path))

    return applied


# ---------------------------------------------------------------------------
# Typed readers
# ---------------------------------------------------------------------------


def env_str(name: str, default: str = "", *, environment: str = "") -> str:

    if environment:
        suffixed = os.environ.get(f"OH_NOTIFIER_{name}_{environment.upper()}")
        if suffixed and suffixed.strip():
            return suffixed.strip()
    raw = os.environ.get(f"OH_NOTIFIER_{name}")
    if raw and raw.strip():
        return raw.strip()
    return default


def env_bool(name: str, default: bool, *, environment: str = "") -> bool:
    raw = env_str(name, "", environment=environment).lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return default


def env_int(name: str, default: int, *, environment: str = "") -> int:
    raw = env_str(name, "", environment=environment)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def env_float(name: str, default: float, *, environment: str = "") -> float:
    raw = env_str(name, "", environment=environment)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default
