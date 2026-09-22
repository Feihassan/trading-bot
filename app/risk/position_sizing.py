"""
Position sizing: converts (equity, risk %, stop-loss distance, symbol spec)
into a broker-valid lot size.

Design decisions:
- This is pulled forward from Phase 5 because the backtesting engine
  (Phase 3) cannot produce a meaningful equity curve without it, and the
  live execution engine (Phase 6) will import this exact module rather
  than duplicate the math - one sizing implementation, used everywhere.
- Deliberately NOT `lot = balance * risk` (spec section 13 explicitly
  calls this out as wrong). Correct forex position sizing is:

      risk_amount = equity * risk_per_trade
      price_distance = |entry - stop_loss|
      ticks = price_distance / tick_size
      loss_per_lot = ticks * tick_value
      lots = risk_amount / loss_per_lot

  `tick_value`/`tick_size` come from the broker's SymbolInfo (see
  app.mt5.market_data.SymbolSpec) and already encode contract size,
  quote/profit currency, and (for MT5) conversion to account currency for
  the common case - so this formula is correct across symbols with very
  different contract specs (e.g. EURUSD vs XAUUSD) without special-casing
  any of them here.
- The raw lot size is then normalized to the broker's volume_min/max/step.
  Normalization always rounds DOWN to the nearest step, never up - risking
  less than requested is acceptable, risking more silently is not.
- If volume_min itself would risk more than the caller asked for, we do
  NOT silently oversize the trade. We report that in
  PositionSizeResult.exceeds_target_risk so the caller (risk manager in
  live trading, the backtest engine here) can decide to skip the trade
  rather than take on undisclosed extra risk.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.mt5.market_data import SymbolSpec


class PositionSizingError(Exception):
    pass


@dataclass
class PositionSizeResult:
    volume: float
    risk_amount_requested: float
    risk_amount_actual: float
    exceeds_target_risk: bool


def _volume_decimals(step: float) -> int:
    if step <= 0:
        return 2
    s = f"{step:.8f}".rstrip("0")
    if "." not in s:
        return 0
    return len(s.split(".")[1])


def normalize_volume(volume: float, spec: SymbolSpec) -> float:
    if spec.volume_step <= 0:
        raise PositionSizingError(f"Invalid volume_step for {spec.name}: {spec.volume_step}")

    steps = math.floor(volume / spec.volume_step + 1e-9)
    normalized = steps * spec.volume_step
    normalized = max(spec.volume_min, min(normalized, spec.volume_max))
    return round(normalized, _volume_decimals(spec.volume_step))


def loss_per_lot(price_distance: float, spec: SymbolSpec) -> float:
    """Account-currency loss for a 1.0-lot position if price moves by
    `price_distance` against it."""
    if price_distance <= 0:
        raise PositionSizingError("price_distance must be positive")
    if spec.trade_tick_size <= 0:
        raise PositionSizingError(f"Invalid trade_tick_size for {spec.name}: {spec.trade_tick_size}")
    ticks = price_distance / spec.trade_tick_size
    return ticks * spec.trade_tick_value


def calculate_position_size(
    equity: float,
    risk_per_trade: float,
    entry_price: float,
    stop_loss_price: float,
    spec: SymbolSpec,
    max_acceptable_risk_multiple: float = 1.5,
) -> PositionSizeResult:
    """
    Returns the broker-valid lot size that risks approximately
    `risk_per_trade` fraction of `equity` if the stop loss is hit.

    If the broker's minimum volume would risk more than
    `max_acceptable_risk_multiple` times the requested risk, `volume` is
    returned as 0.0 and `exceeds_target_risk` is True - the caller must
    treat that as "do not take this trade" (spec: risk manager may never
    be bypassed, including by an unfavorable minimum lot size).
    """
    if equity <= 0:
        raise PositionSizingError("equity must be positive")
    if not (0 < risk_per_trade <= 1):
        raise PositionSizingError("risk_per_trade must be a fraction between 0 and 1")

    price_distance = abs(entry_price - stop_loss_price)
    risk_amount = equity * risk_per_trade
    loss_1_lot = loss_per_lot(price_distance, spec)
    if loss_1_lot <= 0:
        raise PositionSizingError("Computed non-positive loss per lot - check symbol spec / stop distance")

    raw_volume = risk_amount / loss_1_lot
    volume = normalize_volume(raw_volume, spec)

    actual_risk_amount = volume * loss_1_lot
    exceeds_target = actual_risk_amount > risk_amount * max_acceptable_risk_multiple

    if exceeds_target:
        return PositionSizeResult(
            volume=0.0,
            risk_amount_requested=risk_amount,
            risk_amount_actual=actual_risk_amount,
            exceeds_target_risk=True,
        )

    return PositionSizeResult(
        volume=volume,
        risk_amount_requested=risk_amount,
        risk_amount_actual=actual_risk_amount,
        exceeds_target_risk=False,
    )
