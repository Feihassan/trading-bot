"""
Turns a raw {SELL, NEUTRAL, BUY} probability vector into a discrete
decision, deliberately biased toward WAIT.

Design decision (spec section 10): "If conditions conflict, WAIT is
preferable to forcing a trade." This module is the concrete implementation
of that rule at the ML layer - three separate conditions can each force
WAIT, and any one of them is enough:
    1. NEUTRAL itself has the highest probability.
    2. The top class's probability is below `min_confidence` - the model
       isn't confident enough about anything.
    3. The top two classes are too close together (`min_margin`) - the
       model is genuinely torn between e.g. BUY and NEUTRAL, which is a
       meaningfully different (weaker) situation than a clear BUY even if
       BUY happens to have the single highest number.

None of this is a claim that the resulting BUY/SELL decision will be
correct - it's a confidence *gate*, not a certainty statement.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.ai.features import LABEL_CLASSES


@dataclass
class ConfidenceThresholds:
    min_confidence: float = 0.55
    min_margin: float = 0.10


@dataclass
class Decision:
    action: str  # "BUY" | "SELL" | "WAIT"
    confidence: float
    probabilities: dict[str, float]
    reason: str


def decide(probabilities: dict[str, float], thresholds: ConfidenceThresholds | None = None) -> Decision:
    th = thresholds or ConfidenceThresholds()
    missing = set(LABEL_CLASSES) - set(probabilities)
    if missing:
        raise ValueError(f"probabilities missing classes: {missing}")

    ranked = sorted(probabilities.items(), key=lambda kv: kv[1], reverse=True)
    top_class, top_prob = ranked[0]
    second_prob = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = top_prob - second_prob

    if top_class == "NEUTRAL":
        return Decision("WAIT", top_prob, probabilities, "Model's highest-probability outcome is NEUTRAL")

    if top_prob < th.min_confidence:
        return Decision("WAIT", top_prob, probabilities, f"Top probability {top_prob:.2f} below min_confidence {th.min_confidence:.2f}")

    if margin < th.min_margin:
        return Decision("WAIT", top_prob, probabilities, f"Margin over next class {margin:.2f} below min_margin {th.min_margin:.2f} - conflicting signal")

    return Decision(top_class, top_prob, probabilities, f"{top_class} probability {top_prob:.2f}, margin {margin:.2f}")
