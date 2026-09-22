from __future__ import annotations

import pandas as pd
import pytest

from app.backtesting.engine import BacktestConfig, BacktestResult, Trade
from app.backtesting.metrics import compute_metrics


def make_trade(pnl: float, r_multiple: float, minute: int) -> Trade:
    t = pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(minutes=minute)
    return Trade(
        direction="BUY",
        entry_time=t,
        entry_price=1.1,
        exit_time=t + pd.Timedelta(minutes=15),
        exit_price=1.1 + (0.001 if pnl > 0 else -0.001),
        stop_loss=1.09,
        take_profit=1.12,
        volume=0.1,
        pnl=pnl,
        r_multiple=r_multiple,
        exit_reason="TP" if pnl > 0 else "SL",
        duration_bars=3,
    )


class TestComputeMetrics:
    def setup_method(self):
        self.trades = [
            make_trade(200.0, 2.0, 0),
            make_trade(100.0, 1.0, 15),
            make_trade(-100.0, -1.0, 30),
            make_trade(-50.0, -0.5, 45),
        ]
        equity_curve = pd.DataFrame(
            {
                "time": [
                    pd.Timestamp("2024-01-01", tz="UTC"),
                    pd.Timestamp("2024-01-01T00:15", tz="UTC"),
                    pd.Timestamp("2024-01-01T00:30", tz="UTC"),
                    pd.Timestamp("2024-01-01T00:45", tz="UTC"),
                    pd.Timestamp("2024-01-01T01:00", tz="UTC"),
                ],
                "equity": [10_000, 10_200, 10_300, 10_200, 10_150],
            }
        )
        self.result = BacktestResult(
            trades=self.trades,
            equity_curve=equity_curve,
            config=BacktestConfig(initial_balance=10_000),
            final_balance=10_150,
        )

    def test_basic_counts_and_win_rate(self):
        m = compute_metrics(self.result)
        assert m.total_trades == 4
        assert m.winning_trades == 2
        assert m.losing_trades == 2
        assert m.win_rate == pytest.approx(0.5)

    def test_profit_factor_and_expectancy(self):
        m = compute_metrics(self.result)
        assert m.profit_factor == pytest.approx(2.0)  # 300 gross profit / 150 gross loss
        assert m.avg_win == pytest.approx(150.0)
        assert m.avg_loss == pytest.approx(75.0)
        assert m.expectancy == pytest.approx(37.5)
        assert m.expectancy_r == pytest.approx(0.375)

    def test_max_drawdown(self):
        m = compute_metrics(self.result)
        assert m.max_drawdown_currency == pytest.approx(150.0)
        assert m.max_drawdown_pct == pytest.approx(150.0 / 10_300)

    def test_streaks(self):
        m = compute_metrics(self.result)
        assert m.longest_winning_streak == 2
        assert m.longest_losing_streak == 2

    def test_net_return(self):
        m = compute_metrics(self.result)
        assert m.net_return_pct == pytest.approx(0.015)

    def test_no_trades_returns_zeroed_metrics_not_error(self):
        empty_result = BacktestResult(
            trades=[],
            equity_curve=pd.DataFrame({"time": [pd.Timestamp("2024-01-01", tz="UTC")], "equity": [10_000]}),
            config=BacktestConfig(initial_balance=10_000),
            final_balance=10_000,
        )
        m = compute_metrics(empty_result)
        assert m.total_trades == 0
        assert m.win_rate == 0.0
        assert m.profit_factor == 0.0
