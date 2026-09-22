"""
A minimal example strategy used to exercise and validate the backtesting
engine end-to-end.

This is NOT the production trading strategy. It exists purely so Phase 3
(the backtesting engine) can be tested and demonstrated against something
concrete before the ML-driven signal engine (Phase 4/5) exists. Its rule
is deliberately simple and interpretable: an EMA 20/50 crossover, taken
only in the direction of, and only while, the regime classifier (Phase 2)
says the market is trending - i.e. it already respects spec section 7
("trend-following setups should be avoided during consolidation").

SL/TP are ATR-based per spec section 11 (1.5x ATR stop, 3x ATR target -
a 1:2 R:R baseline), not fixed pip values.
"""
from __future__ import annotations

import pandas as pd

from app.backtesting.engine import TradeSignal
from app.strategy.indicators import add_all_indicators
from app.strategy.regime import MarketRegime, classify_regime

BULLISH_REGIMES = {MarketRegime.STRONG_BULLISH_TREND.value, MarketRegime.WEAK_BULLISH_TREND.value}
BEARISH_REGIMES = {MarketRegime.STRONG_BEARISH_TREND.value, MarketRegime.WEAK_BEARISH_TREND.value}


def prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    """Indicators + regime, precomputed once over the full history. Safe
    to precompute (rather than recompute per-bar in the backtest loop)
    because every column here is proven causal - see
    tests/test_indicators.py::TestNoLookaheadBias."""
    df = add_all_indicators(df)
    df = classify_regime(df)
    return df


def make_ema_crossover_strategy(atr_sl_multiple: float = 1.5, atr_tp_multiple: float = 3.0):
    def strategy_fn(df: pd.DataFrame) -> TradeSignal | None:
        if len(df) < 2:
            return None
        last, prev = df.iloc[-1], df.iloc[-2]

        required = ["ema_20", "ema_50", "atr", "regime"]
        if any(pd.isna(last[col]) for col in required) or any(pd.isna(prev[col]) for col in ["ema_20", "ema_50"]):
            return None

        atr = last["atr"]
        entry = last["close"]
        crossed_up = prev["ema_20"] <= prev["ema_50"] and last["ema_20"] > last["ema_50"]
        crossed_down = prev["ema_20"] >= prev["ema_50"] and last["ema_20"] < last["ema_50"]

        if crossed_up and last["regime"] in BULLISH_REGIMES:
            return TradeSignal(
                direction="BUY",
                entry=entry,
                stop_loss=entry - atr_sl_multiple * atr,
                take_profit=entry + atr_tp_multiple * atr,
                reason=f"EMA20/50 bullish cross in {last['regime']}",
            )
        if crossed_down and last["regime"] in BEARISH_REGIMES:
            return TradeSignal(
                direction="SELL",
                entry=entry,
                stop_loss=entry + atr_sl_multiple * atr,
                take_profit=entry - atr_tp_multiple * atr,
                reason=f"EMA20/50 bearish cross in {last['regime']}",
            )
        return None

    return strategy_fn
