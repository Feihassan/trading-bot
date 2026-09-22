"""
Shared application state for the FastAPI server: the one live MT5
connection (MT5 supports exactly one session per terminal, so this is a
singleton by necessity, not convenience), plus the risk/execution
components built in earlier phases, held for the lifetime of the process.

Design decision: MT5's Python API is a stateful, NOT thread-safe module
(see app/mt5/connection.py). FastAPI route handlers here are declared
`async def` and call MT5 wrapper functions directly rather than offloading
them to a thread pool, specifically so every MT5 call happens on the
single event-loop thread - avoiding the thread-safety hazard entirely
rather than adding locks around it. For a dashboard polling every few
seconds this costs a few milliseconds of event-loop blocking per call,
which is an acceptable trade for correctness over throughput here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.model import TradingModel
from app.config import Settings
from app.execution.trade_manager import EquityTracker, TradeJournal
from app.mt5.connection import MT5Connection
from app.notifications.telegram import TelegramNotifier
from app.risk.risk_manager import RiskManager
from app.risk.safeguards import EmergencyStop


@dataclass
class AppState:
    settings: Settings
    connection: MT5Connection
    risk_manager: RiskManager
    equity_tracker: EquityTracker
    journal: TradeJournal
    emergency_stop: EmergencyStop
    notifier: TelegramNotifier
    model_cache: dict[str, TradingModel] = field(default_factory=dict)
    known_open_tickets: set[int] = field(default_factory=set)
