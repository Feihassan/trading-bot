from __future__ import annotations

import pandas as pd
import pytest

from app.backtesting.engine import BacktestConfig, TradeSignal, run_backtest
from app.mt5.market_data import SymbolSpec

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
    trade_tick_value=10.0,  # $10 per 0.0001 move per lot
    trade_tick_size=0.0001,
    currency_base="EUR",
    currency_profit="USD",
    currency_margin="EUR",
)


def make_bars(rows: list[dict], spread: int = 0) -> pd.DataFrame:
    times = pd.date_range("2024-01-01", periods=len(rows), freq="15min", tz="UTC")
    df = pd.DataFrame(rows)
    df["time"] = times
    df["tick_volume"] = 100.0
    df["spread"] = spread
    df["real_volume"] = 0.0
    return df[["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]]


def once_then_none(signal: TradeSignal):
    """Returns `signal` the first time it's called, None every time after."""
    state = {"called": False}

    def strategy_fn(df: pd.DataFrame):
        if state["called"]:
            return None
        state["called"] = True
        return signal

    return strategy_fn


class TestTakeProfitAndStopLoss:
    def test_take_profit_hit_produces_expected_pnl(self):
        bars = make_bars(
            [
                dict(open=1.1000, high=1.1010, low=1.0990, close=1.1000),  # entry bar
                dict(open=1.1000, high=1.1050, low=1.0980, close=1.1020),  # no hit
                dict(open=1.1020, high=1.1120, low=1.1010, close=1.1100),  # TP hit (1.1100)
            ]
        )
        signal = TradeSignal(direction="BUY", entry=1.1000, stop_loss=1.0950, take_profit=1.1100)
        config = BacktestConfig(initial_balance=10_000, risk_per_trade=0.01, min_risk_reward=1.0)
        result = run_backtest(bars, once_then_none(signal), SPEC, config=config, warmup_bars=0)

        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.exit_reason == "TP"
        assert trade.volume == pytest.approx(0.2)  # $100 risk / (50 pips * $10/pip)
        assert trade.pnl == pytest.approx(200.0)  # 100 pips * $10/pip * 0.2 lots
        assert trade.r_multiple == pytest.approx(2.0)
        assert result.final_balance == pytest.approx(10_200.0)

    def test_stop_loss_hit_produces_expected_loss(self):
        bars = make_bars(
            [
                dict(open=1.1000, high=1.1010, low=1.0990, close=1.1000),
                dict(open=1.1000, high=1.1010, low=1.0940, close=1.0950),  # SL hit (1.0950)
            ]
        )
        signal = TradeSignal(direction="BUY", entry=1.1000, stop_loss=1.0950, take_profit=1.1100)
        config = BacktestConfig(initial_balance=10_000, risk_per_trade=0.01, min_risk_reward=1.0)
        result = run_backtest(bars, once_then_none(signal), SPEC, config=config, warmup_bars=0)

        trade = result.trades[0]
        assert trade.exit_reason == "SL"
        assert trade.pnl == pytest.approx(-100.0)
        assert trade.r_multiple == pytest.approx(-1.0)

    def test_ambiguous_same_bar_sl_and_tp_assumes_sl_first(self):
        bars = make_bars(
            [
                dict(open=1.1000, high=1.1010, low=1.0990, close=1.1000),
                dict(open=1.1000, high=1.1200, low=1.0900, close=1.1050),  # both SL and TP inside range
            ]
        )
        signal = TradeSignal(direction="BUY", entry=1.1000, stop_loss=1.0950, take_profit=1.1100)
        config = BacktestConfig(initial_balance=10_000, risk_per_trade=0.01, min_risk_reward=1.0)
        result = run_backtest(bars, once_then_none(signal), SPEC, config=config, warmup_bars=0)
        assert result.trades[0].exit_reason == "SL"

    def test_sell_direction_pnl(self):
        bars = make_bars(
            [
                dict(open=1.1000, high=1.1010, low=1.0990, close=1.1000),
                dict(open=1.1000, high=1.1010, low=1.0900, close=1.0900),  # TP hit for a sell (1.0900)
            ]
        )
        signal = TradeSignal(direction="SELL", entry=1.1000, stop_loss=1.1050, take_profit=1.0900)
        config = BacktestConfig(initial_balance=10_000, risk_per_trade=0.01, min_risk_reward=1.0)
        result = run_backtest(bars, once_then_none(signal), SPEC, config=config, warmup_bars=0)
        trade = result.trades[0]
        assert trade.exit_reason == "TP"
        assert trade.pnl > 0


class TestSpreadCost:
    def test_spread_reduces_pnl(self):
        bars_no_spread = make_bars(
            [
                dict(open=1.1000, high=1.1010, low=1.0990, close=1.1000),
                dict(open=1.1000, high=1.1120, low=1.1010, close=1.1100),
            ],
            spread=0,
        )
        bars_with_spread = make_bars(
            [
                dict(open=1.1000, high=1.1010, low=1.0990, close=1.1000),
                dict(open=1.1000, high=1.1120, low=1.1010, close=1.1100),
            ],
            spread=20,  # 2 pips
        )
        signal = TradeSignal(direction="BUY", entry=1.1000, stop_loss=1.0950, take_profit=1.1100)
        config = BacktestConfig(initial_balance=10_000, risk_per_trade=0.01, min_risk_reward=1.0)

        result_no_spread = run_backtest(bars_no_spread, once_then_none(signal), SPEC, config=config, warmup_bars=0)
        result_with_spread = run_backtest(bars_with_spread, once_then_none(signal), SPEC, config=config, warmup_bars=0)

        assert result_with_spread.trades[0].pnl < result_no_spread.trades[0].pnl


class TestRiskRewardFilter:
    def test_signal_below_min_rr_is_rejected(self):
        bars = make_bars(
            [
                dict(open=1.1000, high=1.1010, low=1.0990, close=1.1000),
                dict(open=1.1000, high=1.1010, low=1.0990, close=1.1000),
            ]
        )
        # Only 1:1 R:R, but min_risk_reward requires 2.0
        signal = TradeSignal(direction="BUY", entry=1.1000, stop_loss=1.0950, take_profit=1.1050)
        config = BacktestConfig(initial_balance=10_000, risk_per_trade=0.01, min_risk_reward=2.0)
        result = run_backtest(bars, once_then_none(signal), SPEC, config=config, warmup_bars=0)
        assert len(result.trades) == 0
        assert result.skipped_signals == 1


class TestNoSignalProducesFlatEquity:
    def test_never_trading_leaves_equity_unchanged(self):
        bars = make_bars([dict(open=1.1, high=1.101, low=1.099, close=1.1) for _ in range(10)])
        result = run_backtest(bars, lambda df: None, SPEC, warmup_bars=0)
        assert len(result.trades) == 0
        assert result.final_balance == pytest.approx(10_000.0)
