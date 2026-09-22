"""
Performance metrics computed from a BacktestResult.

Design decision: metrics are computed from the trade list and the realized
equity curve, never re-derived by re-running the strategy - keeping this
module a pure function of already-simulated results avoids any chance of
metrics disagreeing with what the engine actually simulated.

We deliberately report BOTH win rate and profit factor/expectancy
together, and never optimize or recommend based on win rate alone (spec
section 16: "Do not optimize solely for win rate") - a high win rate with
a poor profit factor is a strategy that loses money slowly with
occasional large losses, which is exactly what these paired metrics expose.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.backtesting.engine import BacktestResult, Trade


@dataclass
class BacktestMetrics:
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    expectancy: float
    expectancy_r: float
    max_drawdown_pct: float
    max_drawdown_currency: float
    sharpe_ratio: float | None
    net_return_pct: float
    longest_losing_streak: int
    longest_winning_streak: int
    avg_trade_duration_bars: float


def _streaks(trades: list[Trade]) -> tuple[int, int]:
    longest_loss, longest_win = 0, 0
    cur_loss, cur_win = 0, 0
    for t in trades:
        if t.pnl < 0:
            cur_loss += 1
            cur_win = 0
        elif t.pnl > 0:
            cur_win += 1
            cur_loss = 0
        else:
            cur_win = cur_loss = 0
        longest_loss = max(longest_loss, cur_loss)
        longest_win = max(longest_win, cur_win)
    return longest_loss, longest_win


def _max_drawdown(equity_curve: pd.DataFrame) -> tuple[float, float]:
    equity = equity_curve["equity"].to_numpy()
    if len(equity) == 0:
        return 0.0, 0.0
    running_max = np.maximum.accumulate(equity)
    drawdown_currency = running_max - equity
    with np.errstate(divide="ignore", invalid="ignore"):
        drawdown_pct = np.where(running_max > 0, drawdown_currency / running_max, 0.0)
    return float(drawdown_pct.max()), float(drawdown_currency.max())


def _sharpe_ratio(equity_curve: pd.DataFrame, periods_per_year: int = 252) -> float | None:
    """
    Sharpe computed on daily-resampled equity returns (last equity value
    per calendar day), annualized assuming ~252 trading days/year. Returns
    None (rather than a misleading number) if there's too little data to
    estimate a meaningful volatility.
    """
    if len(equity_curve) < 3:
        return None
    curve = equity_curve.copy()
    curve["time"] = pd.to_datetime(curve["time"])
    daily = curve.set_index("time")["equity"].resample("1D").last().dropna()
    returns = daily.pct_change().dropna()
    if len(returns) < 5 or returns.std() == 0:
        return None
    return float(returns.mean() / returns.std() * np.sqrt(periods_per_year))


def compute_metrics(result: BacktestResult) -> BacktestMetrics:
    trades = result.trades
    total = len(trades)

    if total == 0:
        return BacktestMetrics(
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            profit_factor=0.0,
            expectancy=0.0,
            expectancy_r=0.0,
            max_drawdown_pct=0.0,
            max_drawdown_currency=0.0,
            sharpe_ratio=None,
            net_return_pct=0.0,
            longest_losing_streak=0,
            longest_winning_streak=0,
            avg_trade_duration_bars=0.0,
        )

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]

    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))

    win_rate = len(wins) / total
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0
    expectancy = sum(t.pnl for t in trades) / total
    expectancy_r = sum(t.r_multiple for t in trades) / total

    max_dd_pct, max_dd_currency = _max_drawdown(result.equity_curve)
    sharpe = _sharpe_ratio(result.equity_curve)
    net_return_pct = (result.final_balance / result.config.initial_balance) - 1.0
    longest_loss_streak, longest_win_streak = _streaks(trades)
    avg_duration = sum(t.duration_bars for t in trades) / total

    return BacktestMetrics(
        total_trades=total,
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        expectancy=expectancy,
        expectancy_r=expectancy_r,
        max_drawdown_pct=max_dd_pct,
        max_drawdown_currency=max_dd_currency,
        sharpe_ratio=sharpe,
        net_return_pct=net_return_pct,
        longest_losing_streak=longest_loss_streak,
        longest_winning_streak=longest_win_streak,
        avg_trade_duration_bars=avg_duration,
    )
