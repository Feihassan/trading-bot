"""
Safety systems that sit alongside (not inside) the risk manager: an
emergency stop switch, and guards for the conditions spec section 24
calls out for automatic shutdown - stale data, abnormal spread, and
repeated errors.

Design decisions:
- `EmergencyStop` defaults to a small on-disk flag file, not just an
  in-memory bool, so that (a) it survives a process restart - if someone
  hit the kill switch, restarting the bot must not silently clear it -
  and (b) it can be triggered externally (a future dashboard "STOP"
  button, an ops script) without importing this process's Python objects.
- Every guard here returns a `SafetyCheckResult`, the same shape the risk
  manager uses, so they can be composed into a single "is it safe to
  trade at all right now" gate that runs before the risk manager's
  per-trade checks.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd


@dataclass
class SafetyCheckResult:
    ok: bool
    reason: str | None = None


class EmergencyStop:
    """File-backed kill switch. `trip()` writes the flag file (and an
    optional reason); `is_tripped()` is a cheap file-existence check;
    `reset()` requires the caller to explicitly clear it - never done
    automatically by any guard in this codebase."""

    def __init__(self, flag_path: str | Path = "./data/EMERGENCY_STOP"):
        self.flag_path = Path(flag_path)

    def trip(self, reason: str) -> None:
        self.flag_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat()
        self.flag_path.write_text(f"{timestamp} | {reason}\n", encoding="utf-8")

    def reset(self) -> None:
        if self.flag_path.exists():
            self.flag_path.unlink()

    def is_tripped(self) -> bool:
        return self.flag_path.exists()

    def check(self) -> SafetyCheckResult:
        if not self.is_tripped():
            return SafetyCheckResult(ok=True)
        try:
            reason = self.flag_path.read_text(encoding="utf-8").strip()
        except OSError:
            reason = "unknown"
        return SafetyCheckResult(ok=False, reason=f"Emergency stop is active: {reason}")


# Timeframe durations used to judge staleness - a bar older than roughly
# 2x its own period means data has stopped updating (feed issue, market
# closed unexpectedly, MT5 desync), not just "the last bar hasn't closed yet".
_TIMEFRAME_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def check_stale_data(last_bar_time: datetime, timeframe: str, now: datetime | None = None, stale_multiple: float = 2.5) -> SafetyCheckResult:
    now = now or datetime.now(timezone.utc)
    if last_bar_time.tzinfo is None:
        last_bar_time = last_bar_time.replace(tzinfo=timezone.utc)
    minutes = _TIMEFRAME_MINUTES.get(timeframe)
    if minutes is None:
        return SafetyCheckResult(ok=True)  # unknown timeframe - don't block on a guard we can't evaluate

    age = now - last_bar_time
    max_age = timedelta(minutes=minutes * stale_multiple)
    if age > max_age:
        return SafetyCheckResult(ok=False, reason=f"Market data is stale: last bar is {age} old (max allowed {max_age})")
    return SafetyCheckResult(ok=True)


def check_spread_anomaly(recent_spreads: pd.Series, current_spread: float, anomaly_multiple: float = 3.0, min_history: int = 20) -> SafetyCheckResult:
    """
    Flags a spread that's a large multiple of its own recent median -
    catching broker-side spread blowouts (news, thin liquidity, feed
    issues) that a single fixed MAX_SPREAD_POINTS threshold would miss if
    it were set loose enough to tolerate normal session-to-session
    variation.
    """
    history = recent_spreads.dropna()
    if len(history) < min_history:
        return SafetyCheckResult(ok=True)  # not enough history to judge "abnormal" yet
    median = statistics.median(history)
    if median <= 0:
        return SafetyCheckResult(ok=True)
    if current_spread > anomaly_multiple * median:
        return SafetyCheckResult(
            ok=False,
            reason=f"Spread {current_spread} is {current_spread / median:.1f}x the recent median {median:.1f} (limit {anomaly_multiple}x)",
        )
    return SafetyCheckResult(ok=True)


@dataclass
class ConsecutiveErrorTripwire:
    """
    Tracks consecutive failures (order rejections, connection drops, data
    errors) and reports itself unsafe once `max_consecutive` is hit. A
    single success resets the counter - this is about catching a
    persistent failure mode, not penalizing one-off transient errors.
    """

    max_consecutive: int = 5
    _count: int = field(default=0, init=False, repr=False)

    def record_success(self) -> None:
        self._count = 0

    def record_failure(self) -> None:
        self._count += 1

    @property
    def consecutive_failures(self) -> int:
        return self._count

    def check(self) -> SafetyCheckResult:
        if self._count >= self.max_consecutive:
            return SafetyCheckResult(ok=False, reason=f"{self._count} consecutive errors (limit {self.max_consecutive})")
        return SafetyCheckResult(ok=True)


def combine_checks(*results: SafetyCheckResult) -> SafetyCheckResult:
    for r in results:
        if not r.ok:
            return r
    return SafetyCheckResult(ok=True)
