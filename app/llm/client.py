"""
Calls an OpenAI-compatible chat completions endpoint for a structured,
advisory-only second opinion (spec section 18), and validates its output
deterministically before anything downstream is allowed to touch it.

Design decisions:
- The system prompt is explicit that the model has NO authority to
  execute trades and that its output is validated - this is a defense
  against prompt injection from... itself, effectively; it costs nothing
  and removes any ambiguity about the contract.
- Every field of the response is validated and coerced to a safe value
  on failure: an invalid `decision` becomes "WAIT", an out-of-range
  `confidence` is clamped, non-string/non-list fields are coerced. A
  malformed or unparsable response never raises up into caller code - it
  becomes an `LLMAnalysis` with `valid=False` and a WAIT decision, which
  is the same fail-safe default used everywhere else in this codebase
  when a signal is ambiguous or a component is unavailable.
- Network/HTTP failures are caught the same way telegram.py catches
  them: logged, never raised, resulting in a safe WAIT rather than an
  exception propagating into trading logic that doesn't expect one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import Settings
from app.llm.context import MarketContext
from app.logging_setup import get_logger

logger = get_logger("llm.client")

VALID_DECISIONS = ("BUY", "SELL", "WAIT")

SYSTEM_PROMPT = """You are a market analysis assistant for a Forex trading system. \
You are given structured, already-computed market data (trend, regime, indicators, \
support/resistance, an optional ML model's probabilities, current open positions, and \
the account's current risk state).

You do NOT have the authority to execute trades, modify orders, or change risk \
settings. Your output is advisory only and will be passed through deterministic \
validation that can downgrade or reject it - it can never bypass position sizing, \
stop-loss requirements, or any risk limit.

Respond with ONLY a single JSON object, no other text, matching exactly this shape:
{"decision": "BUY" | "SELL" | "WAIT", "confidence": <float 0.0-1.0>, "reasoning": "<short explanation>", "risk_flags": ["<short flag>", ...]}

If the data is ambiguous, conflicting, or insufficient, respond with "WAIT" rather \
than guessing. Use risk_flags to note anything concerning (e.g. "high_volatility", \
"conflicting_trend", "near_key_resistance") even if your decision is still directional."""


@dataclass
class LLMAnalysis:
    decision: str  # "BUY" | "SELL" | "WAIT"
    confidence: float  # 0.0-1.0
    reasoning: str
    risk_flags: list[str] = field(default_factory=list)
    raw_response: str | None = None
    valid: bool = True
    validation_errors: list[str] = field(default_factory=list)


def _disabled(reason: str) -> LLMAnalysis:
    return LLMAnalysis(decision="WAIT", confidence=0.0, reasoning=reason, risk_flags=[], valid=False, validation_errors=[reason])


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return text


def _parse_and_validate(content: str) -> LLMAnalysis:
    errors: list[str] = []
    try:
        data = json.loads(_strip_code_fence(content))
    except json.JSONDecodeError as exc:
        return LLMAnalysis(
            decision="WAIT", confidence=0.0, reasoning="Could not parse LLM response as JSON",
            risk_flags=["INVALID_JSON"], raw_response=content, valid=False, validation_errors=[str(exc)],
        )

    if not isinstance(data, dict):
        return LLMAnalysis(
            decision="WAIT", confidence=0.0, reasoning="LLM response was not a JSON object",
            risk_flags=["INVALID_JSON"], raw_response=content, valid=False, validation_errors=["top-level value is not an object"],
        )

    decision = data.get("decision")
    if decision not in VALID_DECISIONS:
        errors.append(f"invalid decision '{decision}' - defaulting to WAIT")
        decision = "WAIT"

    confidence = data.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        errors.append(f"confidence '{confidence}' is not numeric - defaulting to 0.0")
        confidence = 0.0
    if not (0.0 <= confidence <= 1.0):
        errors.append(f"confidence {confidence} outside [0,1] - clamped")
        confidence = max(0.0, min(1.0, confidence))

    reasoning = data.get("reasoning", "")
    if not isinstance(reasoning, str):
        errors.append("reasoning was not a string - coerced")
        reasoning = str(reasoning)
    reasoning = reasoning[:2000]

    risk_flags = data.get("risk_flags", [])
    if not isinstance(risk_flags, list):
        errors.append("risk_flags was not a list - discarded")
        risk_flags = []
    risk_flags = [str(f) for f in risk_flags][:20]

    return LLMAnalysis(
        decision=decision, confidence=confidence, reasoning=reasoning, risk_flags=risk_flags,
        raw_response=content, valid=len(errors) == 0, validation_errors=errors,
    )


def call_llm(context: MarketContext, settings: Settings, timeout_seconds: float = 20.0) -> LLMAnalysis:
    if not settings.llm_enabled:
        return _disabled("LLM_ENABLED is false")
    if not settings.llm_api_key:
        logger.warning("LLM is enabled but LLM_API_KEY is not set")
        return _disabled("LLM_API_KEY is not set")

    request_body: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(context.to_dict())},
        ],
        "temperature": 0.2,
    }

    try:
        response = httpx.post(
            f"{settings.llm_api_base}/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json=request_body,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(f"LLM request failed: {exc}")
        return LLMAnalysis(
            decision="WAIT", confidence=0.0, reasoning=f"LLM request failed: {exc}",
            risk_flags=["LLM_REQUEST_FAILED"], valid=False, validation_errors=[str(exc)],
        )

    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        logger.warning(f"Unexpected LLM response shape: {exc}")
        return LLMAnalysis(
            decision="WAIT", confidence=0.0, reasoning="Unexpected LLM response shape",
            risk_flags=["INVALID_RESPONSE_SHAPE"], raw_response=response.text[:2000], valid=False, validation_errors=[str(exc)],
        )

    return _parse_and_validate(content)
