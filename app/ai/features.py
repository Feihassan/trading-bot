"""
ML feature engineering and label construction.

Design decisions - this is the single most leakage-sensitive file in the
whole project, so read this before changing anything in it:

- `build_feature_matrix(df)` builds X (the inputs the model sees). Every
  column in it is derived from `app.strategy.indicators` /
  `market_structure` / `regime`, all of which are proven causal (a value
  at row i depends only on rows <= i - see
  tests/test_indicators.py::TestNoLookaheadBias). Nothing in this function
  is allowed to look forward. If you add a feature here, it must be
  expressible as "something you could have computed the instant bar i
  closed" - no shift(-k), no centered rolling windows, no using a swing
  point before its confirmation index.

- `compute_forward_labels(df)` builds y (the training target) and is the
  ONE function in this entire codebase that is deliberately allowed to
  look into the future - it has to, since "was this a good trade" is only
  knowable after the fact. It uses `close.shift(-horizon_bars)`. This
  function's output must NEVER be joined into the feature matrix, only
  ever used as the target passed to a model's `.fit()`. Keeping label
  construction physically separate from feature construction (different
  function, different module section) is what makes the leakage boundary
  visible in a code review rather than buried in one giant transform.

- `prepare_training_data(df)` is the only place these two are combined,
  and it does so by joining on index and dropping any row where either
  side is NaN - which naturally drops both the warm-up rows (indicators
  not yet defined) and the tail rows (label not yet knowable because
  there aren't `horizon_bars` future bars to look at yet). A test in
  tests/test_features.py asserts the last `horizon_bars` rows of the
  input never appear in the training data, specifically to guard against
  someone "fixing" the NaN tail with a fillna instead of a drop.

- Categorical columns (`structure_trend`, `regime`) are one-hot encoded
  against a FIXED, hardcoded set of categories (not `pd.get_dummies`'
  data-dependent column set), so a single-row prediction at inference
  time produces exactly the same columns, in the same order, as training
  did - a mismatched column set between train and predict is a classic
  silent bug in ML pipelines, not a leakage issue but just as capable of
  producing a garbage model.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.strategy.indicators import add_all_indicators
from app.strategy.market_structure import analyze_structure
from app.strategy.regime import MarketRegime, RegimeThresholds, classify_regime
from app.strategy.market_structure import TrendDirection

LABEL_CLASSES = ["SELL", "NEUTRAL", "BUY"]
LABEL_TO_INT = {name: i for i, name in enumerate(LABEL_CLASSES)}
INT_TO_LABEL = {i: name for name, i in LABEL_TO_INT.items()}

_TREND_CATEGORIES = [t.value for t in TrendDirection]
_REGIME_CATEGORIES = [r.value for r in MarketRegime]

FEATURE_COLUMNS: list[str] = (
    [
        "return_1",
        "return_4",
        "return_20",
        "ema_20_dist",
        "ema_50_dist",
        "ema_200_dist",
        "ema_20_50_dist",
        "rsi",
        "macd",
        "macd_signal",
        "macd_diff",
        "atr_relative",
        "adx",
        "di_plus",
        "di_minus",
        "bb_pct",
        "stoch_k",
        "stoch_d",
        "volume_ratio",
        "momentum_10",
        "volatility_20",
        "candle_body_ratio",
        "candle_upper_wick_ratio",
        "candle_lower_wick_ratio",
        "candle_bullish",
        "pct_from_recent_high",
        "pct_from_recent_low",
        "bos_bullish",
        "bos_bearish",
        "hour_sin",
        "hour_cos",
        "day_of_week_sin",
        "day_of_week_cos",
    ]
    + [f"trend_{c}" for c in _TREND_CATEGORIES]
    + [f"regime_{c}" for c in _REGIME_CATEGORIES]
)


def _one_hot(series: pd.Series, categories: list[str], prefix: str) -> pd.DataFrame:
    out = pd.DataFrame(index=series.index)
    for cat in categories:
        out[f"{prefix}_{cat}"] = (series == cat).astype(float)
    return out


def build_feature_matrix(df: pd.DataFrame, regime_thresholds: RegimeThresholds | None = None) -> pd.DataFrame:
    """
    df: raw OHLCV as returned by app.mt5.market_data.get_candles*
        (ascending by time). Returns a DataFrame indexed the same as df,
        with exactly FEATURE_COLUMNS, all numeric, causal.
    """
    df = add_all_indicators(df)
    df = analyze_structure(df)
    df = classify_regime(df, regime_thresholds)

    feat = pd.DataFrame(index=df.index)

    feat["return_1"] = df["close"].pct_change(1)
    feat["return_4"] = df["close"].pct_change(4)
    feat["return_20"] = df["close"].pct_change(20)

    # EMA distance normalized by ATR (volatility-adjusted, comparable across symbols).
    safe_atr = df["atr"].replace(0, np.nan)
    feat["ema_20_dist"] = (df["close"] - df["ema_20"]) / safe_atr
    feat["ema_50_dist"] = (df["close"] - df["ema_50"]) / safe_atr
    feat["ema_200_dist"] = (df["close"] - df["ema_200"]) / safe_atr
    feat["ema_20_50_dist"] = (df["ema_20"] - df["ema_50"]) / safe_atr

    feat["rsi"] = df["rsi"]
    feat["macd"] = df["macd"]
    feat["macd_signal"] = df["macd_signal"]
    feat["macd_diff"] = df["macd_diff"]
    feat["atr_relative"] = df["atr"] / df["close"]
    feat["adx"] = df["adx"]
    feat["di_plus"] = df["di_plus"]
    feat["di_minus"] = df["di_minus"]
    feat["bb_pct"] = df["bb_pct"]
    feat["stoch_k"] = df["stoch_k"]
    feat["stoch_d"] = df["stoch_d"]
    feat["volume_ratio"] = df["volume_ratio"]

    feat["momentum_10"] = df["close"] - df["close"].shift(10)
    feat["volatility_20"] = df["close"].pct_change().rolling(window=20, min_periods=20).std()

    candle_range = (df["high"] - df["low"]).replace(0, np.nan)
    feat["candle_body_ratio"] = (df["close"] - df["open"]).abs() / candle_range
    feat["candle_upper_wick_ratio"] = (df["high"] - df[["open", "close"]].max(axis=1)) / candle_range
    feat["candle_lower_wick_ratio"] = (df[["open", "close"]].min(axis=1) - df["low"]) / candle_range
    feat["candle_bullish"] = (df["close"] > df["open"]).astype(float)

    feat["pct_from_recent_high"] = (df["close"] - df["recent_high"]) / safe_atr
    feat["pct_from_recent_low"] = (df["close"] - df["recent_low"]) / safe_atr

    feat["bos_bullish"] = df["bos_bullish"].astype(float)
    feat["bos_bearish"] = df["bos_bearish"].astype(float)

    hour = df["time"].dt.hour + df["time"].dt.minute / 60.0
    feat["hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    feat["hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    dow = df["time"].dt.dayofweek
    feat["day_of_week_sin"] = np.sin(2 * np.pi * dow / 7.0)
    feat["day_of_week_cos"] = np.cos(2 * np.pi * dow / 7.0)

    feat = feat.join(_one_hot(df["structure_trend"], _TREND_CATEGORIES, "trend"))
    feat = feat.join(_one_hot(df["regime"], _REGIME_CATEGORIES, "regime"))

    return feat[FEATURE_COLUMNS]


def compute_forward_labels(df: pd.DataFrame, horizon_bars: int = 8, atr_multiple: float = 1.0) -> pd.Series:
    """
    FORWARD-LOOKING - training target only, never a feature. See module
    docstring. Label bar i by whether price `horizon_bars` bars later has
    moved more than `atr_multiple * ATR(i)` in either direction:
        BUY     if future_close - close > atr_multiple * atr
        SELL    if future_close - close < -atr_multiple * atr
        NEUTRAL otherwise
    The last `horizon_bars` rows get NaN (no future bar to look at yet).
    """
    df = add_all_indicators(df) if "atr" not in df.columns else df
    future_close = df["close"].shift(-horizon_bars)
    forward_move = future_close - df["close"]
    threshold = atr_multiple * df["atr"]

    label = pd.Series(np.where(forward_move > threshold, "BUY", np.where(forward_move < -threshold, "SELL", "NEUTRAL")), index=df.index)
    unknown_future = future_close.isna()
    label[unknown_future] = np.nan
    return label.rename("label")


def prepare_training_data(
    df: pd.DataFrame,
    horizon_bars: int = 8,
    atr_multiple: float = 1.0,
    regime_thresholds: RegimeThresholds | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """
    Returns (X, y) chronologically ordered and row-aligned, with every row
    that has any NaN feature OR an unknowable (future-tail) label dropped.
    """
    df = df.reset_index(drop=True)
    X = build_feature_matrix(df, regime_thresholds)
    y = compute_forward_labels(df, horizon_bars=horizon_bars, atr_multiple=atr_multiple)

    valid = X.notna().all(axis=1) & y.notna()
    X = X.loc[valid].reset_index(drop=True)
    y = y.loc[valid].reset_index(drop=True)
    return X, y
