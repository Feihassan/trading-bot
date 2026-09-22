"""Synthetic OHLCV generators shared by strategy tests - not real market
data, just deterministic price paths with known shapes (trend/range) so
indicator and structure logic can be checked against a known answer."""
from __future__ import annotations

import numpy as np
import pandas as pd


def make_trending_candles(n: int = 300, start: float = 1.1000, drift: float = 0.00015, noise: float = 0.00004, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = start + np.cumsum(np.full(n, drift) + rng.normal(0, noise, n))
    return _closes_to_ohlcv(closes, seed)


def make_ranging_candles(n: int = 300, center: float = 1.1000, amplitude: float = 0.0030, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 8 * np.pi, n)
    closes = center + amplitude * np.sin(x) + rng.normal(0, 0.00003, n)
    return _closes_to_ohlcv(closes, seed)


def _closes_to_ohlcv(closes: np.ndarray, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 1)
    n = len(closes)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    spread = np.abs(rng.normal(0.00005, 0.00002, n)) + 1e-6
    highs = np.maximum(opens, closes) + spread
    lows = np.minimum(opens, closes) - spread
    tick_volume = rng.integers(50, 500, n).astype(float)

    times = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "time": times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "tick_volume": tick_volume,
            "spread": rng.integers(1, 3, n),
            "real_volume": np.zeros(n),
        }
    )
