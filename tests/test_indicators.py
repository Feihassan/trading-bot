from __future__ import annotations

import numpy as np
import pytest

from app.strategy.indicators import add_all_indicators, add_rsi
from tests._synthetic import make_ranging_candles, make_trending_candles


class TestIndicatorsProduceExpectedColumns:
    def test_all_indicators_present(self):
        df = add_all_indicators(make_trending_candles())
        expected = {
            "ema_20", "ema_50", "ema_200", "rsi", "macd", "macd_signal", "macd_diff",
            "atr", "adx", "di_plus", "di_minus", "bb_upper", "bb_middle", "bb_lower",
            "bb_pct", "stoch_k", "stoch_d", "volume_sma", "volume_ratio",
            "recent_high", "recent_low",
        }
        assert expected.issubset(df.columns)

    def test_rsi_bounded_0_100(self):
        df = add_rsi(make_trending_candles())
        valid = df["rsi"].dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_warmup_rows_are_nan(self):
        df = add_all_indicators(make_trending_candles(n=300))
        # ema_200 cannot be defined before 200 bars of history
        assert df["ema_200"].iloc[:199].isna().all()
        assert df["ema_200"].iloc[199:].notna().all()


class TestNoLookaheadBias:
    """
    The critical property: an indicator value at row t must be identical
    whether it was computed on the full series or on a series truncated
    to end at row t. If this fails, the indicator is peeking at future
    bars - which would silently leak into both live signals and, worse,
    ML training features (Phase 4).
    """

    @pytest.mark.parametrize("cut", [100, 150, 250])
    def test_indicator_value_stable_under_truncation(self, cut):
        full = make_trending_candles(n=300)
        df_full = add_all_indicators(full)
        df_truncated = add_all_indicators(full.iloc[: cut + 1])

        columns_to_check = ["ema_20", "ema_50", "rsi", "macd", "atr", "adx", "bb_upper", "stoch_k"]
        for col in columns_to_check:
            full_val = df_full[col].iloc[cut]
            trunc_val = df_truncated[col].iloc[cut]
            if np.isnan(full_val) and np.isnan(trunc_val):
                continue
            assert full_val == pytest.approx(trunc_val, rel=1e-9, abs=1e-9), (
                f"Look-ahead bias detected in '{col}' at row {cut}: "
                f"full-series value {full_val} != truncated-series value {trunc_val}"
            )


class TestRangingVsTrending:
    def test_ranging_data_has_lower_adx_than_trending(self):
        trend_df = add_all_indicators(make_trending_candles())
        range_df = add_all_indicators(make_ranging_candles())
        trend_adx = trend_df["adx"].dropna().iloc[-50:].mean()
        range_adx = range_df["adx"].dropna().iloc[-50:].mean()
        assert trend_adx > range_adx
