"""
Merges an LLMAnalysis into a TradeIdea - this is the deterministic
validation gate spec section 18 requires between the LLM's output and
anything that could act on it.

Structural guarantee, not just a convention: `apply_llm_overlay` has
exactly one assignment target for `direction` other than "leave it
unchanged", and that target is the literal string "WAIT". There is no
code path in this function that can set `direction` to "BUY" or "SELL" -
the LLM can veto a trade the deterministic signal engine (Phase 5)
already proposed, but it can never create one, upgrade a WAIT, or touch
entry/stop-loss/take-profit/position sizing. Combined with the risk
manager (which never even sees the LLM), this is what makes "the LLM
cannot override risk limits, cannot execute trades" true by construction
rather than by convention.

An invalid or unavailable LLM response (disabled, network failure,
malformed JSON) is noted in `reasons` but does NOT downgrade an
otherwise-valid deterministic signal - the LLM is advisory and optional;
its absence must not cripple the system that works without it.
"""
from __future__ import annotations

import dataclasses

from app.llm.client import LLMAnalysis
from app.strategy.signals import TradeIdea


def apply_llm_overlay(idea: TradeIdea, llm: LLMAnalysis) -> TradeIdea:
    reasons = [*idea.reasons]
    blocking = [*idea.blocking_reasons]
    direction = idea.direction

    if llm.reasoning:
        reasons.append(f"LLM ({llm.decision}, confidence {llm.confidence:.2f}): {llm.reasoning}")
    if llm.risk_flags:
        reasons.append(f"LLM risk flags: {', '.join(llm.risk_flags)}")

    if not llm.valid:
        reasons.append(f"LLM analysis unavailable: {'; '.join(llm.validation_errors)}")
    elif idea.direction in ("BUY", "SELL"):
        if llm.decision == "WAIT":
            blocking.append("LLM analysis recommends WAIT")
            direction = "WAIT"
        elif llm.decision != idea.direction:
            blocking.append(f"LLM disagrees with the signal engine (LLM: {llm.decision}, engine: {idea.direction})")
            direction = "WAIT"
        elif llm.risk_flags:
            blocking.append(f"LLM raised risk flags despite agreeing on direction: {', '.join(llm.risk_flags)}")
            direction = "WAIT"

    return dataclasses.replace(
        idea,
        direction=direction,
        reasons=reasons,
        blocking_reasons=blocking,
        llm_decision=llm.decision,
        llm_confidence=llm.confidence,
        llm_reasoning=llm.reasoning,
        llm_risk_flags=llm.risk_flags,
    )
