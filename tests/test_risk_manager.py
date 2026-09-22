from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config import Settings
from app.mt5.market_data import SymbolSpec
from app.risk.risk_manager import AccountState, RiskManager
from app.risk.safeguards import EmergencyStop
from app.strategy.signals import TradeIdea

SPEC = SymbolSpec(
    name="EURUSD",
    digits=5,
    point=0.00001,
    spread=10,
    spread_float=True,
    trade_contract_size=100_000.0,
    volume_min=0.01,
    volume_max=100.0,
    volume_step=0.01,
    trade_tick_value=10.0,
    trade_tick_size=0.0001,
    currency_base="EUR",
    currency_profit="USD",
    currency_margin="EUR",
)

NOON_UTC = datetime(2024, 6, 3, 12, 0, tzinfo=timezone.utc)  # a Monday
NO_STOP = EmergencyStop("this/path/does/not/exist/STOP")  # avoids depending on real filesystem state


def settings(**overrides) -> Settings:
    defaults = dict(_env_file=None)
    defaults.update(overrides)
    return Settings(**defaults)


def make_risk_manager(**settings_overrides) -> RiskManager:
    return RiskManager(settings(**settings_overrides), emergency_stop=NO_STOP)


def account(**overrides) -> AccountState:
    defaults = dict(
        equity=10_000.0,
        balance=10_000.0,
        day_start_equity=10_000.0,
        week_start_equity=10_000.0,
        peak_equity=10_000.0,
        open_positions_count=0,
        trades_today=0,
        exposure_by_currency={},
    )
    defaults.update(overrides)
    return AccountState(**defaults)


def idea(**overrides) -> TradeIdea:
    defaults = dict(
        symbol="EURUSD",
        timestamp=NOON_UTC,
        direction="BUY",
        confidence=70.0,
        entry=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        reward_risk=2.0,
        technical_score=80.0,
        technical_direction="BUY",
        structure_trend="UPTREND",
        regime="STRONG_BULLISH_TREND",
        ml_probabilities={"BUY": 0.7, "NEUTRAL": 0.2, "SELL": 0.1},
        ml_direction="BUY",
        spread_points=5.0,
        reasons=[],
        blocking_reasons=[],
    )
    defaults.update(overrides)
    return TradeIdea(**defaults)


class TestApprovedTrade:
    def test_valid_trade_is_approved_with_correct_sizing(self):
        rm = make_risk_manager(RISK_PER_TRADE=0.01)
        result = rm.evaluate(idea(), account(), SPEC, NOON_UTC)
        assert result.approved
        assert result.volume == pytest.approx(0.2)  # $100 risk / (50 pips * $10/pip)
        assert result.risk_amount == pytest.approx(100.0)


class TestBasicSignalValidity:
    def test_wait_signal_rejected(self):
        rm = make_risk_manager()
        result = rm.evaluate(idea(direction="WAIT"), account(), SPEC, NOON_UTC)
        assert not result.approved

    def test_missing_sl_tp_rejected(self):
        rm = make_risk_manager()
        result = rm.evaluate(idea(stop_loss=None), account(), SPEC, NOON_UTC)
        assert not result.approved
        assert "stop-loss" in result.blocking_reason.lower() or "sl" in result.blocking_reason.lower()

    def test_below_min_risk_reward_rejected(self):
        rm = make_risk_manager(MIN_RISK_REWARD=2.0)
        result = rm.evaluate(idea(reward_risk=1.2), account(), SPEC, NOON_UTC)
        assert not result.approved


class TestLossLimits:
    def test_daily_loss_limit_blocks_new_trades(self):
        rm = make_risk_manager(MAX_DAILY_LOSS=0.03)
        acc = account(day_start_equity=10_000, equity=9_600)  # -4%
        result = rm.evaluate(idea(), acc, SPEC, NOON_UTC)
        assert not result.approved
        assert "daily loss" in result.blocking_reason.lower()

    def test_daily_loss_under_limit_allowed(self):
        rm = make_risk_manager(MAX_DAILY_LOSS=0.03)
        acc = account(day_start_equity=10_000, equity=9_900)  # -1%
        result = rm.evaluate(idea(), acc, SPEC, NOON_UTC)
        assert result.approved

    def test_weekly_loss_limit_blocks_new_trades(self):
        rm = make_risk_manager(MAX_WEEKLY_LOSS=0.08, MAX_DAILY_LOSS=0.5)
        # Loss accrued earlier in the week, not today - isolates the weekly
        # check from the (separately tested) daily check.
        acc = account(day_start_equity=9_000, week_start_equity=10_000, equity=9_000)  # -10% for the week
        result = rm.evaluate(idea(), acc, SPEC, NOON_UTC)
        assert not result.approved
        assert "weekly loss" in result.blocking_reason.lower()

    def test_max_drawdown_halt(self):
        rm = make_risk_manager(MAX_DRAWDOWN_HALT=0.10)
        acc = account(peak_equity=12_000, equity=10_500)  # -12.5% from peak
        result = rm.evaluate(idea(), acc, SPEC, NOON_UTC)
        assert not result.approved
        assert "drawdown" in result.blocking_reason.lower()


class TestPositionAndTradeLimits:
    def test_max_open_trades_blocks(self):
        rm = make_risk_manager(MAX_OPEN_TRADES=3)
        acc = account(open_positions_count=3)
        result = rm.evaluate(idea(), acc, SPEC, NOON_UTC)
        assert not result.approved

    def test_max_trades_per_day_blocks(self):
        rm = make_risk_manager(MAX_TRADES_PER_DAY=5)
        acc = account(trades_today=5)
        result = rm.evaluate(idea(), acc, SPEC, NOON_UTC)
        assert not result.approved


class TestSpreadAndSessions:
    def test_excessive_spread_blocks(self):
        rm = make_risk_manager(MAX_SPREAD_POINTS=25)
        result = rm.evaluate(idea(spread_points=40.0), account(), SPEC, NOON_UTC)
        assert not result.approved
        assert "spread" in result.blocking_reason.lower()

    def test_outside_trading_session_blocks(self):
        rm = make_risk_manager(TRADING_SESSIONS="13:00-21:00")
        result = rm.evaluate(idea(), account(), SPEC, NOON_UTC)  # NOON_UTC = 12:00
        assert not result.approved
        assert "session" in result.blocking_reason.lower()

    def test_inside_trading_session_allowed(self):
        rm = make_risk_manager(TRADING_SESSIONS="07:00-16:00")
        result = rm.evaluate(idea(), account(), SPEC, NOON_UTC)
        assert result.approved

    def test_no_session_restriction_means_always_allowed(self):
        rm = make_risk_manager(TRADING_SESSIONS="")
        result = rm.evaluate(idea(), account(), SPEC, NOON_UTC)
        assert result.approved


class TestEmergencyStop:
    def test_tripped_emergency_stop_blocks_everything(self, tmp_path):
        stop = EmergencyStop(tmp_path / "STOP")
        stop.trip("test")
        rm = RiskManager(settings(), emergency_stop=stop)
        result = rm.evaluate(idea(), account(), SPEC, NOON_UTC)
        assert not result.approved
        assert "emergency" in result.blocking_reason.lower()


class TestCurrencyExposure:
    def test_exposure_limit_blocks_when_already_near_cap(self):
        rm = make_risk_manager(MAX_EXPOSURE_PER_CURRENCY=0.015, RISK_PER_TRADE=0.01)
        acc = account(exposure_by_currency={"EUR": 100.0})  # already $100 (1%) of $10k at risk in EUR
        result = rm.evaluate(idea(), acc, SPEC, NOON_UTC)  # this trade adds another $100 (1%) -> 2% > 1.5% cap
        assert not result.approved
        assert "exposure" in result.blocking_reason.lower()

    def test_exposure_within_limit_allowed(self):
        rm = make_risk_manager(MAX_EXPOSURE_PER_CURRENCY=0.10, RISK_PER_TRADE=0.01)
        acc = account(exposure_by_currency={"EUR": 100.0})
        result = rm.evaluate(idea(), acc, SPEC, NOON_UTC)
        assert result.approved


class TestMinimumLotOverRisk:
    def test_tiny_equity_rejected_rather_than_oversized(self):
        rm = make_risk_manager(RISK_PER_TRADE=0.01)
        acc = account(equity=50.0, day_start_equity=50.0, week_start_equity=50.0, peak_equity=50.0)
        result = rm.evaluate(idea(), acc, SPEC, NOON_UTC)
        assert not result.approved
