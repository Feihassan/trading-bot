"""
Two small pieces of persistent state the execution engine needs that
don't yet have a real database to live in (spec section 21's database
work is a later phase):

- `EquityTracker`: day-start/week-start/peak equity, so the risk manager
  can enforce daily/weekly loss limits and a max-drawdown halt using real
  history rather than only the current snapshot. Backed by a small JSON
  file rather than memory, specifically so it survives a bot restart -
  restarting the process must never reset "how much have I lost today".
- `TradeJournal`: an append-only JSON-lines log of every execution
  decision (approved or rejected, dry-run or real). This is a genuine,
  working implementation of spec section 21's trade journal, not a stub -
  it's just backed by a file instead of SQLite for now. Its record shape
  is deliberately flat/JSON-serializable so a later migration to the real
  database is a matter of reading this file in, not redesigning the schema.

Design decision for `build_account_state`: exposure-by-currency is
computed only from OUR OWN open positions (filtered by magic number) that
already carry a stop loss, using the same loss-per-lot math as position
sizing. A position without a stop loss contributes nothing measurable and
is logged as a warning - silently ignoring it would be worse than being
explicit that exposure tracking is incomplete for that position.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings
from app.logging_setup import get_logger
from app.mt5.account import Position, get_account_info, get_open_positions
from app.mt5.market_data import get_symbol_spec
from app.risk.position_sizing import loss_per_lot
from app.risk.risk_manager import AccountState

logger = get_logger("execution.trade_manager")


@dataclass
class EquityState:
    day_key: str
    week_key: str
    day_start_equity: float
    week_start_equity: float
    peak_equity: float


class EquityTracker:
    def __init__(self, path: str | Path = "./data/equity_state.json"):
        self.path = Path(path)

    def _load(self) -> EquityState | None:
        if not self.path.exists():
            return None
        try:
            return EquityState(**json.loads(self.path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError, OSError):
            logger.warning(f"Could not read equity state from {self.path}; starting fresh")
            return None

    def _save(self, state: EquityState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(state)), encoding="utf-8")

    def update(self, current_equity: float, now: datetime | None = None) -> EquityState:
        now = now or datetime.now(timezone.utc)
        day_key = now.strftime("%Y-%m-%d")
        week_key = now.strftime("%G-W%V")  # ISO year-week: correct across year boundaries

        state = self._load()
        if state is None:
            state = EquityState(day_key, week_key, current_equity, current_equity, current_equity)
        else:
            if state.day_key != day_key:
                state.day_key = day_key
                state.day_start_equity = current_equity
            if state.week_key != week_key:
                state.week_key = week_key
                state.week_start_equity = current_equity
            state.peak_equity = max(state.peak_equity, current_equity)

        self._save(state)
        return state


class TradeJournal:
    def __init__(self, path: str | Path = "./data/trade_journal.jsonl"):
        self.path = Path(path)

    def record(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"timestamp": datetime.now(timezone.utc).isoformat(), **entry}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


def _position_exposure(position: Position) -> tuple[str, float] | None:
    if position.sl == 0:
        logger.warning(f"Position {position.ticket} ({position.symbol}) has no stop loss - excluded from exposure tracking")
        return None
    try:
        spec = get_symbol_spec(position.symbol)
    except Exception as exc:  # noqa: BLE001 - a spec lookup failure must not crash risk evaluation
        logger.warning(f"Could not fetch symbol spec for {position.symbol}: {exc}")
        return None
    distance = abs(position.price_open - position.sl)
    risk_amount = loss_per_lot(distance, spec) * position.volume
    return spec.currency_base, risk_amount


def _count_trades_today(journal: TradeJournal, now: datetime) -> int:
    today = now.strftime("%Y-%m-%d")
    return sum(1 for entry in journal.read_all() if entry.get("executed") and str(entry.get("timestamp", "")).startswith(today))


def build_account_state(
    settings: Settings, equity_tracker: EquityTracker, journal: TradeJournal, now: datetime | None = None
) -> AccountState:
    now = now or datetime.now(timezone.utc)
    info = get_account_info()
    positions = get_open_positions(magic=settings.magic_number)
    equity_state = equity_tracker.update(info.equity, now)

    exposure_by_currency: dict[str, float] = {}
    for position in positions:
        result = _position_exposure(position)
        if result is None:
            continue
        currency, amount = result
        exposure_by_currency[currency] = exposure_by_currency.get(currency, 0.0) + amount

    return AccountState(
        equity=info.equity,
        balance=info.balance,
        day_start_equity=equity_state.day_start_equity,
        week_start_equity=equity_state.week_start_equity,
        peak_equity=equity_state.peak_equity,
        open_positions_count=len(positions),
        trades_today=_count_trades_today(journal, now),
        exposure_by_currency=exposure_by_currency,
    )
