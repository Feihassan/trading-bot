"""
Market regime classification.

Design decisions:
- Regime is derived purely from already-causal indicator columns (ema_20/
  50/200, adx, atr) that `app.strategy.indicators.add_all_indicators` has
  computed - so this module inherits the same no-look-ahead guarantee:
  the regime at row t depends only on rows <= t.
- Volatility is evaluated first and can override the trend classification:
  a market in a high-volatility spike is flagged HIGH_VOLATILITY regardless
  of trend, because trade setups should be sized/filtered differently in
  that regime no matter what the EMAs say (spec section 7).
- ATR "percentile" is a rolling rank against the ATR's own recent history
  (not a fixed pip threshold), so this works unmodified across symbols
  with very different volatility scales (e.g. EURUSD vs XAUUSD).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class MarketRegime(str, Enum):
    STRONG_BULLISH_TREND = "STRONG_BULLISH_TREND"
    WEAK_BULLISH_TREND = "WEAK_BULLISH_TREND"
    STRONG_BEARISH_TREND = "STRONG_BEARISH_TREND"
    WEAK_BEARISH_TREND = "WEAK_BEARISH_TREND"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    UNKNOWN = "UNKNOWN"


@dataclass
class RegimeThresholds:
    adx_range_max: float = 20.0
    adx_strong_min: float = 30.0
    atr_percentile_window: int = 100
    atr_high_percentile: float = 0.90
    atr_low_percentile: float = 0.10


def _atr_percentile(df: pd.DataFrame, window: int) -> pd.Series:
    """Rolling percentile rank (0-1) of the current ATR within its own
    trailing `window`-bar history - causal by construction (each rank is
    computed only from that row and the `window` rows before it)."""

    def rank_last(x: np.ndarray) -> float:
        return (x < x[-1]).sum() / (len(x) - 1) if len(x) > 1 else np.nan

    return df["atr"].rolling(window=window, min_periods=max(10, window // 4)).apply(rank_last, raw=True)


def classify_regime(df: pd.DataFrame, thresholds: RegimeThresholds | None = None) -> pd.DataFrame:
    """
    Requires df to already have ema_20, ema_50, ema_200, adx, atr columns
    (see app.strategy.indicators.add_all_indicators). Adds:
        atr_percentile: float 0-1
        regime: MarketRegime value per bar
    """
    th = thresholds or RegimeThresholds()
    df = df.copy()
    df["atr_percentile"] = _atr_percentile(df, th.atr_percentile_window)

    regimes: list[str] = []
    for _, row in df.iterrows():
        regimes.append(_classify_row(row, th).value)
    df["regime"] = regimes
    return df


def _classify_row(row: pd.Series, th: RegimeThresholds) -> MarketRegime:
    ema_20, ema_50, ema_200, adx, atr_pct, close = (
        row.get("ema_20"),
        row.get("ema_50"),
        row.get("ema_200"),
        row.get("adx"),
        row.get("atr_percentile"),
        row.get("close"),
    )

    if any(pd.isna(v) for v in (ema_20, ema_50, ema_200, adx, close)):
        return MarketRegime.UNKNOWN

    if not pd.isna(atr_pct):
        if atr_pct >= th.atr_high_percentile:
            return MarketRegime.HIGH_VOLATILITY
        if atr_pct <= th.atr_low_percentile:
            return MarketRegime.LOW_VOLATILITY

    bullish_aligned = ema_20 > ema_50 > ema_200 and close > ema_20
    bearish_aligned = ema_20 < ema_50 < ema_200 and close < ema_20

    if adx < th.adx_range_max:
        return MarketRegime.RANGE

    if bullish_aligned:
        return MarketRegime.STRONG_BULLISH_TREND if adx >= th.adx_strong_min else MarketRegime.WEAK_BULLISH_TREND
    if bearish_aligned:
        return MarketRegime.STRONG_BEARISH_TREND if adx >= th.adx_strong_min else MarketRegime.WEAK_BEARISH_TREND

    return MarketRegime.RANGE


def regime_allows_trend_following(regime: MarketRegime | str) -> bool:
    """
    Whether trend-following setups should be considered at all in this
    regime (spec section 7: avoid/reduce trend-following during strong
    consolidation). Mean-reversion/breakout strategies may use the
    opposite rule - this only governs trend-following logic.
    """
    regime = MarketRegime(regime) if not isinstance(regime, MarketRegime) else regime
    return regime in (
        MarketRegime.STRONG_BULLISH_TREND,
        MarketRegime.WEAK_BULLISH_TREND,
        MarketRegime.STRONG_BEARISH_TREND,
        MarketRegime.WEAK_BEARISH_TREND,
    )
