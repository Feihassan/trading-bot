"""
The risk manager: the single gate every proposed trade must pass through
before an order is ever sent. Spec section 12: "Never allow a strategy
signal to bypass the risk manager" - concretely, that means the execution
engine (Phase 6) will be written so it is structurally incapable of
calling MT5's order API with anything except a `RiskCheckResult` this
module approved. Nothing about signal confidence, ML probability, or an
LLM's opinion (Phase 9) can substitute for that approval.

Design decisions:
- `evaluate()` is a pure function of its arguments (settings + a
  `TradeIdea` + an `AccountState` snapshot + the symbol spec + the
  current time) - it does not reach into MT5 itself. That's deliberate:
  a pure core is exhaustively unit-testable (every limit below has a
  dedicated test with a fabricated AccountState) without mocking a live
  terminal connection, and it can be reused identically in a backtest's
  risk-aware mode later. Gathering the live AccountState from MT5 is a
  separate, thin responsibility that belongs to the execution layer
  (Phase 6), not here.
- Checks run in a fixed, documented order and stop at the first failure,
  each returning a specific, human-readable reason - this is what makes
  a rejected trade explainable (spec section 25) instead of a bare
  "approved: false". Each rejection also carries a stable `reason_code`
  (e.g. "DAILY_LOSS_LIMIT") alongside the free-text message, so callers
  like the execution engine can react to *which* limit fired (e.g. only
  a loss-limit or drawdown-halt code is worth a Telegram alert - a
  routine "spread too wide" skip is not) without parsing prose.
- Position sizing happens *inside* the risk manager, not before it -
  sizing is itself a risk decision (it's how max_exposure_per_currency
  and "minimum lot would over-risk" get enforced), not a separate step
  that happens to produce a number this module trusts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as dtime

from app.config import Settings
from app.mt5.market_data import SymbolSpec
from app.risk.position_sizing import calculate_position_size
from app.risk.safeguards import EmergencyStop
from app.strategy.signals import TradeIdea


@dataclass
class AccountState:
    equity: float
    balance: float
    day_start_equity: float
    week_start_equity: float
    peak_equity: float
    open_positions_count: int
    trades_today: int
    exposure_by_currency: dict[str, float] = field(default_factory=dict)


@dataclass
class RiskCheckResult:
    approved: bool
    volume: float | None
    risk_amount: float | None
    reasons: list[str]
    blocking_reason: str | None
    reason_code: str | None = None


def _parse_hhmm(value: str) -> dtime:
    hour, minute = value.split(":")
    return dtime(int(hour), int(minute))


def _within_trading_sessions(now: datetime, sessions: list[tuple[str, str]]) -> bool:
    if not sessions:
        return True
    current = now.time()
    for start_str, end_str in sessions:
        start, end = _parse_hhmm(start_str), _parse_hhmm(end_str)
        if start <= end:
            if start <= current <= end:
                return True
        else:  # overnight session, e.g. 22:00-06:00
            if current >= start or current <= end:
                return True
    return False


class RiskManager:
    def __init__(self, settings: Settings, emergency_stop: EmergencyStop | None = None):
        self.settings = settings
        self.emergency_stop = emergency_stop or EmergencyStop()

    def evaluate(
        self,
        idea: TradeIdea,
        account: AccountState,
        spec: SymbolSpec,
        now: datetime,
        current_spread_points: float | None = None,
    ) -> RiskCheckResult:
        reasons: list[str] = []

        def rejected(reason: str, code: str) -> RiskCheckResult:
            return RiskCheckResult(approved=False, volume=None, risk_amount=None, reasons=[*reasons, reason], blocking_reason=reason, reason_code=code)

        if idea.direction not in ("BUY", "SELL"):
            return rejected(f"Signal direction is '{idea.direction}', not an actionable trade", "INVALID_SIGNAL")

        stop_check = self.emergency_stop.check()
        if not stop_check.ok:
            return rejected(stop_check.reason or "Emergency stop is active", "EMERGENCY_STOP")

        if idea.entry is None or idea.stop_loss is None or idea.take_profit is None:
            return rejected("Signal is missing entry/stop-loss/take-profit - SL/TP are mandatory", "MISSING_SLTP")

        if idea.reward_risk is None or idea.reward_risk < self.settings.min_risk_reward:
            return rejected(f"Reward:risk {idea.reward_risk} is below configured minimum {self.settings.min_risk_reward}", "BELOW_MIN_RR")

        if account.day_start_equity > 0:
            daily_loss_pct = (account.day_start_equity - account.equity) / account.day_start_equity
            if daily_loss_pct >= self.settings.max_daily_loss:
                return rejected(f"Daily loss limit reached ({daily_loss_pct:.2%} >= {self.settings.max_daily_loss:.2%}) - no new trades today", "DAILY_LOSS_LIMIT")

        if account.week_start_equity > 0:
            weekly_loss_pct = (account.week_start_equity - account.equity) / account.week_start_equity
            if weekly_loss_pct >= self.settings.max_weekly_loss:
                return rejected(f"Weekly loss limit reached ({weekly_loss_pct:.2%} >= {self.settings.max_weekly_loss:.2%}) - no new trades this week", "WEEKLY_LOSS_LIMIT")

        if account.peak_equity > 0:
            drawdown_pct = (account.peak_equity - account.equity) / account.peak_equity
            if drawdown_pct >= self.settings.max_drawdown_halt:
                return rejected(f"Max drawdown halt triggered ({drawdown_pct:.2%} >= {self.settings.max_drawdown_halt:.2%}) - trading stopped", "MAX_DRAWDOWN")

        if account.open_positions_count >= self.settings.max_open_trades:
            return rejected(f"Max simultaneous open trades reached ({account.open_positions_count} >= {self.settings.max_open_trades})", "MAX_OPEN_TRADES")

        if account.trades_today >= self.settings.max_trades_per_day:
            return rejected(f"Max trades per day reached ({account.trades_today} >= {self.settings.max_trades_per_day})", "MAX_TRADES_PER_DAY")

        spread = current_spread_points if current_spread_points is not None else idea.spread_points
        if spread is not None and spread > self.settings.max_spread_points:
            return rejected(f"Spread {spread:.0f}pts exceeds max allowed {self.settings.max_spread_points:.0f}pts", "SPREAD_TOO_WIDE")

        if not _within_trading_sessions(now, self.settings.trading_sessions):
            return rejected(f"Current time {now.strftime('%H:%M UTC')} is outside configured trading sessions", "OUTSIDE_SESSION")

        sizing = calculate_position_size(
            equity=account.equity,
            risk_per_trade=self.settings.risk_per_trade,
            entry_price=idea.entry,
            stop_loss_price=idea.stop_loss,
            spec=spec,
        )
        if sizing.volume <= 0:
            return rejected("Broker minimum lot size would risk substantially more than configured risk_per_trade - skipping rather than oversizing", "MIN_LOT_OVERRISK")

        base_currency = spec.currency_base
        existing_exposure = account.exposure_by_currency.get(base_currency, 0.0)
        prospective_exposure = existing_exposure + sizing.risk_amount_actual
        if account.equity > 0:
            exposure_pct = prospective_exposure / account.equity
            if exposure_pct > self.settings.max_exposure_per_currency:
                return rejected(
                    f"{base_currency} exposure would reach {exposure_pct:.2%} of equity, "
                    f"exceeding max {self.settings.max_exposure_per_currency:.2%}",
                    "EXPOSURE_LIMIT",
                )

        reasons.append(f"All risk checks passed - sizing {sizing.volume} lots (${sizing.risk_amount_actual:.2f} risk)")
        return RiskCheckResult(
            approved=True,
            volume=sizing.volume,
            risk_amount=sizing.risk_amount_actual,
            reasons=reasons,
            blocking_reason=None,
            reason_code="APPROVED",
        )
