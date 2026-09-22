from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.ai.features import FEATURE_COLUMNS, build_feature_matrix, compute_forward_labels, prepare_training_data
from tests._synthetic import make_trending_candles


class TestBuildFeatureMatrix:
    def test_produces_exactly_feature_columns(self):
        X = build_feature_matrix(make_trending_candles(n=400))
        assert list(X.columns) == FEATURE_COLUMNS

    def test_same_row_count_as_input(self):
        df = make_trending_candles(n=400)
        X = build_feature_matrix(df)
        assert len(X) == len(df)

    def test_one_hot_columns_are_mutually_exclusive_per_row(self):
        X = build_feature_matrix(make_trending_candles(n=400))
        trend_cols = [c for c in X.columns if c.startswith("trend_")]
        valid_rows = X.dropna()
        row_sums = valid_rows[trend_cols].sum(axis=1)
        assert (row_sums == 1.0).all()


class TestNoLookaheadInFeatures:
    @pytest.mark.parametrize("cut", [250, 300, 350])
    def test_feature_value_stable_under_truncation(self, cut):
        full = make_trending_candles(n=400)
        X_full = build_feature_matrix(full)
        X_trunc = build_feature_matrix(full.iloc[: cut + 1])

        for col in FEATURE_COLUMNS:
            v_full = X_full[col].iloc[cut]
            v_trunc = X_trunc[col].iloc[cut]
            if pd.isna(v_full) and pd.isna(v_trunc):
                continue
            assert v_full == pytest.approx(v_trunc, rel=1e-9, abs=1e-9), f"Look-ahead bias in feature '{col}' at row {cut}"


class TestComputeForwardLabels:
    def test_tail_rows_are_nan(self):
        df = make_trending_candles(n=300)
        labels = compute_forward_labels(df, horizon_bars=8)
        assert labels.iloc[-8:].isna().all()
        assert labels.iloc[:-8].notna().all()

    def test_strong_up_move_labeled_buy(self):
        n = 250
        closes = np.concatenate([np.full(50, 1.1000), np.linspace(1.1000, 1.1000 + 0.01, n - 50)])
        df = _flat_then_rising(n, closes)
        labels = compute_forward_labels(df, horizon_bars=5, atr_multiple=0.5)
        # Somewhere in the middle of the rising section, a clear uptrend should be labeled BUY.
        mid_section = labels.iloc[100:150].dropna()
        assert (mid_section == "BUY").sum() > 0

    def test_flat_market_labeled_neutral(self):
        n = 250
        closes = np.full(n, 1.1000)
        df = _flat_then_rising(n, closes)
        labels = compute_forward_labels(df, horizon_bars=5, atr_multiple=1.0)
        valid = labels.dropna()
        assert (valid == "NEUTRAL").all()


def _flat_then_rising(n: int, closes: np.ndarray) -> pd.DataFrame:
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


class TestPrepareTrainingData:
    def test_drops_warmup_and_tail_rows(self):
        df = make_trending_candles(n=400)
        horizon = 8
        X, y = prepare_training_data(df, horizon_bars=horizon)
        assert len(X) == len(y)
        assert len(X) < len(df) - horizon + 1  # warmup (ema_200) also drops rows
        assert set(y.unique()).issubset({"BUY", "SELL", "NEUTRAL"})

    def test_no_nan_in_output(self):
        df = make_trending_candles(n=400)
        X, y = prepare_training_data(df, horizon_bars=8)
        assert not X.isna().any().any()
        assert not y.isna().any()
