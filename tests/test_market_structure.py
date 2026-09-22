from __future__ import annotations

import numpy as np
import pandas as pd

from app.strategy.indicators import add_atr
from app.strategy.market_structure import (
    TrendDirection,
    analyze_structure,
    detect_swings,
    nearest_support_resistance,
)
from tests._synthetic import make_ranging_candles, make_trending_candles


def make_staircase_candles(n_steps: int = 6, step_bars: int = 10) -> pd.DataFrame:
    """A deterministic zig-zag that makes clear, unambiguous higher-highs/
    higher-lows so swing/trend detection has an exact expected answer."""
    closes = []
    price = 1.1000
    for step in range(n_steps):
        for i in range(step_bars):
            price += 0.0006
            closes.append(price)
        for i in range(step_bars // 2):
            price -= 0.0002
            closes.append(price)
    closes = np.array(closes)
    n = len(closes)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) + 0.00005
    lows = np.minimum(opens, closes) - 0.00005
    times = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "time": times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "tick_volume": np.full(n, 100.0),
            "spread": np.ones(n, dtype=int),
            "real_volume": np.zeros(n),
        }
    )


class TestSwingDetection:
    def test_finds_swing_highs_and_lows_in_staircase(self):
        df = detect_swings(make_staircase_candles(), order=2)
        assert df["swing_high"].sum() >= 4
        assert df["swing_low"].sum() >= 4

    def test_confirmation_index_is_after_formation_index(self):
        df = detect_swings(make_staircase_candles(), order=3)
        confirmed = df[df["swing_high"]]
        for idx, row in confirmed.iterrows():
            assert row["swing_high_confirmed_at"] == idx + 3


class TestTrendClassification:
    def test_staircase_is_classified_uptrend(self):
        df = analyze_structure(make_staircase_candles(n_steps=8, step_bars=10), swing_order=2)
        late_trend = df["structure_trend"].iloc[-10:]
        assert (late_trend == TrendDirection.UPTREND.value).sum() >= 5

    def test_downtrend_staircase_is_classified_downtrend(self):
        up_df = make_staircase_candles(n_steps=8, step_bars=10)
        # Mirror the staircase to build a downtrend from the same shape.
        base = up_df["close"].iloc[0]
        down = up_df.copy()
        for col in ["open", "high", "low", "close"]:
            down[col] = base - (up_df[col] - base)
        down["high"], down["low"] = (
            np.maximum(down["open"], down["close"]) + 0.00005,
            np.minimum(down["open"], down["close"]) - 0.00005,
        )
        df = analyze_structure(down, swing_order=2)
        late_trend = df["structure_trend"].iloc[-10:]
        assert (late_trend == TrendDirection.DOWNTREND.value).sum() >= 5


class TestBreakOfStructure:
    def test_bullish_bos_flagged_when_closing_above_prior_swing_high(self):
        df = analyze_structure(make_staircase_candles(n_steps=8, step_bars=10), swing_order=2)
        assert df["bos_bullish"].sum() > 0


class TestSupportResistance:
    def test_nearest_levels_bracket_current_price(self):
        # Ranging data (oscillating around a center) is used here rather
        # than a one-directional trend, since a persistent trend can push
        # price beyond every prior swing high/low, leaving no resistance
        # "above" or support "below" left to find - that's a real, valid
        # market state, just not one this bracket-check applies to.
        df = make_ranging_candles(n=200)
        df = add_atr(df)
        df = detect_swings(df, order=2)
        as_of = len(df) - 1
        sr = nearest_support_resistance(df, as_of)
        current_price = df["close"].iloc[as_of]
        if sr.support is not None:
            assert sr.support <= current_price + 1e-6
        if sr.resistance is not None:
            assert sr.resistance >= current_price - 1e-6
