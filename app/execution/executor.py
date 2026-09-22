"""
The execution engine: turns an approved TradeIdea into a real MT5 order,
or - in DRY_RUN mode - into a fully-evaluated "what would have happened"
record and nothing else. This is the only place in the codebase allowed
to call app.mt5.orders.submit_market_order.

Implements the pre-trade checklist from spec section 14, in this order
(each step can short-circuit the rest):
    1. Safety guards: emergency stop, stale data
    2. Signal validity (must be BUY/SELL, not WAIT)
    3. Duplicate-position prevention (one open position per symbol+magic)
    4. Account state assembly (equity, exposure, trade counts)
    5. Risk manager evaluation (limits + position sizing) - spec section 12:
       nothing below this line runs unless the risk manager approved it
    6. Trading-mode / account-type consistency check (DEMO config must be
       running against an actual demo account; LIVE against an actual
       real account) - defense in depth on top of config.py's LIVE gate
    7. Place order (skipped entirely in DRY_RUN)
    8. Verify the position actually exists post-submission
    9. Record the outcome in the trade journal, always - approved,
       rejected, dry-run, or failed, so every decision is reconstructable
       from the journal alone (spec section 25).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import MetaTrader5 as mt5

from app.config import Settings, TradingMode
from app.logging_setup import get_logger
from app.mt5.account import get_account_info, get_open_positions, is_demo_account, is_real_account
from app.mt5.market_data import SymbolSpec
from app.mt5.orders import OrderResult, submit_market_order
from app.notifications.telegram import TelegramNotifier
from app.risk.risk_manager import RiskCheckResult, RiskManager
from app.risk.safeguards import ConsecutiveErrorTripwire, EmergencyStop, check_stale_data
from app.strategy.signals import TradeIdea

from .trade_manager import EquityTracker, TradeJournal, build_account_state

logger = get_logger("execution.executor")

# Risk-manager reason codes worth interrupting someone over (spec section
# 20's "daily loss limit reached" alert). Routine skips - a wide spread,
# an outside-session signal - happen constantly during normal operation
# and would make Telegram unusable if every one paged the user.
_NOTIFY_WORTHY_REASON_CODES = {"DAILY_LOSS_LIMIT", "WEEKLY_LOSS_LIMIT", "MAX_DRAWDOWN", "EMERGENCY_STOP"}


@dataclass
class ExecutionResult:
    executed: bool  # an order was actually sent and filled
    dry_run: bool
    skipped_reason: str | None
    idea: TradeIdea
    risk_result: RiskCheckResult | None
    order_result: OrderResult | None


class Executor:
    def __init__(
        self,
        settings: Settings,
        risk_manager: RiskManager,
        equity_tracker: EquityTracker | None = None,
        journal: TradeJournal | None = None,
        emergency_stop: EmergencyStop | None = None,
        error_tripwire: ConsecutiveErrorTripwire | None = None,
        notifier: TelegramNotifier | None = None,
        deviation_points: int = 20,
    ):
        self.settings = settings
        self.risk_manager = risk_manager
        self.equity_tracker = equity_tracker or EquityTracker()
        self.journal = journal or TradeJournal()
        self.emergency_stop = emergency_stop or EmergencyStop()
        self.error_tripwire = error_tripwire or ConsecutiveErrorTripwire()
        self.notifier = notifier or TelegramNotifier(settings)
        self.deviation_points = deviation_points

    def _skip(self, idea: TradeIdea, reason: str, risk_result: RiskCheckResult | None = None) -> ExecutionResult:
        logger.info(f"{idea.symbol}: execution skipped - {reason}")
        self.journal.record(
            {
                "symbol": idea.symbol,
                "direction": idea.direction,
                "executed": False,
                "dry_run": self.settings.trading_mode == TradingMode.DRY_RUN,
                "skipped_reason": reason,
                "confidence": idea.confidence,
                "reasons": idea.reasons,
            }
        )
        return ExecutionResult(executed=False, dry_run=False, skipped_reason=reason, idea=idea, risk_result=risk_result, order_result=None)

    def execute_signal(
        self,
        idea: TradeIdea,
        broker_symbol: str,
        spec: SymbolSpec,
        last_bar_time: datetime,
        timeframe: str,
    ) -> ExecutionResult:
        now = datetime.now(timezone.utc)

        stale = check_stale_data(last_bar_time, timeframe, now=now)
        if not stale.ok:
            return self._skip(idea, stale.reason or "stale data")

        stop_check = self.emergency_stop.check()
        if not stop_check.ok:
            return self._skip(idea, stop_check.reason or "emergency stop active")

        if idea.direction not in ("BUY", "SELL"):
            return self._skip(idea, f"Signal is {idea.direction}, nothing to execute")

        existing = get_open_positions(symbol=broker_symbol, magic=self.settings.magic_number)
        if existing:
            return self._skip(idea, f"Existing open position {existing[0].ticket} for {broker_symbol} - skipping to avoid a duplicate/pyramided entry")

        account_state = build_account_state(self.settings, self.equity_tracker, self.journal, now)
        risk_result = self.risk_manager.evaluate(idea, account_state, spec, now, current_spread_points=idea.spread_points)
        if not risk_result.approved:
            if risk_result.reason_code in _NOTIFY_WORTHY_REASON_CODES:
                self.notifier.notify_risk_halt(risk_result.reason_code, risk_result.blocking_reason or "")
            return self._skip(idea, risk_result.blocking_reason or "risk manager rejected", risk_result)

        if self.settings.trading_mode == TradingMode.DRY_RUN:
            logger.info(
                f"[DRY_RUN] Would place {idea.direction} {risk_result.volume} lots {broker_symbol} "
                f"entry={idea.entry} sl={idea.stop_loss} tp={idea.take_profit} risk=${risk_result.risk_amount:.2f}"
            )
            self.journal.record(
                {
                    "symbol": idea.symbol,
                    "direction": idea.direction,
                    "executed": False,
                    "dry_run": True,
                    "entry": idea.entry,
                    "stop_loss": idea.stop_loss,
                    "take_profit": idea.take_profit,
                    "volume": risk_result.volume,
                    "risk_amount": risk_result.risk_amount,
                    "reward_risk": idea.reward_risk,
                    "confidence": idea.confidence,
                    "reasons": idea.reasons,
                }
            )
            return ExecutionResult(executed=False, dry_run=True, skipped_reason=None, idea=idea, risk_result=risk_result, order_result=None)

        account_info = get_account_info()
        if self.settings.trading_mode == TradingMode.DEMO and not is_demo_account(account_info):
            reason = "TRADING_MODE=DEMO but the connected MT5 account is not flagged as a demo account"
            self.emergency_stop.trip(reason)
            self.notifier.notify_emergency_stop(reason)
            return self._skip(idea, "SAFETY: config says DEMO but the connected account is not a demo account - emergency stop tripped", risk_result)
        if self.settings.trading_mode == TradingMode.LIVE and not is_real_account(account_info):
            return self._skip(idea, "SAFETY: config says LIVE but the connected account is not flagged as real - refusing to trade", risk_result)

        order_result = submit_market_order(
            broker_symbol,
            idea.direction,
            risk_result.volume,
            idea.stop_loss,
            idea.take_profit,
            deviation_points=self.deviation_points,
            magic=self.settings.magic_number,
            comment=(idea.reasons[0] if idea.reasons else "signal"),
        )

        if not order_result.success:
            self.error_tripwire.record_failure()
            self.notifier.notify_order_failed(idea.symbol, idea.direction, order_result.retcode_description)
            self.journal.record(
                {
                    "symbol": idea.symbol,
                    "direction": idea.direction,
                    "executed": False,
                    "dry_run": False,
                    "order_failed": True,
                    "retcode_description": order_result.retcode_description,
                    "reasons": idea.reasons,
                }
            )
            return ExecutionResult(executed=False, dry_run=False, skipped_reason=order_result.retcode_description, idea=idea, risk_result=risk_result, order_result=order_result)

        self.error_tripwire.record_success()

        positions_after = get_open_positions(symbol=broker_symbol, magic=self.settings.magic_number)
        verified = any(p.ticket == order_result.ticket for p in positions_after)
        if not verified:
            logger.warning(f"Order {order_result.ticket} reported success but position was not found in positions_get() afterward")

        self.notifier.notify_trade_opened(idea.symbol, idea.direction, order_result.volume, order_result.price, idea.stop_loss, idea.take_profit, order_result.ticket)

        self.journal.record(
            {
                "symbol": idea.symbol,
                "direction": idea.direction,
                "executed": True,
                "dry_run": False,
                "verified": verified,
                "ticket": order_result.ticket,
                "entry": order_result.price,
                "stop_loss": idea.stop_loss,
                "take_profit": idea.take_profit,
                "volume": order_result.volume,
                "risk_amount": risk_result.risk_amount,
                "reward_risk": idea.reward_risk,
                "confidence": idea.confidence,
                "reasons": idea.reasons,
            }
        )
        return ExecutionResult(executed=True, dry_run=False, skipped_reason=None, idea=idea, risk_result=risk_result, order_result=order_result)
