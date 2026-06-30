"""Tests for the legacy (disabled-by-default) email sender."""

import smtplib
from unittest.mock import MagicMock, patch

import pytest

from legacy_email.sender import send_digest


def test_send_digest_success(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")

    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = send_digest("Subject", "<html/>", "plain", ["r@example.com"])
        assert result is True
        mock_smtp.sendmail.assert_called_once()


def test_send_digest_no_credentials_returns_false(monkeypatch):
    for var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    result = send_digest("Subject", "<html/>", "plain", ["r@example.com"])
    assert result is False


def test_send_digest_smtp_error_returns_false(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")

    with patch("smtplib.SMTP", side_effect=smtplib.SMTPException("refused")):
        result = send_digest("Subject", "<html/>", "plain", ["r@example.com"])
        assert result is False


def test_send_digest_no_recipients_returns_false(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "u@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    result = send_digest("Subject", "<html/>", "plain", [])
    assert result is False
