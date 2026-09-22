"""
Signal engine: combines technical analysis + market structure + regime +
ML probability + spread/volatility checks into one BUY/SELL/WAIT decision
with a reasoned (not guaranteed) confidence score - spec section 10.

Design decisions:
- Every component is computed independently and then required to AGREE.
  If the technical score says bullish but market structure says
  bearish, or the ML model disagrees with both, the result is WAIT -
  never "average them out into a weak BUY". Spec section 10 is explicit
  that WAIT is preferable to a forced trade when conditions conflict;
  averaging conflicting signals into a mediocre score would violate that
  by turning "the components disagree" into "a slightly less confident
  version of whichever score was numerically larger."
- The ML model is optional. Without one, the engine still produces a
  technical+structure+regime-only signal, but composite confidence is
  capped lower and every reason list says so - this is a real, reduced-
  information mode, not silently treated as equivalent to having ML
  confirmation (spec's whole premise is a *multi-factor* system).
- `entry`/`stop_loss`/`take_profit`/`reward_risk` are computed as soon as
  a directional candidate exists, even if a later gate (spread, minimum
  score, minimum R:R) downgrades the final decision to WAIT. This is
  deliberate: spec section 25 requires every decision to be explainable,
  and "here's the trade we would have taken and exactly why we didn't"
  is far more useful in the log than nulled-out fields.
- `make_signal_engine_strategy` adapts this engine to the Phase 3
  backtesting engine's `StrategyFunc` interface, so the actual production
  signal logic - not just the placeholder EMA crossover - can be
  backtested end to end.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from app.ai.confidence import ConfidenceThresholds
from app.ai.model import TradingModel
from app.ai.predictor import predict_latest
from app.backtesting.engine import TradeSignal
from app.strategy.indicators import add_all_indicators
from app.strategy.market_structure import analyze_structure
from app.strategy.regime import RegimeThresholds, classify_regime, regime_allows_trend_following

DIRECTIONS = ("BUY", "SELL")


def build_analysis(df: pd.DataFrame, regime_thresholds: RegimeThresholds | None = None) -> pd.DataFrame:
    """Full Phase 2 pipeline: indicators + market structure + regime."""
    df = add_all_indicators(df)
    df = analyze_structure(df)
    df = classify_regime(df, regime_thresholds)
    return df


def _technical_signal(row: pd.Series) -> tuple[str, float, list[str]]:
    """Weighted technical score from EMA alignment, MACD, RSI zone, ADX/DI
    trend strength, and Stochastic - each weight documents its own
    contribution so the total is auditable, not a black box."""
    bull, bear, max_points = 0.0, 0.0, 0.0
    reasons: list[str] = []

    w = 30.0
    max_points += w
    if row["ema_20"] > row["ema_50"] > row["ema_200"]:
        bull += w
        reasons.append("EMA 20>50>200 (bullish alignment)")
    elif row["ema_20"] < row["ema_50"] < row["ema_200"]:
        bear += w
        reasons.append("EMA 20<50<200 (bearish alignment)")

    w = 20.0
    max_points += w
    if row["macd_diff"] > 0:
        bull += w
        reasons.append("MACD histogram positive")
    elif row["macd_diff"] < 0:
        bear += w
        reasons.append("MACD histogram negative")

    w = 15.0
    max_points += w
    if 50 <= row["rsi"] < 70:
        bull += w
        reasons.append(f"RSI {row['rsi']:.0f} bullish momentum zone")
    elif 30 < row["rsi"] <= 50:
        bear += w
        reasons.append(f"RSI {row['rsi']:.0f} bearish momentum zone")

    w = 20.0
    max_points += w
    adx_factor = min(row["adx"] / 40.0, 1.0)
    if row["di_plus"] > row["di_minus"]:
        bull += w * adx_factor
        if adx_factor > 0.4:
            reasons.append(f"ADX {row['adx']:.0f} confirms bullish trend strength")
    else:
        bear += w * adx_factor
        if adx_factor > 0.4:
            reasons.append(f"ADX {row['adx']:.0f} confirms bearish trend strength")

    w = 15.0
    max_points += w
    if row["stoch_k"] > row["stoch_d"] and row["stoch_k"] < 80:
        bull += w
        reasons.append("Stochastic %K>%D, not overbought")
    elif row["stoch_k"] < row["stoch_d"] and row["stoch_k"] > 20:
        bear += w
        reasons.append("Stochastic %K<%D, not oversold")

    if bull > bear:
        return "BUY", 100.0 * bull / max_points, reasons
    if bear > bull:
        return "SELL", 100.0 * bear / max_points, reasons
    return "NEUTRAL", 0.0, reasons


@dataclass
class SignalEngineConfig:
    atr_sl_multiple: float = 1.5
    atr_tp_multiple: float = 3.0
    min_technical_score: float = 40.0
    min_composite_confidence: float = 55.0
    max_spread_points: float = 25.0
    min_risk_reward: float = 2.0
    technical_weight: float = 0.35
    structure_weight: float = 0.20
    ml_weight: float = 0.45
    ml_confidence_thresholds: ConfidenceThresholds = field(default_factory=ConfidenceThresholds)


@dataclass
class TradeIdea:
    symbol: str
    timestamp: pd.Timestamp | None
    direction: str  # "BUY" | "SELL" | "WAIT"
    confidence: float  # 0-100, composite - not a probability of winning
    entry: float | None
    stop_loss: float | None
    take_profit: float | None
    reward_risk: float | None
    technical_score: float
    technical_direction: str
    structure_trend: str
    regime: str
    ml_probabilities: dict[str, float] | None
    ml_direction: str | None
    spread_points: float | None
    reasons: list[str] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)
    # Optional LLM overlay (Phase 9) - populated only when app.llm.overlay
    # .apply_llm_overlay() has run; the LLM never influences entry/SL/TP/
    # sizing, only whether the deterministic direction survives as-is or
    # gets downgraded to WAIT.
    llm_decision: str | None = None
    llm_confidence: float | None = None
    llm_reasoning: str | None = None
    llm_risk_flags: list[str] = field(default_factory=list)


def _wait(symbol: str, timestamp, reasons: list[str], blocking: list[str]) -> TradeIdea:
    return TradeIdea(
        symbol=symbol,
        timestamp=timestamp,
        direction="WAIT",
        confidence=0.0,
        entry=None,
        stop_loss=None,
        take_profit=None,
        reward_risk=None,
        technical_score=0.0,
        technical_direction="NEUTRAL",
        structure_trend="UNKNOWN",
        regime="UNKNOWN",
        ml_probabilities=None,
        ml_direction=None,
        spread_points=None,
        reasons=reasons,
        blocking_reasons=blocking,
    )


def generate_signal(
    df: pd.DataFrame,
    symbol: str,
    model: TradingModel | None = None,
    config: SignalEngineConfig | None = None,
    regime_thresholds: RegimeThresholds | None = None,
) -> TradeIdea:
    """
    df: raw OHLCV (ascending by time) with enough trailing history for
        indicator warm-up (>= ema_200's 200 bars) plus ML feature warm-up
        if a model is supplied.
    """
    config = config or SignalEngineConfig()
    analyzed = build_analysis(df, regime_thresholds)
    last = analyzed.iloc[-1]

    required = ["ema_20", "ema_50", "ema_200", "atr", "adx", "rsi", "macd_diff", "stoch_k", "stoch_d", "di_plus", "di_minus"]
    if any(pd.isna(last[c]) for c in required) or pd.isna(last.get("structure_trend")) or pd.isna(last.get("regime")):
        return _wait(symbol, last.get("time"), [], ["Insufficient warm-up history for one or more indicators"])

    reasons: list[str] = []
    blocking: list[str] = []

    tech_direction, tech_score, tech_reasons = _technical_signal(last)
    reasons.extend(tech_reasons)

    structure_trend = last["structure_trend"]
    structure_direction = {"UPTREND": "BUY", "DOWNTREND": "SELL"}.get(structure_trend, "NEUTRAL")
    if structure_direction != "NEUTRAL":
        reasons.append(f"Market structure: {structure_trend}")
    if bool(last.get("bos_bullish")):
        reasons.append("Bullish break of structure")
    if bool(last.get("bos_bearish")):
        reasons.append("Bearish break of structure")

    regime_value = last["regime"]
    trend_ok = regime_allows_trend_following(regime_value)
    if not trend_ok:
        blocking.append(f"Regime {regime_value} does not support trend-following setups")

    ml_direction: str | None = None
    ml_probs: dict[str, float] | None = None
    if model is not None:
        try:
            prediction = predict_latest(model, df, config.ml_confidence_thresholds)
            ml_probs = prediction.probabilities
            ml_direction = prediction.decision.action
            if ml_direction == "WAIT":
                reasons.append(f"ML: {prediction.decision.reason}")
            else:
                reasons.append(f"ML confirms {ml_direction} (p={prediction.decision.confidence:.2f})")
        except ValueError as exc:
            reasons.append(f"ML unavailable: {exc}")
    else:
        reasons.append("ML model not supplied - operating in technical+structure-only mode")

    directional_votes = [d for d in (tech_direction, structure_direction, ml_direction) if d not in (None, "NEUTRAL", "WAIT")]
    unique_directions = set(directional_votes)

    if len(unique_directions) == 0:
        blocking.append("No directional agreement among technical/structure/ML components")
        candidate_direction = None
    elif len(unique_directions) > 1:
        blocking.append(f"Conflicting directional signals: {sorted(unique_directions)}")
        candidate_direction = None
    else:
        candidate_direction = unique_directions.pop()
        if not trend_ok:
            candidate_direction = None  # regime already recorded as blocking above

    # Composite confidence: technical + structure agreement + ML (if present),
    # renormalized to exclude ML's weight when no model was supplied.
    structure_component = 100.0 if structure_direction == tech_direction and tech_direction != "NEUTRAL" else 0.0
    ml_component = (ml_probs[ml_direction] * 100.0) if (ml_direction in DIRECTIONS and ml_probs) else 0.0

    if model is not None:
        weights = (config.technical_weight, config.structure_weight, config.ml_weight)
    else:
        total = config.technical_weight + config.structure_weight
        weights = (config.technical_weight / total, config.structure_weight / total, 0.0)

    composite_confidence = weights[0] * tech_score + weights[1] * structure_component + weights[2] * ml_component

    spread_points = float(last.get("spread", 0) or 0)
    entry = stop_loss = take_profit = reward_risk = None

    if candidate_direction in DIRECTIONS:
        atr = last["atr"]
        entry = float(last["close"])
        if candidate_direction == "BUY":
            stop_loss = entry - config.atr_sl_multiple * atr
            take_profit = entry + config.atr_tp_multiple * atr
        else:
            stop_loss = entry + config.atr_sl_multiple * atr
            take_profit = entry - config.atr_tp_multiple * atr
        risk = abs(entry - stop_loss)
        reward_risk = abs(take_profit - entry) / risk if risk > 0 else 0.0

        if reward_risk < config.min_risk_reward:
            blocking.append(f"Reward:risk {reward_risk:.2f} below minimum {config.min_risk_reward:.2f}")
            candidate_direction = None
        elif tech_score < config.min_technical_score:
            blocking.append(f"Technical score {tech_score:.0f} below minimum {config.min_technical_score:.0f}")
            candidate_direction = None
        elif composite_confidence < config.min_composite_confidence:
            blocking.append(f"Composite confidence {composite_confidence:.0f} below minimum {config.min_composite_confidence:.0f}")
            candidate_direction = None
        elif spread_points > config.max_spread_points:
            blocking.append(f"Spread {spread_points:.0f}pts exceeds max {config.max_spread_points:.0f}pts")
            candidate_direction = None

    final_direction = candidate_direction if candidate_direction in DIRECTIONS else "WAIT"

    return TradeIdea(
        symbol=symbol,
        timestamp=last["time"],
        direction=final_direction,
        confidence=round(composite_confidence, 1) if final_direction != "WAIT" else round(composite_confidence, 1),
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        reward_risk=reward_risk,
        technical_score=round(tech_score, 1),
        technical_direction=tech_direction,
        structure_trend=structure_trend,
        regime=regime_value,
        ml_probabilities=ml_probs,
        ml_direction=ml_direction,
        spread_points=spread_points,
        reasons=reasons,
        blocking_reasons=blocking,
    )


def make_signal_engine_strategy(
    model: TradingModel | None = None,
    config: SignalEngineConfig | None = None,
    regime_thresholds: RegimeThresholds | None = None,
    symbol: str = "UNKNOWN",
):
    """Adapts generate_signal to app.backtesting.engine's StrategyFunc
    interface, so the real signal engine (not just example_strategy.py's
    placeholder) can be run through the Phase 3 backtester."""

    def strategy_fn(df_so_far: pd.DataFrame) -> TradeSignal | None:
        idea = generate_signal(df_so_far, symbol, model=model, config=config, regime_thresholds=regime_thresholds)
        if idea.direction not in DIRECTIONS:
            return None
        return TradeSignal(
            direction=idea.direction,
            entry=idea.entry,
            stop_loss=idea.stop_loss,
            take_profit=idea.take_profit,
            reason="; ".join(idea.reasons),
        )

    return strategy_fn
