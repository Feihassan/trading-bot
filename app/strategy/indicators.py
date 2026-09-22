"""
Technical indicators computed on OHLCV candle data.

Design decisions:
- Every indicator here is causal: it only uses the current row and rows
  before it (rolling/EWM windows), never a centered or forward-looking
  window. This is what makes it safe to reuse this exact module for both
  live signal generation and ML feature engineering (Phase 4) without
  introducing look-ahead bias - a value computed on `df.iloc[:t+1]` is
  identical to the value at row t computed on the full series. This
  property is directly asserted in `tests/test_indicators.py`.
- Warm-up: indicators need `n` prior bars before they're defined (e.g. a
  200-period EMA needs ~200 bars). Rows before that are NaN by design -
  callers must not treat NaN indicator values as zero/neutral.
- We use the `ta` library (pure pandas/numpy, causal by construction) for
  the standard indicators rather than hand-rolling formulas that are easy
  to get subtly wrong (e.g. Wilder's smoothing for RSI/ADX/ATR).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import ta


def add_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_20"] = ta.trend.EMAIndicator(df["close"], window=20).ema_indicator()
    df["ema_50"] = ta.trend.EMAIndicator(df["close"], window=50).ema_indicator()
    df["ema_200"] = ta.trend.EMAIndicator(df["close"], window=200).ema_indicator()
    return df


def add_rsi(df: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    df = df.copy()
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=window).rsi()
    return df


def add_macd(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    macd = ta.trend.MACD(df["close"], window_slow=26, window_fast=12, window_sign=9)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_diff"] = macd.macd_diff()
    return df


def add_atr(df: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    df = df.copy()
    df["atr"] = ta.volatility.AverageTrueRange(
        df["high"], df["low"], df["close"], window=window
    ).average_true_range()
    return df


def add_adx(df: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    df = df.copy()
    adx_indicator = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=window)
    df["adx"] = adx_indicator.adx()
    df["di_plus"] = adx_indicator.adx_pos()
    df["di_minus"] = adx_indicator.adx_neg()
    return df


def add_bollinger_bands(df: pd.DataFrame, window: int = 20, window_dev: float = 2.0) -> pd.DataFrame:
    df = df.copy()
    bb = ta.volatility.BollingerBands(df["close"], window=window, window_dev=window_dev)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_middle"] = bb.bollinger_mavg()
    df["bb_lower"] = bb.bollinger_lband()
    # Position of price within the bands: 0 = at lower band, 1 = at upper band.
    band_width = df["bb_upper"] - df["bb_lower"]
    df["bb_pct"] = np.where(band_width > 0, (df["close"] - df["bb_lower"]) / band_width, np.nan)
    return df


def add_stochastic(df: pd.DataFrame, window: int = 14, smooth_window: int = 3) -> pd.DataFrame:
    df = df.copy()
    stoch = ta.momentum.StochasticOscillator(
        df["high"], df["low"], df["close"], window=window, smooth_window=smooth_window
    )
    df["stoch_k"] = stoch.stoch()
    df["stoch_d"] = stoch.stoch_signal()
    return df


def add_volume_features(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """MT5 forex feeds report tick_volume (number of price changes), not
    real traded volume - used here as the best available liquidity proxy."""
    df = df.copy()
    df["volume_sma"] = df["tick_volume"].rolling(window=window, min_periods=window).mean()
    df["volume_ratio"] = np.where(
        df["volume_sma"] > 0, df["tick_volume"] / df["volume_sma"], np.nan
    )
    return df


def add_recent_extremes(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """Rolling N-bar high/low, inclusive of the current bar."""
    df = df.copy()
    df["recent_high"] = df["high"].rolling(window=window, min_periods=window).max()
    df["recent_low"] = df["low"].rolling(window=window, min_periods=window).min()
    return df


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the full standard indicator set in one call. Input must have
    at least ['open','high','low','close','tick_volume'] columns, sorted
    ascending by time (oldest first) - the same shape returned by
    app.mt5.market_data.get_candles / get_candles_range."""
    df = df.copy()
    df = add_moving_averages(df)
    df = add_rsi(df)
    df = add_macd(df)
    df = add_atr(df)
    df = add_adx(df)
    df = add_bollinger_bands(df)
    df = add_stochastic(df)
    df = add_volume_features(df)
    df = add_recent_extremes(df)
    return df
