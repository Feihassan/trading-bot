"""
Pydantic response models for the dashboard API.

Design decision: these are separate from the internal dataclasses used
throughout app/ (AccountInfo, TradeIdea, BacktestMetrics, ...) rather than
serializing those directly. The internal shapes are free to change as the
trading logic evolves; the API contract is what the frontend depends on
and should change deliberately, not as a side effect of an unrelated
refactor elsewhere in the codebase.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ConnectionStatus(BaseModel):
    connected: bool
    trading_mode: str
    server: str | None = None
    login: int | None = None
    error: str | None = None


class AccountSummary(BaseModel):
    balance: float
    equity: float
    margin: float
    margin_free: float
    currency: str
    leverage: int
    trade_allowed: bool
    is_demo: bool
    day_start_equity: float
    week_start_equity: float
    peak_equity: float
    daily_pnl: float
    daily_pnl_pct: float
    weekly_pnl: float
    weekly_pnl_pct: float
    drawdown_from_peak_pct: float


class PositionOut(BaseModel):
    ticket: int
    symbol: str
    type: str
    volume: float
    price_open: float
    price_current: float
    sl: float
    tp: float
    profit: float
    swap: float
    open_time: int


class SymbolQuote(BaseModel):
    symbol: str
    broker_symbol: str
    bid: float
    ask: float
    spread_points: float


class SignalOut(BaseModel):
    symbol: str
    timestamp: str | None
    direction: str
    confidence: float
    entry: float | None
    stop_loss: float | None
    take_profit: float | None
    reward_risk: float | None
    technical_score: float
    technical_direction: str
    structure_trend: str
    regime: str
    ml_probabilities: dict[str, float] | None
    ml_direction: str | None
    spread_points: float | None
    reasons: list[str]
    blocking_reasons: list[str]
    llm_decision: str | None = None
    llm_confidence: float | None = None
    llm_reasoning: str | None = None
    llm_risk_flags: list[str] = []


class RiskLimits(BaseModel):
    risk_per_trade: float
    max_daily_loss: float
    max_weekly_loss: float
    max_open_trades: int
    max_trades_per_day: int
    max_exposure_per_currency: float
    max_spread_points: float
    min_risk_reward: float
    max_drawdown_halt: float
    trading_sessions: list[str]
    emergency_stop_active: bool
    emergency_stop_reason: str | None = None


class EmergencyStopRequest(BaseModel):
    reason: str = "Manual stop via dashboard"


class JournalEntryOut(BaseModel):
    timestamp: str
    symbol: str | None = None
    direction: str | None = None
    executed: bool | None = None
    dry_run: bool | None = None
    skipped_reason: str | None = None
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    volume: float | None = None
    risk_amount: float | None = None
    reward_risk: float | None = None
    confidence: float | None = None
    ticket: int | None = None
    reasons: list[str] | None = None


class BacktestRequest(BaseModel):
    symbol: str
    timeframe: str = "H1"
    # Upper-bounded so a request can't trigger an unbounded MT5 history
    # pull / in-memory backtest run (~10 years of daily-down-to-M15 data
    # is already a lot; there's no legitimate reason to ask for more in
    # one call).
    days: int = Field(default=365, gt=0, le=3650)
    initial_balance: float = Field(default=10_000.0, gt=0)
    risk_per_trade: float = Field(default=0.01, gt=0, le=0.5)
    min_risk_reward: float = Field(default=2.0, ge=0)
    use_ml: bool = False
    ml_horizon_bars: int = Field(default=8, gt=0, le=200)


class EquityPoint(BaseModel):
    time: str
    equity: float


class TradeOut(BaseModel):
    direction: str
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    volume: float
    pnl: float
    r_multiple: float
    exit_reason: str
    duration_bars: int


class LogLine(BaseModel):
    timestamp: str | None = None
    level: str | None = None
    logger: str | None = None
    message: str
    raw: str


class BacktestResponse(BaseModel):
    symbol: str
    timeframe: str
    bars_used: int
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    expectancy: float
    expectancy_r: float
    max_drawdown_pct: float
    sharpe_ratio: float | None
    net_return_pct: float
    longest_losing_streak: int
    final_balance: float
    equity_curve: list[EquityPoint]
    trades: list[TradeOut]
