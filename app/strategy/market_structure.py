"""
Market structure analysis: swing highs/lows, HH/HL/LH/LL sequences, break
of structure, trend direction, consolidation, and support/resistance.

Design decisions - confirmation lag is the central issue here:
- A swing high/low ("fractal") at bar i is only identifiable once we can
  see `order` bars AFTER it too (i.e. high[i] is a swing high if it's the
  max of the window [i-order, i+order]). That means a swing point at index
  i is not KNOWN until index i+order.
- To make this leakage-safe by construction, `detect_swings` returns two
  kinds of columns: `swing_high`/`swing_low` (marked at the bar where the
  swing actually occurred, for charting/backtesting-with-hindsight) and
  `swing_high_confirmed_at`/`swing_low_confirmed_at` (the index at which
  that swing point becomes knowable). Every other function in this module
  that reports "the current trend" or "the nearest resistance" uses only
  confirmed information as of each row - never a swing point whose
  confirmation index is in the future relative to that row. This is what
  lets `market_structure` be reused unmodified for both live signals and
  historical backtesting (Phase 3) without look-ahead bias.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class TrendDirection(str, Enum):
    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    SIDEWAYS = "SIDEWAYS"
    UNKNOWN = "UNKNOWN"


def detect_swings(df: pd.DataFrame, order: int = 2) -> pd.DataFrame:
    """
    Mark fractal swing highs/lows using a symmetric `order`-bar window.

    Adds:
        swing_high, swing_low: bool, True at the bar the swing occurred
        swing_high_confirmed_at, swing_low_confirmed_at: int index (row
            position) at which the swing becomes knowable (NaN if none)
    """
    df = df.reset_index(drop=True).copy()
    n = len(df)
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()

    swing_high = np.zeros(n, dtype=bool)
    swing_low = np.zeros(n, dtype=bool)

    for i in range(order, n - order):
        window_high = high[i - order : i + order + 1]
        window_low = low[i - order : i + order + 1]
        if high[i] == window_high.max() and np.argmax(window_high) == order:
            swing_high[i] = True
        if low[i] == window_low.min() and np.argmin(window_low) == order:
            swing_low[i] = True

    df["swing_high"] = swing_high
    df["swing_low"] = swing_low
    df["swing_high_confirmed_at"] = np.where(swing_high, np.arange(n) + order, np.nan)
    df["swing_low_confirmed_at"] = np.where(swing_low, np.arange(n) + order, np.nan)
    return df


def _confirmed_swings(df: pd.DataFrame, as_of_idx: int, kind: str) -> pd.DataFrame:
    """Swings of `kind` ('high' or 'low') that are confirmed by as_of_idx."""
    confirmed_col = f"swing_{kind}_confirmed_at"
    mask = df[f"swing_{kind}"] & (df[confirmed_col] <= as_of_idx)
    return df.loc[mask]


def trend_from_structure(df: pd.DataFrame, lookback_swings: int = 4) -> pd.Series:
    """
    For each bar, classify trend direction from the sequence of the most
    recently CONFIRMED swing highs/lows as of that bar:
        - higher highs AND higher lows -> UPTREND
        - lower highs AND lower lows   -> DOWNTREND
        - otherwise                    -> SIDEWAYS
    Requires `detect_swings` to have been run first.
    """
    n = len(df)
    result = [TrendDirection.UNKNOWN] * n

    highs_confirmed = df.index[df["swing_high"]].to_numpy()
    lows_confirmed = df.index[df["swing_low"]].to_numpy()
    high_confirm_at = df["swing_high_confirmed_at"].to_numpy()
    low_confirm_at = df["swing_low_confirmed_at"].to_numpy()

    for i in range(n):
        avail_highs = [h for h in highs_confirmed if high_confirm_at[h] <= i]
        avail_lows = [l for l in lows_confirmed if low_confirm_at[l] <= i]
        recent_highs = avail_highs[-lookback_swings:]
        recent_lows = avail_lows[-lookback_swings:]

        if len(recent_highs) < 2 or len(recent_lows) < 2:
            result[i] = TrendDirection.UNKNOWN
            continue

        high_vals = df["high"].iloc[recent_highs].to_numpy()
        low_vals = df["low"].iloc[recent_lows].to_numpy()

        making_higher_highs = high_vals[-1] > high_vals[-2]
        making_higher_lows = low_vals[-1] > low_vals[-2]
        making_lower_highs = high_vals[-1] < high_vals[-2]
        making_lower_lows = low_vals[-1] < low_vals[-2]

        if making_higher_highs and making_higher_lows:
            result[i] = TrendDirection.UPTREND
        elif making_lower_highs and making_lower_lows:
            result[i] = TrendDirection.DOWNTREND
        else:
            result[i] = TrendDirection.SIDEWAYS

    return pd.Series([t.value for t in result], index=df.index, name="structure_trend")


def detect_break_of_structure(df: pd.DataFrame) -> pd.DataFrame:
    """
    Bullish BOS: close breaks above the most recent CONFIRMED swing high.
    Bearish BOS: close breaks below the most recent CONFIRMED swing low.
    Requires `detect_swings` to have been run first.
    """
    df = df.copy()
    n = len(df)
    bos_bullish = np.zeros(n, dtype=bool)
    bos_bearish = np.zeros(n, dtype=bool)

    last_confirmed_high = np.nan
    last_confirmed_low = np.nan
    high_confirm_at = df["swing_high_confirmed_at"].to_numpy()
    low_confirm_at = df["swing_low_confirmed_at"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()

    for i in range(n):
        # Pull in any swing that becomes confirmed exactly at this bar.
        confirmed_high_idx = np.where(high_confirm_at == i)[0]
        confirmed_low_idx = np.where(low_confirm_at == i)[0]
        if len(confirmed_high_idx):
            last_confirmed_high = highs[confirmed_high_idx[-1]]
        if len(confirmed_low_idx):
            last_confirmed_low = lows[confirmed_low_idx[-1]]

        if not np.isnan(last_confirmed_high) and closes[i] > last_confirmed_high:
            bos_bullish[i] = True
        if not np.isnan(last_confirmed_low) and closes[i] < last_confirmed_low:
            bos_bearish[i] = True

    df["bos_bullish"] = bos_bullish
    df["bos_bearish"] = bos_bearish
    return df


def is_consolidating(df: pd.DataFrame, atr_col: str = "atr", window: int = 20, threshold: float = 0.6) -> pd.Series:
    """
    Flags consolidation when the recent price range is small relative to
    ATR - a rolling `window`-bar high-low range under `threshold` * ATR
    (summed) suggests price is chopping rather than trending.
    """
    rolling_range = df["high"].rolling(window=window, min_periods=window).max() - df["low"].rolling(
        window=window, min_periods=window
    ).min()
    atr_sum_proxy = df[atr_col] * (window**0.5)  # rough expected range under trending conditions
    return (rolling_range < threshold * atr_sum_proxy).rename("is_consolidating")


@dataclass
class SupportResistance:
    support: float | None
    resistance: float | None
    support_atr_zone: tuple[float, float] | None
    resistance_atr_zone: tuple[float, float] | None


def nearest_support_resistance(df: pd.DataFrame, as_of_idx: int, atr_multiple: float = 0.5) -> SupportResistance:
    """
    Nearest confirmed swing low (support) / swing high (resistance) as of
    `as_of_idx`, each padded into a zone of +/- atr_multiple * ATR.
    """
    confirmed_lows = _confirmed_swings(df, as_of_idx, "low")
    confirmed_highs = _confirmed_swings(df, as_of_idx, "high")

    current_price = df["close"].iloc[as_of_idx]
    atr = df["atr"].iloc[as_of_idx] if "atr" in df.columns else np.nan

    support = None
    resistance = None
    if not confirmed_lows.empty:
        below = confirmed_lows[confirmed_lows["low"] <= current_price]
        support = float(below["low"].max()) if not below.empty else float(confirmed_lows["low"].iloc[-1])
    if not confirmed_highs.empty:
        above = confirmed_highs[confirmed_highs["high"] >= current_price]
        resistance = float(above["high"].min()) if not above.empty else float(confirmed_highs["high"].iloc[-1])

    support_zone = (support - atr_multiple * atr, support + atr_multiple * atr) if support and not np.isnan(atr) else None
    resistance_zone = (
        (resistance - atr_multiple * atr, resistance + atr_multiple * atr) if resistance and not np.isnan(atr) else None
    )

    return SupportResistance(
        support=support,
        resistance=resistance,
        support_atr_zone=support_zone,
        resistance_atr_zone=resistance_zone,
    )


def analyze_structure(df: pd.DataFrame, swing_order: int = 2) -> pd.DataFrame:
    """Convenience: run the full structure pipeline (swings, trend, BOS)."""
    df = detect_swings(df, order=swing_order)
    df["structure_trend"] = trend_from_structure(df).values
    df = detect_break_of_structure(df)
    return df
