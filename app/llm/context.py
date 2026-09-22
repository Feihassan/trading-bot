"""
Builds the structured market context handed to the LLM (spec section
18). This module does no I/O of its own - it's a pure formatter over
data the caller already has (candles, open positions, risk state, an
optional ML probability dict), which keeps it trivially testable and
reusable from both the API layer and any future scheduler.

Design decision: the LLM never sees raw OHLCV or gets to run its own
"analysis" - it only ever sees the SAME derived facts (trend, regime,
indicator values, support/resistance, ML probabilities) that the
deterministic signal engine (Phase 5) already computed. This bounds what
the LLM can possibly disagree about to interpretation of known facts, not
invented ones, and is what makes its output a second opinion rather than
an independent (and unverifiable) source of "analysis".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.strategy.market_structure import nearest_support_resistance
from app.strategy.signals import build_analysis


@dataclass
class MarketContext:
    symbol: str
    timeframe: str
    timestamp: str
    trend: str
    regime: str
    indicators: dict[str, float]
    market_structure: dict[str, Any]
    volatility: dict[str, Any]
    support_resistance: dict[str, float | None]
    ml_probabilities: dict[str, float] | None
    current_positions: list[dict[str, Any]]
    risk_state: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp,
            "trend": self.trend,
            "regime": self.regime,
            "indicators": self.indicators,
            "market_structure": self.market_structure,
            "volatility": self.volatility,
            "support_resistance": self.support_resistance,
            "ml_probabilities": self.ml_probabilities,
            "current_positions": self.current_positions,
            "risk_state": self.risk_state,
        }


def build_market_context(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    current_positions: list[dict[str, Any]] | None = None,
    risk_state: dict[str, Any] | None = None,
    ml_probabilities: dict[str, float] | None = None,
) -> MarketContext:
    """
    df: raw OHLCV with enough trailing history for indicator warm-up
        (>= 200 bars for ema_200), ascending by time.
    """
    analyzed = build_analysis(df)
    last = analyzed.iloc[-1]
    as_of = len(analyzed) - 1
    sr = nearest_support_resistance(analyzed, as_of)

    return MarketContext(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=str(last["time"]),
        trend=str(last.get("structure_trend", "UNKNOWN")),
        regime=str(last.get("regime", "UNKNOWN")),
        indicators={
            "rsi": _safe_float(last.get("rsi")),
            "macd": _safe_float(last.get("macd")),
            "macd_diff": _safe_float(last.get("macd_diff")),
            "adx": _safe_float(last.get("adx")),
            "atr": _safe_float(last.get("atr")),
            "bb_pct": _safe_float(last.get("bb_pct")),
            "stoch_k": _safe_float(last.get("stoch_k")),
            "stoch_d": _safe_float(last.get("stoch_d")),
            "ema_20": _safe_float(last.get("ema_20")),
            "ema_50": _safe_float(last.get("ema_50")),
            "ema_200": _safe_float(last.get("ema_200")),
            "close": _safe_float(last.get("close")),
        },
        market_structure={
            "bos_bullish": bool(last.get("bos_bullish", False)),
            "bos_bearish": bool(last.get("bos_bearish", False)),
            "recent_high": _safe_float(last.get("recent_high")),
            "recent_low": _safe_float(last.get("recent_low")),
        },
        volatility={
            "regime": str(last.get("regime", "UNKNOWN")),
            "atr_percentile": _safe_float(last.get("atr_percentile")),
        },
        support_resistance={"support": sr.support, "resistance": sr.resistance},
        ml_probabilities=ml_probabilities,
        current_positions=current_positions or [],
        risk_state=risk_state or {},
    )


def _safe_float(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return round(float(value), 6)
