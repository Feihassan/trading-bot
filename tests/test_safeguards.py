from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.risk.safeguards import (
    ConsecutiveErrorTripwire,
    EmergencyStop,
    check_spread_anomaly,
    check_stale_data,
    combine_checks,
)


class TestEmergencyStop:
    def test_not_tripped_by_default(self, tmp_path):
        stop = EmergencyStop(tmp_path / "STOP")
        assert not stop.is_tripped()
        assert stop.check().ok

    def test_trip_and_check(self, tmp_path):
        stop = EmergencyStop(tmp_path / "STOP")
        stop.trip("manual test trip")
        assert stop.is_tripped()
        result = stop.check()
        assert not result.ok
        assert "manual test trip" in result.reason

    def test_reset_clears_flag(self, tmp_path):
        stop = EmergencyStop(tmp_path / "STOP")
        stop.trip("x")
        stop.reset()
        assert not stop.is_tripped()
        assert stop.check().ok

    def test_survives_new_instance_same_path(self, tmp_path):
        path = tmp_path / "STOP"
        EmergencyStop(path).trip("persisted")
        assert EmergencyStop(path).is_tripped()


class TestStaleData:
    def test_fresh_bar_is_ok(self):
        now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
        last_bar = now - timedelta(minutes=10)
        result = check_stale_data(last_bar, "M15", now=now)
        assert result.ok

    def test_stale_bar_fails(self):
        now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
        last_bar = now - timedelta(hours=3)
        result = check_stale_data(last_bar, "M15", now=now)
        assert not result.ok
        assert "stale" in result.reason.lower()

    def test_unknown_timeframe_does_not_block(self):
        now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
        last_bar = now - timedelta(days=5)
        result = check_stale_data(last_bar, "M2", now=now)
        assert result.ok


class TestSpreadAnomaly:
    def test_normal_spread_ok(self):
        history = pd.Series([10, 11, 9, 10, 12, 10, 9, 11, 10, 10] * 3)
        result = check_spread_anomaly(history, current_spread=12, anomaly_multiple=3.0)
        assert result.ok

    def test_anomalous_spread_blocked(self):
        history = pd.Series([10, 11, 9, 10, 12, 10, 9, 11, 10, 10] * 3)
        result = check_spread_anomaly(history, current_spread=100, anomaly_multiple=3.0)
        assert not result.ok

    def test_insufficient_history_does_not_block(self):
        history = pd.Series([10, 11, 9])
        result = check_spread_anomaly(history, current_spread=500, min_history=20)
        assert result.ok


class TestConsecutiveErrorTripwire:
    def test_trips_after_max_consecutive_failures(self):
        wire = ConsecutiveErrorTripwire(max_consecutive=3)
        assert wire.check().ok
        wire.record_failure()
        wire.record_failure()
        assert wire.check().ok
        wire.record_failure()
        assert not wire.check().ok

    def test_success_resets_counter(self):
        wire = ConsecutiveErrorTripwire(max_consecutive=2)
        wire.record_failure()
        wire.record_success()
        wire.record_failure()
        assert wire.check().ok


class TestCombineChecks:
    def test_all_ok_passes(self):
        from app.risk.safeguards import SafetyCheckResult

        result = combine_checks(SafetyCheckResult(ok=True), SafetyCheckResult(ok=True))
        assert result.ok

    def test_first_failure_short_circuits(self):
        from app.risk.safeguards import SafetyCheckResult

        result = combine_checks(SafetyCheckResult(ok=True), SafetyCheckResult(ok=False, reason="bad"), SafetyCheckResult(ok=True))
        assert not result.ok
        assert result.reason == "bad"
