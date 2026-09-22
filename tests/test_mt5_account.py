from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app.mt5.account as account_mod
from app.mt5.account import (
    AccountError,
    get_account_info,
    get_closed_pnl_between,
    get_closed_position_info,
    get_open_positions,
    is_demo_account,
    is_real_account,
)

FAKE_ACCOUNT = SimpleNamespace(
    login=123, balance=10_000.0, equity=10_050.0, margin=100.0, margin_free=9_950.0, margin_level=1000.0,
    profit=50.0, currency="USD", leverage=100, trade_allowed=True, server="Demo-Server", trade_mode=0,
)


class TestGetAccountInfo:
    def test_maps_fields_correctly(self, monkeypatch):
        monkeypatch.setattr(account_mod.mt5, "account_info", lambda: FAKE_ACCOUNT)
        info = get_account_info()
        assert info.login == 123
        assert info.equity == 10_050.0
        assert info.trade_allowed is True

    def test_none_raises_account_error(self, monkeypatch):
        monkeypatch.setattr(account_mod.mt5, "account_info", lambda: None)
        monkeypatch.setattr(account_mod.mt5, "last_error", lambda: (-1, "not connected"))
        with pytest.raises(AccountError):
            get_account_info()


class TestDemoRealHelpers:
    def test_is_demo_account_true_for_trade_mode_0(self):
        assert is_demo_account(SimpleNamespace(trade_mode=0)) is True

    def test_is_demo_account_false_for_real(self):
        assert is_demo_account(SimpleNamespace(trade_mode=2)) is False

    def test_is_real_account_true_for_trade_mode_2(self):
        assert is_real_account(SimpleNamespace(trade_mode=2)) is True

    def test_is_real_account_false_for_demo(self):
        assert is_real_account(SimpleNamespace(trade_mode=0)) is False


def fake_position(**overrides):
    defaults = dict(ticket=1, symbol="EURUSD", type=0, volume=0.1, price_open=1.1, price_current=1.105, sl=1.09, tp=1.12, profit=5.0, swap=0.0, magic=99, comment="", time=1700000000)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestGetOpenPositions:
    def test_maps_buy_and_sell_types(self, monkeypatch):
        monkeypatch.setattr(account_mod.mt5, "positions_get", lambda **kw: [fake_position(type=0), fake_position(ticket=2, type=1)])
        positions = get_open_positions()
        assert positions[0].type == "BUY"
        assert positions[1].type == "SELL"

    def test_filters_by_magic(self, monkeypatch):
        monkeypatch.setattr(account_mod.mt5, "positions_get", lambda **kw: [fake_position(magic=1), fake_position(ticket=2, magic=2)])
        positions = get_open_positions(magic=1)
        assert len(positions) == 1
        assert positions[0].magic == 1

    def test_none_with_ok_code_means_no_positions(self, monkeypatch):
        monkeypatch.setattr(account_mod.mt5, "positions_get", lambda **kw: None)
        monkeypatch.setattr(account_mod.mt5, "last_error", lambda: (1, "ok"))
        assert get_open_positions() == []

    def test_none_with_error_code_raises(self, monkeypatch):
        monkeypatch.setattr(account_mod.mt5, "positions_get", lambda **kw: None)
        monkeypatch.setattr(account_mod.mt5, "last_error", lambda: (-1, "connection lost"))
        with pytest.raises(AccountError):
            get_open_positions()

    def test_symbol_filter_passed_through(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(account_mod.mt5, "positions_get", lambda **kw: captured.update(kw) or [])
        get_open_positions(symbol="EURUSD")
        assert captured == {"symbol": "EURUSD"}


def fake_deal(**overrides):
    defaults = dict(profit=10.0, swap=-0.5, commission=-1.0, magic=99, entry=1, symbol="EURUSD", type=0, price=1.11, reason=0)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestGetClosedPnlBetween:
    def test_sums_only_exit_deals(self, monkeypatch):
        deals = [fake_deal(entry=0, profit=0, swap=0, commission=0), fake_deal(entry=1, profit=10.0, swap=-0.5, commission=-1.0)]
        monkeypatch.setattr(account_mod.mt5, "history_deals_get", lambda *a, **kw: deals)
        pnl = get_closed_pnl_between(datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))
        assert pnl == pytest.approx(8.5)

    def test_filters_by_magic(self, monkeypatch):
        deals = [fake_deal(entry=1, magic=1, profit=10.0, swap=0, commission=0), fake_deal(entry=1, magic=2, profit=100.0, swap=0, commission=0)]
        monkeypatch.setattr(account_mod.mt5, "history_deals_get", lambda *a, **kw: deals)
        pnl = get_closed_pnl_between(datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc), magic=1)
        assert pnl == pytest.approx(10.0)

    def test_none_with_ok_code_returns_zero(self, monkeypatch):
        monkeypatch.setattr(account_mod.mt5, "history_deals_get", lambda *a, **kw: None)
        monkeypatch.setattr(account_mod.mt5, "last_error", lambda: (1, "ok"))
        assert get_closed_pnl_between(datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc)) == 0.0

    def test_none_with_error_raises(self, monkeypatch):
        monkeypatch.setattr(account_mod.mt5, "history_deals_get", lambda *a, **kw: None)
        monkeypatch.setattr(account_mod.mt5, "last_error", lambda: (-1, "failed"))
        with pytest.raises(AccountError):
            get_closed_pnl_between(datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))


class TestGetClosedPositionInfo:
    def test_sl_exit_reason_detected(self, monkeypatch):
        opening = fake_deal(entry=0, type=0, price=1.10)
        closing = fake_deal(entry=1, type=1, price=1.095, reason=account_mod.mt5.DEAL_REASON_SL, profit=-50.0, swap=0, commission=0)
        monkeypatch.setattr(account_mod.mt5, "history_deals_get", lambda **kw: [opening, closing])
        info = get_closed_position_info(123)
        assert info.exit_reason == "SL"
        assert info.direction == "BUY"
        assert info.entry_price == 1.10
        assert info.exit_price == 1.095
        assert info.pnl == pytest.approx(-50.0)

    def test_tp_exit_reason_detected(self, monkeypatch):
        opening = fake_deal(entry=0, type=1, price=1.12)
        closing = fake_deal(entry=1, type=0, price=1.10, reason=account_mod.mt5.DEAL_REASON_TP, profit=20.0, swap=0, commission=0)
        monkeypatch.setattr(account_mod.mt5, "history_deals_get", lambda **kw: [opening, closing])
        info = get_closed_position_info(456)
        assert info.exit_reason == "TP"
        assert info.direction == "SELL"

    def test_manual_close_reason(self, monkeypatch):
        opening = fake_deal(entry=0, type=0, price=1.10)
        closing = fake_deal(entry=1, type=1, price=1.105, reason=account_mod.mt5.DEAL_REASON_CLIENT, profit=5.0, swap=0, commission=0)
        monkeypatch.setattr(account_mod.mt5, "history_deals_get", lambda **kw: [opening, closing])
        info = get_closed_position_info(789)
        assert info.exit_reason == "MANUAL"

    def test_no_deals_returns_none(self, monkeypatch):
        monkeypatch.setattr(account_mod.mt5, "history_deals_get", lambda **kw: [])
        assert get_closed_position_info(1) is None

    def test_no_closing_deal_returns_none(self, monkeypatch):
        opening = fake_deal(entry=0, type=0, price=1.10)
        monkeypatch.setattr(account_mod.mt5, "history_deals_get", lambda **kw: [opening])
        assert get_closed_position_info(1) is None

    def test_missing_opening_deal_falls_back_to_closing_price(self, monkeypatch):
        closing = fake_deal(entry=1, type=1, price=1.10, reason=0, profit=0, swap=0, commission=0)
        monkeypatch.setattr(account_mod.mt5, "history_deals_get", lambda **kw: [closing])
        info = get_closed_position_info(1)
        assert info.entry_price == 1.10
        assert info.direction == "UNKNOWN"
