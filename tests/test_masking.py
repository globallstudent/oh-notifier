"""Tests for sensitive data masking."""

from oh_notifier.masking import mask_sensitive, summarize_body


def test_mask_password():
    result = mask_sensitive({"password": "secret123", "username": "admin"})
    assert result["password"] == "***"
    assert result["username"] == "admin"


def test_mask_token():
    result = mask_sensitive({"access_token": "jwt.xxx.yyy"})
    assert result["access_token"] == "***"


def test_mask_card_number():
    result = mask_sensitive({"card_number": "8600123456789012"})
    assert result["card_number"] == "***"


def test_mask_nested():
    result = mask_sensitive({"data": {"otp": "12345", "name": "test"}})
    assert "***" in result["data"]


def test_mask_otp():
    result = mask_sensitive({"otp_code": "123456"})
    assert result["otp_code"] == "***"


def test_summarize_body_json():
    body = b'{"username": "admin", "password": "secret"}'
    result = summarize_body(body)
    assert "***" in result
    assert "admin" in result


def test_summarize_body_non_json():
    body = b"plain text body"
    result = summarize_body(body)
    assert result == "plain text body"


def test_summarize_body_truncation():
    body = b'{"key": "' + b"x" * 1000 + b'"}'
    result = summarize_body(body, max_len=100)
    assert len(result) <= 103  # 100 + "..."
    assert result.endswith("...")
