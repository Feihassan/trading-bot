"""
Phase 1 tests: configuration validation and safety gates.

These tests construct Settings directly with explicit kwargs (bypassing
.env / real environment variables) so they're deterministic regardless of
the machine they run on.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import LIVE_CONFIRMATION_PHRASE, Settings, TradingMode


def _settings(**overrides) -> Settings:
    defaults = dict(_env_file=None)
    defaults.update(overrides)
    return Settings(**defaults)


class TestTradingModeSafety:
    def test_default_mode_is_dry_run(self):
        s = _settings()
        assert s.trading_mode == TradingMode.DRY_RUN

    def test_demo_mode_does_not_require_confirmation(self):
        s = _settings(TRADING_MODE="DEMO")
        assert s.trading_mode == TradingMode.DEMO

    def test_live_mode_without_confirmation_is_rejected(self):
        with pytest.raises(ValidationError):
            _settings(TRADING_MODE="LIVE")

    def test_live_mode_with_wrong_confirmation_is_rejected(self):
        with pytest.raises(ValidationError):
            _settings(TRADING_MODE="LIVE", LIVE_TRADING_CONFIRMED="yes")

    def test_live_mode_with_correct_confirmation_is_accepted(self):
        s = _settings(TRADING_MODE="LIVE", LIVE_TRADING_CONFIRMED=LIVE_CONFIRMATION_PHRASE)
        assert s.trading_mode == TradingMode.LIVE


class TestRiskLimits:
    def test_risk_per_trade_within_bounds_accepted(self):
        s = _settings(RISK_PER_TRADE=0.02)
        assert s.risk_per_trade == 0.02

    def test_risk_per_trade_zero_rejected(self):
        with pytest.raises(ValidationError):
            _settings(RISK_PER_TRADE=0)

    def test_risk_per_trade_too_high_rejected(self):
        with pytest.raises(ValidationError):
            _settings(RISK_PER_TRADE=0.5)

    def test_min_risk_reward_below_one_rejected(self):
        with pytest.raises(ValidationError):
            _settings(MIN_RISK_REWARD=0.5)

    def test_max_daily_loss_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            _settings(MAX_DAILY_LOSS=1.5)


class TestSymbolMapping:
    def test_default_symbols_parsed(self):
        s = _settings()
        mapping = s.symbol_map
        assert mapping["EURUSD"] == "EURUSD"
        assert "XAUUSD" in mapping

    def test_broker_specific_suffix_mapping(self):
        s = _settings(SYMBOLS="EURUSD,XAUUSD:XAUUSDm")
        mapping = s.symbol_map
        assert mapping["EURUSD"] == "EURUSD"
        assert mapping["XAUUSD"] == "XAUUSDm"

    def test_primary_timeframes_parsed(self):
        s = _settings(PRIMARY_TIMEFRAMES="M15,H1,H4")
        assert s.primary_timeframes == ["M15", "H1", "H4"]


class TestTradingSessions:
    def test_empty_sessions_means_unrestricted(self):
        s = _settings()
        assert s.trading_sessions == []

    def test_sessions_parsed_as_tuples(self):
        s = _settings(TRADING_SESSIONS="07:00-16:00,12:00-21:00")
        assert s.trading_sessions == [("07:00", "16:00"), ("12:00", "21:00")]


class TestApiAuthSafety:
    """Security review finding: the dashboard API has no built-in auth,
    which is only safe because it binds to loopback by default. Binding
    elsewhere without an API_KEY must be refused at config load, the same
    way LIVE mode without confirmation is refused."""

    def test_default_localhost_binding_does_not_require_api_key(self):
        s = _settings()
        assert s.api_host == "127.0.0.1"
        assert s.api_key is None

    def test_localhost_binding_without_key_is_fine(self):
        s = _settings(API_HOST="localhost")
        assert s.api_key is None

    def test_non_local_binding_without_key_is_rejected(self):
        with pytest.raises(ValidationError):
            _settings(API_HOST="0.0.0.0")

    def test_non_local_binding_with_key_is_accepted(self):
        s = _settings(API_HOST="0.0.0.0", API_KEY="secret123")
        assert s.api_key == "secret123"
