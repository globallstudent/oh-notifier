"""Environment resolution and .env loading.

Direct cover for the defect that motivated this module: every production
alert was labelled ``[DEVELOPMENT]`` because ``APP_ENV`` is unset on all four
production deployments and the old default filled the gap silently.
"""

from __future__ import annotations

import pytest

from oh_notifier.env import (
    UNKNOWN_ENVIRONMENT,
    canonical_environment,
    env_bool,
    env_int,
    env_str,
    load_dotenv_files,
    resolve_environment,
)

_ENV_VARS = ("OH_NOTIFIER_ENV", "APP_ENV", "ENVIRONMENT", "ENV")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# -- resolution -------------------------------------------------------------


def test_nothing_set_is_unknown_not_development():
    """The whole point: silence must not be reported as 'development'."""
    env, source = resolve_environment(None)
    assert env == UNKNOWN_ENVIRONMENT
    assert source == "unset"


def test_app_env_is_used(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    assert resolve_environment(None) == ("production", "APP_ENV")


def test_oh_notifier_env_beats_app_env(monkeypatch):
    """A service can label its alerts separately from its own APP_ENV."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OH_NOTIFIER_ENV", "demo")
    assert resolve_environment(None) == ("demo", "OH_NOTIFIER_ENV")


def test_bare_development_argument_does_not_mask_a_real_env(monkeypatch):
    """All four services pass ``os.environ.get("APP_ENV", "development")``.

    Trusting that argument would keep hiding a set APP_ENV behind the
    hard-coded fallback, which is exactly the bug.
    """
    monkeypatch.setenv("APP_ENV", "production")
    assert resolve_environment("development") == ("production", "APP_ENV")


def test_bare_development_argument_with_nothing_set_is_flagged():
    env, source = resolve_environment("development")
    assert env == "development"
    # Marked so the formatter can show it was never deliberately chosen.
    assert source == "argument-default"


def test_explicit_non_default_argument_wins(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    assert resolve_environment("demo") == ("demo", "argument")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("prod", "production"),
        ("PRODUCTION", "production"),
        ("  Live  ", "production"),
        ("stage", "staging"),
        ("dev", "development"),
        ("demo", "demo"),
        # Unrecognised names pass through — new environments must not need
        # a code change to be labelled correctly.
        ("sandbox", "sandbox"),
        ("qa-2", "qa-2"),
        ("", UNKNOWN_ENVIRONMENT),
        (None, UNKNOWN_ENVIRONMENT),
    ],
)
def test_canonical_environment(raw, expected):
    assert canonical_environment(raw) == expected


# -- .env loading -----------------------------------------------------------


def test_dotenv_precedence(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SHARED=base\nONLY_BASE=1\n")
    (tmp_path / ".env.production").write_text("SHARED=prod\n")
    monkeypatch.delenv("SHARED", raising=False)
    monkeypatch.delenv("ONLY_BASE", raising=False)

    applied = load_dotenv_files("production", tmp_path)

    assert len(applied) == 2
    import os

    assert os.environ["SHARED"] == "prod"   # env-specific file read first
    assert os.environ["ONLY_BASE"] == "1"   # base still contributes


def test_real_env_wins_over_dotenv(tmp_path, monkeypatch):
    """In Kubernetes the manifest is the source of truth; a baked-in .env
    must not silently outrank it."""
    (tmp_path / ".env").write_text("SHARED=from_file\n")
    monkeypatch.setenv("SHARED", "from_manifest")

    load_dotenv_files("production", tmp_path)

    import os

    assert os.environ["SHARED"] == "from_manifest"


def test_dotenv_parsing_quirks(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "\n".join([
            "# a comment",
            "",
            "export EXPORTED=yes",
            'QUOTED="hello world"',
            "SINGLE='single'",
            "EMPTY=",
            "WITH_EQUALS=a=b=c",
            "  SPACED  =  padded  ",
            "not_a_pair",
        ])
    )
    for key in ("EXPORTED", "QUOTED", "SINGLE", "EMPTY", "WITH_EQUALS", "SPACED"):
        monkeypatch.delenv(key, raising=False)

    load_dotenv_files("production", tmp_path)

    import os

    assert os.environ["EXPORTED"] == "yes"
    assert os.environ["QUOTED"] == "hello world"
    assert os.environ["SINGLE"] == "single"
    assert os.environ["EMPTY"] == ""
    assert os.environ["WITH_EQUALS"] == "a=b=c"
    assert os.environ["SPACED"] == "padded"


def test_missing_dotenv_dir_is_not_an_error(tmp_path):
    assert load_dotenv_files("production", tmp_path / "nope") == []


# -- typed readers ----------------------------------------------------------


def test_per_environment_override_wins(monkeypatch):
    """How demo traffic gets routed to a different chat."""
    monkeypatch.setenv("OH_NOTIFIER_CHAT_ID", "-100base")
    monkeypatch.setenv("OH_NOTIFIER_CHAT_ID_DEMO", "-100demo")

    assert env_str("CHAT_ID", "", environment="demo") == "-100demo"
    assert env_str("CHAT_ID", "", environment="production") == "-100base"


def test_readers_fall_back_on_garbage(monkeypatch):
    monkeypatch.setenv("OH_NOTIFIER_MAX_BUFFER_SIZE", "not-a-number")
    monkeypatch.setenv("OH_NOTIFIER_ENABLED", "maybe")
    assert env_int("MAX_BUFFER_SIZE", 50) == 50
    assert env_bool("ENABLED", True) is True


@pytest.mark.parametrize("raw,expected", [("1", True), ("true", True), ("YES", True),
                                          ("0", False), ("false", False), ("off", False)])
def test_env_bool_spellings(monkeypatch, raw, expected):
    monkeypatch.setenv("OH_NOTIFIER_ENABLED", raw)
    assert env_bool("ENABLED", not expected) is expected
