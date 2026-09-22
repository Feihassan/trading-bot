from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import app.execution.trade_manager as tm_mod
from app.execution.trade_manager import EquityTracker, TradeJournal, build_account_state
from app.mt5.market_data import SymbolSpec


class TestEquityTracker:
    def test_first_update_seeds_all_values_from_current_equity(self, tmp_path):
        tracker = EquityTracker(tmp_path / "equity.json")
        now = datetime(2024, 6, 3, 10, 0, tzinfo=timezone.utc)
        state = tracker.update(10_000.0, now)
        assert state.day_start_equity == 10_000.0
        assert state.week_start_equity == 10_000.0
        assert state.peak_equity == 10_000.0

    def test_same_day_does_not_reset_day_start(self, tmp_path):
        tracker = EquityTracker(tmp_path / "equity.json")
        day1_morning = datetime(2024, 6, 3, 9, 0, tzinfo=timezone.utc)
        day1_evening = datetime(2024, 6, 3, 18, 0, tzinfo=timezone.utc)
        tracker.update(10_000.0, day1_morning)
        state = tracker.update(9_500.0, day1_evening)
        assert state.day_start_equity == 10_000.0  # unchanged within the same day

    def test_new_day_resets_day_start_but_not_week_start(self, tmp_path):
        tracker = EquityTracker(tmp_path / "equity.json")
        day1 = datetime(2024, 6, 3, 9, 0, tzinfo=timezone.utc)  # Monday
        day2 = datetime(2024, 6, 4, 9, 0, tzinfo=timezone.utc)  # Tuesday, same week
        tracker.update(10_000.0, day1)
        state = tracker.update(9_800.0, day2)
        assert state.day_start_equity == 9_800.0
        assert state.week_start_equity == 10_000.0

    def test_new_week_resets_week_start(self, tmp_path):
        tracker = EquityTracker(tmp_path / "equity.json")
        this_week = datetime(2024, 6, 3, 9, 0, tzinfo=timezone.utc)  # Monday
        next_week = datetime(2024, 6, 10, 9, 0, tzinfo=timezone.utc)  # next Monday
        tracker.update(10_000.0, this_week)
        state = tracker.update(9_800.0, next_week)
        assert state.week_start_equity == 9_800.0

    def test_peak_equity_is_monotonic(self, tmp_path):
        tracker = EquityTracker(tmp_path / "equity.json")
        now = datetime(2024, 6, 3, 9, 0, tzinfo=timezone.utc)
        tracker.update(10_000.0, now)
        tracker.update(11_000.0, now + timedelta(hours=1))
        state = tracker.update(10_500.0, now + timedelta(hours=2))
        assert state.peak_equity == 11_000.0  # never drops when equity dips

    def test_state_persists_across_new_tracker_instances(self, tmp_path):
        path = tmp_path / "equity.json"
        now = datetime(2024, 6, 3, 9, 0, tzinfo=timezone.utc)
        EquityTracker(path).update(10_000.0, now)
        state = EquityTracker(path).update(10_100.0, now + timedelta(minutes=5))
        assert state.day_start_equity == 10_000.0

    def test_corrupted_state_file_is_treated_as_fresh_start(self, tmp_path):
        path = tmp_path / "equity.json"
        path.write_text("not valid json{{{", encoding="utf-8")
        now = datetime(2024, 6, 3, 9, 0, tzinfo=timezone.utc)
        state = EquityTracker(path).update(10_000.0, now)
        assert state.day_start_equity == 10_000.0  # recovered rather than raising


class TestTradeJournal:
    def test_record_and_read_all(self, tmp_path):
        journal = TradeJournal(tmp_path / "journal.jsonl")
        journal.record({"symbol": "EURUSD", "executed": True})
        journal.record({"symbol": "GBPUSD", "executed": False})
        entries = journal.read_all()
        assert len(entries) == 2
        assert entries[0]["symbol"] == "EURUSD"
        assert "timestamp" in entries[0]

    def test_read_all_on_missing_file_returns_empty(self, tmp_path):
        journal = TradeJournal(tmp_path / "does_not_exist.jsonl")
        assert journal.read_all() == []


SPEC = SymbolSpec(
    name="EURUSD", digits=5, point=0.00001, spread=10, spread_float=True, trade_contract_size=100_000.0,
    volume_min=0.01, volume_max=100.0, volume_step=0.01, trade_tick_value=10.0, trade_tick_size=0.0001,
    currency_base="EUR", currency_profit="USD", currency_margin="EUR",
)


class TestBuildAccountState:
    def test_combines_live_account_and_persisted_equity(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            tm_mod,
            "get_account_info",
            lambda: SimpleNamespace(equity=9_800.0, balance=10_000.0, trade_mode=0),
        )
        monkeypatch.setattr(tm_mod, "get_open_positions", lambda magic=None: [])
        from app.config import Settings

        settings = Settings(_env_file=None)
        tracker = EquityTracker(tmp_path / "equity.json")
        journal = TradeJournal(tmp_path / "journal.jsonl")
        now = datetime(2024, 6, 3, 12, 0, tzinfo=timezone.utc)

        state = build_account_state(settings, tracker, journal, now)
        assert state.equity == 9_800.0
        assert state.day_start_equity == 9_800.0  # first call of the day seeds it
        assert state.open_positions_count == 0
        assert state.exposure_by_currency == {}

    def test_exposure_computed_from_positions_with_stop_loss(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tm_mod, "get_account_info", lambda: SimpleNamespace(equity=10_000.0, balance=10_000.0, trade_mode=0))
        position = SimpleNamespace(ticket=1, symbol="EURUSD", volume=0.2, price_open=1.1000, sl=1.0950, tp=1.1100, magic=1)
        monkeypatch.setattr(tm_mod, "get_open_positions", lambda magic=None: [position])
        monkeypatch.setattr(tm_mod, "get_symbol_spec", lambda symbol: SPEC)

        from app.config import Settings

        settings = Settings(_env_file=None)
        tracker = EquityTracker(tmp_path / "equity.json")
        journal = TradeJournal(tmp_path / "journal.jsonl")
        now = datetime(2024, 6, 3, 12, 0, tzinfo=timezone.utc)

        state = build_account_state(settings, tracker, journal, now)
        # 50 pips * $10/pip * 0.2 lots = $100 at risk in EUR
        assert state.exposure_by_currency["EUR"] == pytest.approx(100.0)

    def test_symbol_spec_lookup_failure_excludes_position_without_crashing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tm_mod, "get_account_info", lambda: SimpleNamespace(equity=10_000.0, balance=10_000.0, trade_mode=0))
        position = SimpleNamespace(ticket=1, symbol="EURUSD", volume=0.2, price_open=1.1000, sl=1.0950, tp=1.1100, magic=1)
        monkeypatch.setattr(tm_mod, "get_open_positions", lambda magic=None: [position])

        def raise_error(symbol):
            raise RuntimeError("symbol not found")

        monkeypatch.setattr(tm_mod, "get_symbol_spec", raise_error)

        from app.config import Settings

        settings = Settings(_env_file=None)
        tracker = EquityTracker(tmp_path / "equity.json")
        journal = TradeJournal(tmp_path / "journal.jsonl")
        state = build_account_state(settings, tracker, journal, datetime(2024, 6, 3, 12, 0, tzinfo=timezone.utc))
        assert state.exposure_by_currency == {}  # excluded, not raised

    def test_position_without_stop_loss_excluded_from_exposure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tm_mod, "get_account_info", lambda: SimpleNamespace(equity=10_000.0, balance=10_000.0, trade_mode=0))
        position = SimpleNamespace(ticket=1, symbol="EURUSD", volume=0.2, price_open=1.1000, sl=0.0, tp=0.0, magic=1)
        monkeypatch.setattr(tm_mod, "get_open_positions", lambda magic=None: [position])

        from app.config import Settings

        settings = Settings(_env_file=None)
        tracker = EquityTracker(tmp_path / "equity.json")
        journal = TradeJournal(tmp_path / "journal.jsonl")
        state = build_account_state(settings, tracker, journal, datetime(2024, 6, 3, 12, 0, tzinfo=timezone.utc))
        assert state.exposure_by_currency == {}

    def test_trades_today_counts_only_executed_entries_from_today(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tm_mod, "get_account_info", lambda: SimpleNamespace(equity=10_000.0, balance=10_000.0, trade_mode=0))
        monkeypatch.setattr(tm_mod, "get_open_positions", lambda magic=None: [])

        from app.config import Settings

        settings = Settings(_env_file=None)
        tracker = EquityTracker(tmp_path / "equity.json")
        journal = TradeJournal(tmp_path / "journal.jsonl")

        journal.record({"symbol": "EURUSD", "executed": True})  # today
        journal.record({"symbol": "GBPUSD", "executed": False})  # skipped, doesn't count

        state = build_account_state(settings, tracker, journal, datetime.now(timezone.utc))
        assert state.trades_today == 1
