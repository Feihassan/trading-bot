"""
Walk-forward windowing (spec section 17) and walk-forward backtesting.

Design decisions:
- Windows are defined in bar counts, not calendar dates. Forex data has
  irregular gaps (weekends, holidays), so "train on January-June" is
  ambiguous in bar terms; "train on the next 4000 H1 bars" is not. Callers
  who want calendar-aligned windows can convert dates to bar indices first
  using the DataFrame's own `time` column.
- Splitting is strictly chronological and never shuffled - each test
  window starts exactly where its train window ends, and successive
  windows only move forward. This is the actual leakage boundary that
  matters (spec section 9): a model/strategy "fit" on a train window must
  never see any bar from its own or a later test window.
- `mode="rolling"` (fixed-size train window sliding forward) vs
  `mode="expanding"` (train window grows, always starting from bar 0) are
  both supported - expanding is closer to "how much history would
  actually have been available live", rolling tests robustness to regime
  drift by discarding old data.
- This same windowing function is reused, unmodified, by the ML trainer's
  walk-forward validation in Phase 4 - a rule-based strategy and a model
  fit/predict cycle have the same leakage-prevention requirement, so they
  share one implementation rather than two.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator, Literal

import pandas as pd

from app.backtesting.engine import BacktestConfig, BacktestResult, StrategyFunc, run_backtest
from app.backtesting.metrics import BacktestMetrics, compute_metrics
from app.mt5.market_data import SymbolSpec

WalkForwardMode = Literal["rolling", "expanding"]


@dataclass
class WalkForwardWindow:
    window_index: int
    train_df: pd.DataFrame
    test_df: pd.DataFrame
    train_start_time: pd.Timestamp
    train_end_time: pd.Timestamp
    test_start_time: pd.Timestamp
    test_end_time: pd.Timestamp


def generate_walk_forward_windows(
    df: pd.DataFrame,
    train_bars: int,
    test_bars: int,
    step_bars: int | None = None,
    mode: WalkForwardMode = "rolling",
) -> Iterator[WalkForwardWindow]:
    df = df.reset_index(drop=True)
    n = len(df)
    step = step_bars or test_bars
    if step <= 0 or train_bars <= 0 or test_bars <= 0:
        raise ValueError("train_bars, test_bars, and step_bars must all be positive")

    window_index = 0
    test_start = train_bars
    while test_start + test_bars <= n:
        train_start = 0 if mode == "expanding" else test_start - train_bars
        train_df = df.iloc[train_start:test_start]
        test_df = df.iloc[test_start : test_start + test_bars]

        yield WalkForwardWindow(
            window_index=window_index,
            train_df=train_df,
            test_df=test_df,
            train_start_time=train_df["time"].iloc[0],
            train_end_time=train_df["time"].iloc[-1],
            test_start_time=test_df["time"].iloc[0],
            test_end_time=test_df["time"].iloc[-1],
        )

        window_index += 1
        test_start += step


@dataclass
class WalkForwardResult:
    window: WalkForwardWindow
    backtest_result: BacktestResult
    metrics: BacktestMetrics


StrategyFactory = Callable[[pd.DataFrame], StrategyFunc]
"""Given a train_df, return a strategy_fn ready to evaluate on unseen
bars. For a fixed rule-based strategy this can just ignore train_df and
return the same function every time; for a fitted model (Phase 4) this is
where .fit() happens, strictly on train_df only."""


def run_walk_forward_backtest(
    df: pd.DataFrame,
    strategy_factory: StrategyFactory,
    spec: SymbolSpec,
    train_bars: int,
    test_bars: int,
    step_bars: int | None = None,
    mode: WalkForwardMode = "rolling",
    config: BacktestConfig | None = None,
    warmup_bars: int = 200,
) -> list[WalkForwardResult]:
    results: list[WalkForwardResult] = []
    for window in generate_walk_forward_windows(df, train_bars, test_bars, step_bars, mode):
        strategy_fn = strategy_factory(window.train_df)
        # Reset warmup relative to the test window: the strategy still
        # only ever sees bars up to and including the current one within
        # test_df, exactly as run_backtest enforces.
        bt_result = run_backtest(
            window.test_df, strategy_fn, spec, config=config, warmup_bars=min(warmup_bars, len(window.test_df) - 1)
        )
        results.append(
            WalkForwardResult(window=window, backtest_result=bt_result, metrics=compute_metrics(bt_result))
        )
    return results
