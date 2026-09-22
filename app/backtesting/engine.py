"""
Bar-by-bar backtesting engine.

Design decisions:
- The engine is strategy-agnostic: it takes a `strategy_fn(df_so_far) ->
  TradeSignal | None` callable. `df_so_far` is `df.iloc[:i+1]` - everything
  up to and including the current bar, never beyond it - so any strategy
  plugged in here (the Phase-2 indicator/structure/regime pipeline today,
  the ML-driven signal engine from Phase 5 later) is mechanically
  prevented from seeing future bars during a backtest. Indicator/structure
  columns may be precomputed once over the full DataFrame before the loop
  starts purely as a performance optimization - this is safe *only*
  because Phase 2's indicators are proven causal (see
  tests/test_indicators.py::TestNoLookaheadBias): a precomputed value at
  row i is identical to one computed on data truncated at i.
- Position sizing reuses `app.risk.position_sizing` verbatim - the same
  function the live execution engine will call in Phase 6. If a proposed
  trade's minimum viable lot size would risk meaningfully more than
  `risk_per_trade`, the trade is skipped, not silently oversized.
- Every signal is required to already carry a stop loss and take profit;
  trades below `min_risk_reward` are rejected before sizing is even
  attempted (spec section 12: SL/TP are mandatory, minimum R:R enforced).
- One open position at a time (per symbol) in this version - multi-symbol
  portfolio-level exposure limits belong to the live risk manager
  (Phase 5/6), not this single-symbol backtester.

Known simplifications (documented rather than hidden):
- No intrabar tick path: if a single bar's range would hit both the stop
  loss and take profit, the stop loss is assumed to fill first. This is
  the conservative assumption (never overstates edge).
- Spread cost is charged once per round trip, using the entry bar's
  historical `spread` column (real historical spread from MT5, not a
  guess), rather than simulating separate bid/ask series.
- The equity curve updates only when a trade closes (realized equity),
  not on unrealized intrabar mark-to-market - so drawdown here reflects
  swings between closed trades, not worst-case intrabar excursion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

import pandas as pd

from app.mt5.market_data import SymbolSpec
from app.risk.position_sizing import calculate_position_size

Direction = Literal["BUY", "SELL"]


@dataclass
class TradeSignal:
    direction: Direction
    entry: float
    stop_loss: float
    take_profit: float
    reason: str = ""


StrategyFunc = Callable[[pd.DataFrame], "TradeSignal | None"]


@dataclass
class Trade:
    direction: Direction
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    stop_loss: float
    take_profit: float
    volume: float
    pnl: float
    r_multiple: float
    exit_reason: str
    duration_bars: int
    reason: str = ""


@dataclass
class BacktestConfig:
    initial_balance: float = 10_000.0
    risk_per_trade: float = 0.01
    min_risk_reward: float = 2.0
    max_holding_bars: int | None = None
    max_acceptable_risk_multiple: float = 1.5


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: pd.DataFrame  # columns: time, equity
    config: BacktestConfig = field(repr=False)
    final_balance: float = 0.0
    skipped_signals: int = 0


def _reward_risk_ratio(signal: TradeSignal) -> float:
    risk = abs(signal.entry - signal.stop_loss)
    reward = abs(signal.take_profit - signal.entry)
    if risk == 0:
        return 0.0
    return reward / risk


def _price_diff_in_favor(direction: Direction, entry: float, exit_price: float) -> float:
    return (exit_price - entry) if direction == "BUY" else (entry - exit_price)


def _price_to_currency(price_diff: float, volume: float, spec: SymbolSpec) -> float:
    ticks = price_diff / spec.trade_tick_size
    return ticks * spec.trade_tick_value * volume


def _check_exit(direction: Direction, bar: pd.Series, stop_loss: float, take_profit: float) -> str | None:
    hit_sl = bar["low"] <= stop_loss if direction == "BUY" else bar["high"] >= stop_loss
    hit_tp = bar["high"] >= take_profit if direction == "BUY" else bar["low"] <= take_profit
    if hit_sl and hit_tp:
        return "SL"  # conservative: assume stop loss fills first when ambiguous
    if hit_sl:
        return "SL"
    if hit_tp:
        return "TP"
    return None


def run_backtest(
    df: pd.DataFrame,
    strategy_fn: StrategyFunc,
    spec: SymbolSpec,
    config: BacktestConfig | None = None,
    warmup_bars: int = 200,
) -> BacktestResult:
    """
    df: OHLCV (+ any precomputed indicator/structure/regime columns),
        ascending by time, as returned by app.mt5.market_data.get_candles*.
    strategy_fn: called once per bar (after warmup_bars) with data up to
        and including that bar; returns a TradeSignal to attempt to open,
        or None to stay flat.
    spec: SymbolSpec for position sizing (contract/tick specs).
    warmup_bars: bars to skip before evaluating any signal, so indicators
        needing history (e.g. ema_200) aren't evaluated while still NaN.
    """
    config = config or BacktestConfig()
    df = df.reset_index(drop=True)
    n = len(df)

    equity = config.initial_balance
    equity_curve_rows = [{"time": df["time"].iloc[0], "equity": equity}]
    trades: list[Trade] = []
    skipped_signals = 0

    open_trade: dict | None = None  # in-progress position state

    for i in range(warmup_bars, n):
        bar = df.iloc[i]

        if open_trade is not None:
            exit_kind = _check_exit(open_trade["direction"], bar, open_trade["stop_loss"], open_trade["take_profit"])
            held_bars = i - open_trade["entry_index"]
            forced_exit = config.max_holding_bars is not None and held_bars >= config.max_holding_bars

            if exit_kind is not None or forced_exit or i == n - 1:
                if exit_kind == "SL":
                    exit_price = open_trade["stop_loss"]
                    reason = "SL"
                elif exit_kind == "TP":
                    exit_price = open_trade["take_profit"]
                    reason = "TP"
                else:
                    exit_price = bar["close"]
                    reason = "MAX_HOLD" if forced_exit else "END_OF_DATA"

                price_diff = _price_diff_in_favor(open_trade["direction"], open_trade["entry_price"], exit_price)
                gross_pnl = _price_to_currency(price_diff, open_trade["volume"], spec)
                net_pnl = gross_pnl - open_trade["spread_cost"]
                risk_amount = open_trade["risk_amount"]
                r_multiple = net_pnl / risk_amount if risk_amount > 0 else 0.0

                equity += net_pnl
                trades.append(
                    Trade(
                        direction=open_trade["direction"],
                        entry_time=open_trade["entry_time"],
                        entry_price=open_trade["entry_price"],
                        exit_time=bar["time"],
                        exit_price=exit_price,
                        stop_loss=open_trade["stop_loss"],
                        take_profit=open_trade["take_profit"],
                        volume=open_trade["volume"],
                        pnl=net_pnl,
                        r_multiple=r_multiple,
                        exit_reason=reason,
                        duration_bars=held_bars,
                        reason=open_trade["reason"],
                    )
                )
                equity_curve_rows.append({"time": bar["time"], "equity": equity})
                open_trade = None
            continue

        signal = strategy_fn(df.iloc[: i + 1])
        if signal is None:
            continue

        if _reward_risk_ratio(signal) < config.min_risk_reward:
            skipped_signals += 1
            continue

        sizing = calculate_position_size(
            equity=equity,
            risk_per_trade=config.risk_per_trade,
            entry_price=signal.entry,
            stop_loss_price=signal.stop_loss,
            spec=spec,
            max_acceptable_risk_multiple=config.max_acceptable_risk_multiple,
        )
        if sizing.volume <= 0:
            skipped_signals += 1
            continue

        spread_points = bar["spread"] if "spread" in df.columns else 0
        spread_price = spread_points * spec.point
        spread_cost = _price_to_currency(spread_price, sizing.volume, spec)

        open_trade = {
            "direction": signal.direction,
            "entry_time": bar["time"],
            "entry_price": signal.entry,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "volume": sizing.volume,
            "risk_amount": sizing.risk_amount_actual,
            "spread_cost": spread_cost,
            "entry_index": i,
            "reason": signal.reason,
        }

    return BacktestResult(
        trades=trades,
        equity_curve=pd.DataFrame(equity_curve_rows),
        config=config,
        final_balance=equity,
        skipped_signals=skipped_signals,
    )
