from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

import app.notifications.telegram as telegram_mod
from app.config import Settings
from app.notifications.telegram import TelegramNotifier


def settings(**overrides) -> Settings:
    defaults = dict(_env_file=None, TELEGRAM_ENABLED=True, TELEGRAM_BOT_TOKEN="test-token", TELEGRAM_CHAT_ID="12345")
    defaults.update(overrides)
    return Settings(**defaults)


class TestDisabledOrMisconfigured:
    def test_disabled_notifier_never_calls_http(self, monkeypatch):
        called = {"count": 0}
        monkeypatch.setattr(telegram_mod.httpx, "post", lambda *a, **kw: called.update(count=called["count"] + 1))
        notifier = TelegramNotifier(settings(TELEGRAM_ENABLED=False))
        assert notifier.notify_disconnected("test") is False
        assert called["count"] == 0

    def test_enabled_without_token_returns_false_without_raising(self, monkeypatch):
        called = {"count": 0}
        monkeypatch.setattr(telegram_mod.httpx, "post", lambda *a, **kw: called.update(count=called["count"] + 1))
        notifier = TelegramNotifier(settings(TELEGRAM_BOT_TOKEN=None))
        assert notifier.notify_disconnected("test") is False
        assert called["count"] == 0


class TestSendSuccessAndFailure:
    def test_successful_send_returns_true(self, monkeypatch):
        monkeypatch.setattr(
            telegram_mod.httpx, "post", lambda *a, **kw: SimpleNamespace(raise_for_status=lambda: None)
        )
        notifier = TelegramNotifier(settings())
        assert notifier.notify_disconnected("MT5 dropped") is True

    def test_http_error_is_caught_and_returns_false(self, monkeypatch):
        def raise_error(*args, **kwargs):
            raise httpx.ConnectError("network unreachable")

        monkeypatch.setattr(telegram_mod.httpx, "post", raise_error)
        notifier = TelegramNotifier(settings())
        assert notifier.notify_disconnected("MT5 dropped") is False

    def test_request_targets_correct_chat_and_token(self, monkeypatch):
        captured = {}

        def fake_post(url, json, timeout):
            captured["url"] = url
            captured["json"] = json
            return SimpleNamespace(raise_for_status=lambda: None)

        monkeypatch.setattr(telegram_mod.httpx, "post", fake_post)
        notifier = TelegramNotifier(settings(TELEGRAM_BOT_TOKEN="abc123", TELEGRAM_CHAT_ID="999"))
        notifier.notify_disconnected("test")
        assert "abc123" in captured["url"]
        assert captured["json"]["chat_id"] == "999"


class TestMessageFormatting:
    def setup_method(self):
        self.sent = {}

        def fake_post(url, json, timeout):
            self.sent["text"] = json["text"]
            return SimpleNamespace(raise_for_status=lambda: None)

        self._fake_post = fake_post

    def test_notify_signal_includes_key_fields(self, monkeypatch):
        monkeypatch.setattr(telegram_mod.httpx, "post", self._fake_post)
        notifier = TelegramNotifier(settings())
        notifier.notify_signal("EURUSD", "BUY", 1.1000, 1.0950, 1.1100, 0.01, 2.0, "Bullish structure + EMA alignment")
        text = self.sent["text"]
        assert "EURUSD" in text and "BUY" in text
        assert "1.10000" in text or "1.1000" in text
        assert "1:2.0" in text

    def test_notify_trade_closed_sl_hit_headline(self, monkeypatch):
        monkeypatch.setattr(telegram_mod.httpx, "post", self._fake_post)
        notifier = TelegramNotifier(settings())
        notifier.notify_trade_closed("EURUSD", "BUY", "SL", 1.1000, 1.0950, -50.0, 12345)
        assert "STOP LOSS HIT" in self.sent["text"]

    def test_notify_trade_closed_tp_hit_headline(self, monkeypatch):
        monkeypatch.setattr(telegram_mod.httpx, "post", self._fake_post)
        notifier = TelegramNotifier(settings())
        notifier.notify_trade_closed("EURUSD", "BUY", "TP", 1.1000, 1.1100, 100.0, 12345)
        assert "TAKE PROFIT HIT" in self.sent["text"]

    def test_notify_trade_closed_manual_headline(self, monkeypatch):
        monkeypatch.setattr(telegram_mod.httpx, "post", self._fake_post)
        notifier = TelegramNotifier(settings())
        notifier.notify_trade_closed("EURUSD", "BUY", "MANUAL", 1.1000, 1.1050, 50.0, 12345)
        assert "TRADE CLOSED" in self.sent["text"]

    def test_notify_emergency_stop_mentions_reason(self, monkeypatch):
        monkeypatch.setattr(telegram_mod.httpx, "post", self._fake_post)
        notifier = TelegramNotifier(settings())
        notifier.notify_emergency_stop("Account mismatch detected")
        assert "EMERGENCY STOP" in self.sent["text"]
        assert "Account mismatch detected" in self.sent["text"]

    def test_notify_risk_halt_mentions_code(self, monkeypatch):
        monkeypatch.setattr(telegram_mod.httpx, "post", self._fake_post)
        notifier = TelegramNotifier(settings())
        notifier.notify_risk_halt("DAILY_LOSS_LIMIT", "-4.00% >= -3.00%")
        assert "DAILY LOSS LIMIT" in self.sent["text"]
