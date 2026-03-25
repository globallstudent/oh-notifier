"""Tests for configure() and public API."""

import oh_notifier
from oh_notifier.config import get_settings
from oh_notifier.notifier import TelegramNotifier


def test_configure_creates_settings():
    oh_notifier.configure(
        bot_token="test-token",
        chat_id="test-chat",
        service_name="test-svc",
        environment="test",
    )
    settings = get_settings()
    assert settings.bot_token == "test-token"
    assert settings.chat_id == "test-chat"
    assert settings.service_name == "test-svc"
    assert settings.environment == "test"


def test_configure_creates_notifier():
    oh_notifier.configure(
        bot_token="t",
        chat_id="c",
        service_name="s",
    )
    notifier = TelegramNotifier.get_instance()
    assert notifier is not None
    assert notifier.service_name == "s"


def test_configure_custom_settings():
    oh_notifier.configure(
        bot_token="t",
        chat_id="c",
        service_name="s",
        timezone="Asia/Tashkent",
        dedup_window=60.0,
        max_buffer_size=100,
    )
    settings = get_settings()
    assert settings.timezone == "Asia/Tashkent"
    assert settings.dedup_window == 60.0
    assert settings.max_buffer_size == 100


def test_send_alert_without_crash():
    oh_notifier.configure(bot_token="t", chat_id="c", service_name="s", enabled=False)
    oh_notifier.send_alert("test error")
    oh_notifier.send_warning("test warning")
    oh_notifier.send_info("test info")
