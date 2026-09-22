from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import app.execution.executor as executor_mod
from app.config import Settings, TradingMode
from app.execution.executor import Executor
from app.execution.trade_manager import EquityTracker, TradeJournal
from app.mt5.market_data import SymbolSpec
from app.mt5.orders import OrderResult
from app.risk.risk_manager import AccountState, RiskCheckResult
from app.risk.safeguards import EmergencyStop
from app.strategy.signals import TradeIdea

SPEC = SymbolSpec(
    name="EURUSD", digits=5, point=0.00001, spread=10, spread_float=True, trade_contract_size=100_000.0,
    volume_min=0.01, volume_max=100.0, volume_step=0.01, trade_tick_value=10.0, trade_tick_size=0.0001,
    currency_base="EUR", currency_profit="USD", currency_margin="EUR",
)


def make_idea(**overrides) -> TradeIdea:
    defaults = dict(
        symbol="EURUSD", timestamp=None, direction="BUY", confidence=70.0,
        entry=1.1000, stop_loss=1.0950, take_profit=1.1100, reward_risk=2.0,
        technical_score=80.0, technical_direction="BUY", structure_trend="UPTREND",
        regime="STRONG_BULLISH_TREND", ml_probabilities=None, ml_direction=None,
        spread_points=5.0, reasons=["test reason"], blocking_reasons=[],
    )
    defaults.update(overrides)
    return TradeIdea(**defaults)


class FakeRiskManager:
    def __init__(self, result: RiskCheckResult):
        self.result = result
        self.evaluate_called = False

    def evaluate(self, *args, **kwargs) -> RiskCheckResult:
        self.evaluate_called = True
        return self.result


APPROVED = RiskCheckResult(approved=True, volume=0.2, risk_amount=100.0, reasons=["ok"], blocking_reason=None, reason_code="APPROVED")
REJECTED = RiskCheckResult(
    approved=False, volume=None, risk_amount=None, reasons=["no"],
    blocking_reason="daily loss limit reached", reason_code="DAILY_LOSS_LIMIT",
)
REJECTED_ROUTINE = RiskCheckResult(
    approved=False, volume=None, risk_amount=None, reasons=["no"],
    blocking_reason="spread too wide", reason_code="SPREAD_TOO_WIDE",
)


class FakeNotifier:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name, args))
            return True

        return record


def make_executor(tmp_path, settings: Settings, risk_manager, emergency_stop=None, notifier=None) -> Executor:
    return Executor(
        settings=settings,
        risk_manager=risk_manager,
        equity_tracker=EquityTracker(tmp_path / "equity.json"),
        journal=TradeJournal(tmp_path / "journal.jsonl"),
        emergency_stop=emergency_stop or EmergencyStop(tmp_path / "STOP"),
        notifier=notifier or FakeNotifier(),
    )


def settings(**overrides) -> Settings:
    defaults = dict(_env_file=None)
    defaults.update(overrides)
    return Settings(**defaults)


class TestWaitSignalNeverReachesRiskManager:
    def test_wait_idea_short_circuits_before_duplicate_or_risk_check(self, tmp_path, monkeypatch):
        called = {"positions": False}
        monkeypatch.setattr(executor_mod, "get_open_positions", lambda **kw: called.update(positions=True) or [])
        rm = FakeRiskManager(APPROVED)
        ex = make_executor(tmp_path, settings(TRADING_MODE="DRY_RUN"), rm)

        result = ex.execute_signal(make_idea(direction="WAIT"), "EURUSD", SPEC, datetime.now(timezone.utc), "H1")
        assert not result.executed
        assert not called["positions"]
        assert not rm.evaluate_called


class TestDuplicatePositionPrevention:
    def test_existing_open_position_skips_before_risk_check(self, tmp_path, monkeypatch):
        monkeypatch.setattr(executor_mod, "get_open_positions", lambda **kw: [SimpleNamespace(ticket=1)])
        rm = FakeRiskManager(APPROVED)
        ex = make_executor(tmp_path, settings(TRADING_MODE="DRY_RUN"), rm)

        result = ex.execute_signal(make_idea(), "EURUSD", SPEC, datetime.now(timezone.utc), "H1")
        assert not result.executed
        assert "duplicate" in result.skipped_reason.lower() or "existing" in result.skipped_reason.lower()
        assert not rm.evaluate_called


class TestDryRunNeverSubmitsOrders:
    def test_dry_run_approved_trade_does_not_call_submit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(executor_mod, "get_open_positions", lambda **kw: [])
        submitted = {"called": False}
        monkeypatch.setattr(executor_mod, "submit_market_order", lambda *a, **kw: submitted.update(called=True))
        monkeypatch.setattr(executor_mod, "build_account_state", lambda *a, **kw: AccountState(10_000, 10_000, 10_000, 10_000, 10_000, 0, 0))

        rm = FakeRiskManager(APPROVED)
        ex = make_executor(tmp_path, settings(TRADING_MODE="DRY_RUN"), rm)
        result = ex.execute_signal(make_idea(), "EURUSD", SPEC, datetime.now(timezone.utc), "H1")

        assert result.dry_run
        assert not result.executed
        assert not submitted["called"]
        assert rm.evaluate_called


class TestRiskRejectionBlocksExecution:
    def test_rejected_by_risk_manager_never_submits(self, tmp_path, monkeypatch):
        monkeypatch.setattr(executor_mod, "get_open_positions", lambda **kw: [])
        submitted = {"called": False}
        monkeypatch.setattr(executor_mod, "submit_market_order", lambda *a, **kw: submitted.update(called=True))
        monkeypatch.setattr(executor_mod, "build_account_state", lambda *a, **kw: AccountState(10_000, 10_000, 10_000, 10_000, 10_000, 0, 0))

        rm = FakeRiskManager(REJECTED)
        ex = make_executor(tmp_path, settings(TRADING_MODE="DEMO"), rm)
        result = ex.execute_signal(make_idea(), "EURUSD", SPEC, datetime.now(timezone.utc), "H1")

        assert not result.executed
        assert result.skipped_reason == "daily loss limit reached"
        assert not submitted["called"]


class TestAccountTypeSafetyCheck:
    def test_demo_mode_against_real_account_trips_emergency_stop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(executor_mod, "get_open_positions", lambda **kw: [])
        monkeypatch.setattr(executor_mod, "build_account_state", lambda *a, **kw: AccountState(10_000, 10_000, 10_000, 10_000, 10_000, 0, 0))
        monkeypatch.setattr(executor_mod, "get_account_info", lambda: SimpleNamespace(trade_mode=2))  # REAL, not demo
        submitted = {"called": False}
        monkeypatch.setattr(executor_mod, "submit_market_order", lambda *a, **kw: submitted.update(called=True))

        stop = EmergencyStop(tmp_path / "STOP")
        rm = FakeRiskManager(APPROVED)
        ex = make_executor(tmp_path, settings(TRADING_MODE="DEMO"), rm, emergency_stop=stop)
        result = ex.execute_signal(make_idea(), "EURUSD", SPEC, datetime.now(timezone.utc), "H1")

        assert not result.executed
        assert not submitted["called"]
        assert stop.is_tripped()

    def test_demo_mode_against_demo_account_proceeds_to_submit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(executor_mod, "get_open_positions", lambda **kw: [])
        monkeypatch.setattr(executor_mod, "build_account_state", lambda *a, **kw: AccountState(10_000, 10_000, 10_000, 10_000, 10_000, 0, 0))
        monkeypatch.setattr(executor_mod, "get_account_info", lambda: SimpleNamespace(trade_mode=0))  # DEMO
        monkeypatch.setattr(
            executor_mod,
            "submit_market_order",
            lambda *a, **kw: OrderResult(True, 10009, "DONE", ticket=555, volume=0.2, price=1.1005, request={}),
        )

        rm = FakeRiskManager(APPROVED)
        ex = make_executor(tmp_path, settings(TRADING_MODE="DEMO"), rm)
        result = ex.execute_signal(make_idea(), "EURUSD", SPEC, datetime.now(timezone.utc), "H1")

        assert result.executed
        assert result.order_result.ticket == 555


class TestOrderFailureHandling:
    def test_failed_submission_records_failure_and_does_not_mark_executed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(executor_mod, "get_open_positions", lambda **kw: [])
        monkeypatch.setattr(executor_mod, "build_account_state", lambda *a, **kw: AccountState(10_000, 10_000, 10_000, 10_000, 10_000, 0, 0))
        monkeypatch.setattr(executor_mod, "get_account_info", lambda: SimpleNamespace(trade_mode=0))
        monkeypatch.setattr(
            executor_mod, "submit_market_order", lambda *a, **kw: OrderResult(False, 10004, "REQUOTE", ticket=None, volume=None, price=None, request={})
        )

        rm = FakeRiskManager(APPROVED)
        ex = make_executor(tmp_path, settings(TRADING_MODE="DEMO"), rm)
        result = ex.execute_signal(make_idea(), "EURUSD", SPEC, datetime.now(timezone.utc), "H1")

        assert not result.executed
        assert ex.error_tripwire.consecutive_failures == 1


class TestStaleDataBlocks:
    def test_stale_last_bar_skips_before_anything_else(self, tmp_path, monkeypatch):
        monkeypatch.setattr(executor_mod, "get_open_positions", lambda **kw: [SimpleNamespace(ticket=1)])  # would also fail here
        rm = FakeRiskManager(APPROVED)
        ex = make_executor(tmp_path, settings(TRADING_MODE="DRY_RUN"), rm)

        stale_time = datetime(2020, 1, 1, tzinfo=timezone.utc)  # ancient
        result = ex.execute_signal(make_idea(), "EURUSD", SPEC, stale_time, "H1")
        assert not result.executed
        assert "stale" in result.skipped_reason.lower()


class TestNotifications:
    def test_successful_execution_notifies_trade_opened(self, tmp_path, monkeypatch):
        monkeypatch.setattr(executor_mod, "get_open_positions", lambda **kw: [])
        monkeypatch.setattr(executor_mod, "build_account_state", lambda *a, **kw: AccountState(10_000, 10_000, 10_000, 10_000, 10_000, 0, 0))
        monkeypatch.setattr(executor_mod, "get_account_info", lambda: SimpleNamespace(trade_mode=0))
        monkeypatch.setattr(
            executor_mod, "submit_market_order", lambda *a, **kw: OrderResult(True, 10009, "DONE", ticket=555, volume=0.2, price=1.1005, request={})
        )

        notifier = FakeNotifier()
        rm = FakeRiskManager(APPROVED)
        ex = make_executor(tmp_path, settings(TRADING_MODE="DEMO"), rm, notifier=notifier)
        ex.execute_signal(make_idea(), "EURUSD", SPEC, datetime.now(timezone.utc), "H1")

        assert any(name == "notify_trade_opened" for name, _ in notifier.calls)

    def test_critical_risk_rejection_notifies_risk_halt(self, tmp_path, monkeypatch):
        monkeypatch.setattr(executor_mod, "get_open_positions", lambda **kw: [])
        monkeypatch.setattr(executor_mod, "build_account_state", lambda *a, **kw: AccountState(10_000, 10_000, 10_000, 10_000, 10_000, 0, 0))

        notifier = FakeNotifier()
        rm = FakeRiskManager(REJECTED)  # reason_code=DAILY_LOSS_LIMIT
        ex = make_executor(tmp_path, settings(TRADING_MODE="DRY_RUN"), rm, notifier=notifier)
        ex.execute_signal(make_idea(), "EURUSD", SPEC, datetime.now(timezone.utc), "H1")

        assert any(name == "notify_risk_halt" for name, _ in notifier.calls)

    def test_routine_rejection_does_not_notify(self, tmp_path, monkeypatch):
        monkeypatch.setattr(executor_mod, "get_open_positions", lambda **kw: [])
        monkeypatch.setattr(executor_mod, "build_account_state", lambda *a, **kw: AccountState(10_000, 10_000, 10_000, 10_000, 10_000, 0, 0))

        notifier = FakeNotifier()
        rm = FakeRiskManager(REJECTED_ROUTINE)  # reason_code=SPREAD_TOO_WIDE
        ex = make_executor(tmp_path, settings(TRADING_MODE="DRY_RUN"), rm, notifier=notifier)
        ex.execute_signal(make_idea(), "EURUSD", SPEC, datetime.now(timezone.utc), "H1")

        assert notifier.calls == []

    def test_order_failure_notifies(self, tmp_path, monkeypatch):
        monkeypatch.setattr(executor_mod, "get_open_positions", lambda **kw: [])
        monkeypatch.setattr(executor_mod, "build_account_state", lambda *a, **kw: AccountState(10_000, 10_000, 10_000, 10_000, 10_000, 0, 0))
        monkeypatch.setattr(executor_mod, "get_account_info", lambda: SimpleNamespace(trade_mode=0))
        monkeypatch.setattr(
            executor_mod, "submit_market_order", lambda *a, **kw: OrderResult(False, 10004, "REQUOTE", ticket=None, volume=None, price=None, request={})
        )

        notifier = FakeNotifier()
        rm = FakeRiskManager(APPROVED)
        ex = make_executor(tmp_path, settings(TRADING_MODE="DEMO"), rm, notifier=notifier)
        ex.execute_signal(make_idea(), "EURUSD", SPEC, datetime.now(timezone.utc), "H1")

        assert any(name == "notify_order_failed" for name, _ in notifier.calls)
