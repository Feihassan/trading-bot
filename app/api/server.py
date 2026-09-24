"""
FastAPI server exposing the trading bot's real state to the dashboard:
live MT5 account/positions, on-demand signal generation, risk limits and
the emergency stop, the trade journal, and a backtest runner. Nothing
here is mocked - every endpoint calls the same modules the CLI/executor
use, against whatever MT5 account is configured in .env.

Run with:
    python -m app.api.server
or:
    uvicorn app.api.server:app --reload
"""
from __future__ import annotations

import asyncio
import json
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.ai.model import ModelType
from app.ai.trainer import train_and_evaluate
from app.backtesting.engine import BacktestConfig, run_backtest
from app.backtesting.example_strategy import make_ema_crossover_strategy, prepare_features as prepare_baseline_features
from app.backtesting.metrics import compute_metrics
from app.config import get_settings
from app.execution.trade_manager import EquityTracker, TradeJournal
from app.llm.client import call_llm
from app.llm.context import build_market_context
from app.llm.overlay import apply_llm_overlay
from app.logging_setup import configure_logging, get_logger
from app.mt5.account import get_account_info, get_closed_position_info, get_open_positions, is_demo_account
from app.mt5.connection import MT5Connection, MT5ConnectionError
from app.mt5.market_data import (
    MarketDataError,
    get_candles,
    get_candles_range,
    get_current_price,
    get_current_spread_points,
    get_symbol_spec,
    resolve_symbol,
)
from app.notifications.telegram import TelegramNotifier
from app.risk.risk_manager import RiskManager
from app.risk.safeguards import EmergencyStop
from app.strategy.signals import SignalEngineConfig, generate_signal

from .schemas import (
    AccountSummary,
    BacktestRequest,
    BacktestResponse,
    ConnectionStatus,
    EmergencyStopRequest,
    EquityPoint,
    JournalEntryOut,
    LogLine,
    PositionOut,
    RiskLimits,
    SignalOut,
    SymbolQuote,
    TradeOut,
)
from .state import AppState

logger = get_logger("api.server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_directories()
    configure_logging(log_dir=settings.log_dir, level=settings.log_level)

    notifier = TelegramNotifier(settings)
    connection = MT5Connection(settings)
    try:
        connection.connect()
    except MT5ConnectionError as exc:
        logger.error(f"Starting API without a live MT5 connection: {exc}")
        notifier.notify_disconnected(f"API server could not connect to MT5 on startup: {exc}")

    app.state.trading = AppState(
        settings=settings,
        connection=connection,
        risk_manager=RiskManager(settings),
        equity_tracker=EquityTracker(),
        journal=TradeJournal(),
        emergency_stop=EmergencyStop(),
        notifier=notifier,
    )
    watcher_task = asyncio.create_task(_closed_trade_watcher(app.state.trading))
    reconnect_task = asyncio.create_task(_mt5_reconnector(app.state.trading))
    yield
    watcher_task.cancel()
    reconnect_task.cancel()
    connection.shutdown()


async def _mt5_reconnector(state: AppState) -> None:
    """Reconnects to MT5 whenever the connection drops. Without this, a
    terminal restart (or starting the API before the terminal) left every
    MT5-backed endpoint returning 503 until the API process was restarted."""
    was_connected = state.connection.is_connected()
    while True:
        await asyncio.sleep(15.0)
        try:
            await asyncio.to_thread(state.connection.ensure_connected)
            if not was_connected:
                logger.info("MT5 connection restored")
                was_connected = True
        except Exception as exc:  # noqa: BLE001 - keep retrying until MT5 is back
            if was_connected:
                state.notifier.notify_disconnected(f"Lost MT5 connection, retrying every 15s: {exc}")
            logger.warning(f"MT5 reconnect failed: {exc}")
            was_connected = False


async def _closed_trade_watcher(state: AppState) -> None:
    """
    Runs once, independent of how many dashboard tabs are open (unlike
    the per-connection /ws/live loop below), so a closed position never
    triggers duplicate Telegram alerts. MT5 has no push notification for
    fills/closes, so this is the polling-based substitute: every tick,
    diff the currently-open ticket set against what we saw last tick, and
    for anything that disappeared, look up how it closed via MT5's own
    deal history (spec section 20: trade closed / SL hit / TP hit).
    """
    while True:
        try:
            positions = get_open_positions(magic=state.settings.magic_number)
            current_tickets = {p.ticket for p in positions}
            newly_closed = state.known_open_tickets - current_tickets
            for ticket in newly_closed:
                info = get_closed_position_info(ticket)
                if info is None:
                    continue
                state.notifier.notify_trade_closed(info.symbol, info.direction, info.exit_reason, info.entry_price, info.exit_price, info.pnl, info.ticket)
                state.journal.record(
                    {
                        "symbol": info.symbol,
                        "direction": info.direction,
                        "executed": False,  # this is a close event, not a new-position execution
                        "closed": True,
                        "ticket": info.ticket,
                        "exit_reason": info.exit_reason,
                        "entry": info.entry_price,
                        "exit_price": info.exit_price,
                        "pnl": info.pnl,
                    }
                )
            state.known_open_tickets = current_tickets
        except Exception as exc:  # noqa: BLE001 - one bad tick must not kill the watcher
            logger.warning(f"Closed-trade watcher tick failed: {exc}")
        await asyncio.sleep(5.0)


app = FastAPI(title="Trading Bot API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def enforce_api_key(request, call_next):
    """
    No-op when API_KEY is unset (the local-only default). When it is set
    (mandatory once API_HOST binds off loopback - see config.py's
    validator), every /api/* request must present it via the X-API-Key
    header, or get a 401 before touching any route logic. /ws/live isn't
    covered here (WebSocket handshakes don't go through this middleware
    the same way) - see the websocket handler's own check below.
    """
    api_key = getattr(getattr(request.app.state, "trading", None), "settings", None)
    api_key = api_key.api_key if api_key else None
    if api_key and request.url.path.startswith("/api/"):
        if request.headers.get("X-API-Key") != api_key:
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=401, content={"detail": "Missing or invalid X-API-Key header"})
    return await call_next(request)


def _state(app_: FastAPI) -> AppState:
    return app_.state.trading


@app.get("/api/status", response_model=ConnectionStatus)
async def get_status() -> ConnectionStatus:
    state = _state(app)
    if not state.connection.is_connected():
        return ConnectionStatus(connected=False, trading_mode=state.settings.trading_mode.value, error="Not connected to MT5")
    try:
        info = get_account_info()
        return ConnectionStatus(connected=True, trading_mode=state.settings.trading_mode.value, server=info.server, login=info.login)
    except Exception as exc:  # noqa: BLE001
        # Full detail goes to the server log only - a caller (any device
        # that can reach this port, if API_HOST is ever changed off
        # loopback) gets a generic, bounded message rather than raw MT5
        # internals (security review finding).
        logger.error(f"get_status: account info unavailable: {exc}")
        return ConnectionStatus(connected=False, trading_mode=state.settings.trading_mode.value, error="MT5 account info unavailable")


@app.get("/api/account", response_model=AccountSummary)
async def get_account() -> AccountSummary:
    state = _state(app)
    try:
        info = get_account_info()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"get_account: account info unavailable: {exc}")
        raise HTTPException(status_code=503, detail="MT5 account info unavailable")

    equity_state = state.equity_tracker.update(info.equity)
    daily_pnl = info.equity - equity_state.day_start_equity
    weekly_pnl = info.equity - equity_state.week_start_equity
    drawdown = (equity_state.peak_equity - info.equity) / equity_state.peak_equity if equity_state.peak_equity > 0 else 0.0

    return AccountSummary(
        balance=info.balance,
        equity=info.equity,
        margin=info.margin,
        margin_free=info.margin_free,
        currency=info.currency,
        leverage=info.leverage,
        trade_allowed=info.trade_allowed,
        is_demo=is_demo_account(info),
        day_start_equity=equity_state.day_start_equity,
        week_start_equity=equity_state.week_start_equity,
        peak_equity=equity_state.peak_equity,
        daily_pnl=daily_pnl,
        daily_pnl_pct=(daily_pnl / equity_state.day_start_equity) if equity_state.day_start_equity else 0.0,
        weekly_pnl=weekly_pnl,
        weekly_pnl_pct=(weekly_pnl / equity_state.week_start_equity) if equity_state.week_start_equity else 0.0,
        drawdown_from_peak_pct=drawdown,
    )


@app.get("/api/positions", response_model=list[PositionOut])
async def get_positions() -> list[PositionOut]:
    state = _state(app)
    try:
        positions = get_open_positions(magic=state.settings.magic_number)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"get_positions: MT5 positions unavailable: {exc}")
        raise HTTPException(status_code=503, detail="MT5 positions unavailable")
    return [
        PositionOut(
            ticket=p.ticket, symbol=p.symbol, type=p.type, volume=p.volume, price_open=p.price_open,
            price_current=p.price_current, sl=p.sl, tp=p.tp, profit=p.profit, swap=p.swap, open_time=p.open_time,
        )
        for p in positions
    ]


@app.get("/api/symbols", response_model=list[SymbolQuote])
async def get_symbols() -> list[SymbolQuote]:
    state = _state(app)
    quotes: list[SymbolQuote] = []
    for canonical, broker_symbol in state.settings.symbol_map.items():
        try:
            bid, ask = get_current_price(broker_symbol)
            spread = get_current_spread_points(broker_symbol)
            quotes.append(SymbolQuote(symbol=canonical, broker_symbol=broker_symbol, bid=bid, ask=ask, spread_points=spread))
        except MarketDataError as exc:
            logger.warning(f"Could not fetch quote for {canonical}: {exc}")
    return quotes


def _get_or_train_model(state: AppState, canonical: str, broker_symbol: str, timeframe: str, horizon_bars: int):
    cache_key = f"{canonical}:{timeframe}:{horizon_bars}"
    if cache_key in state.model_cache:
        return state.model_cache[cache_key]

    date_to = datetime.now(timezone.utc)
    date_from = date_to - timedelta(days=365)
    history = get_candles_range(broker_symbol, timeframe, date_from, date_to)
    result = train_and_evaluate(history, model_type=ModelType.RANDOM_FOREST, horizon_bars=horizon_bars, symbol=canonical, timeframe=timeframe, n_estimators=150)
    state.model_cache[cache_key] = result.model
    return result.model


@app.get("/api/signals/{symbol}", response_model=SignalOut)
async def get_signal(symbol: str, use_ml: bool = False, use_llm: bool = False, timeframe: str = "H1") -> SignalOut:
    state = _state(app)
    try:
        broker_symbol = resolve_symbol(symbol, state.settings)
    except MarketDataError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    try:
        df = get_candles(broker_symbol, timeframe, count=500)
    except MarketDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    model = None
    if use_ml:
        try:
            model = _get_or_train_model(state, symbol, broker_symbol, timeframe, horizon_bars=8)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Could not prepare ML model for {symbol}: {exc}")

    idea = generate_signal(df, symbol, model=model, config=SignalEngineConfig(max_spread_points=state.settings.max_spread_points, min_risk_reward=state.settings.min_risk_reward))

    if use_llm:
        try:
            positions = [
                {"ticket": p.ticket, "type": p.type, "volume": p.volume, "profit": p.profit}
                for p in get_open_positions(symbol=broker_symbol, magic=state.settings.magic_number)
            ]
            risk_state = {
                "risk_per_trade": state.settings.risk_per_trade,
                "max_daily_loss": state.settings.max_daily_loss,
                "emergency_stop_active": not state.emergency_stop.check().ok,
            }
            context = build_market_context(df, symbol, timeframe, current_positions=positions, risk_state=risk_state, ml_probabilities=idea.ml_probabilities)
            llm_result = call_llm(context, state.settings)
            idea = apply_llm_overlay(idea, llm_result)
        except Exception as exc:  # noqa: BLE001 - the LLM overlay is advisory; a failure here must not break the endpoint
            logger.warning(f"LLM overlay failed for {symbol}: {exc}")

    return SignalOut(
        symbol=idea.symbol,
        timestamp=str(idea.timestamp) if idea.timestamp is not None else None,
        direction=idea.direction,
        confidence=idea.confidence,
        entry=idea.entry,
        stop_loss=idea.stop_loss,
        take_profit=idea.take_profit,
        reward_risk=idea.reward_risk,
        technical_score=idea.technical_score,
        technical_direction=idea.technical_direction,
        structure_trend=idea.structure_trend,
        regime=idea.regime,
        ml_probabilities=idea.ml_probabilities,
        ml_direction=idea.ml_direction,
        spread_points=idea.spread_points,
        reasons=idea.reasons,
        blocking_reasons=idea.blocking_reasons,
        llm_decision=idea.llm_decision,
        llm_confidence=idea.llm_confidence,
        llm_reasoning=idea.llm_reasoning,
        llm_risk_flags=idea.llm_risk_flags,
    )


@app.get("/api/journal", response_model=list[JournalEntryOut])
async def get_journal(limit: int = Query(default=100, gt=0, le=2000)) -> list[JournalEntryOut]:
    state = _state(app)
    entries = state.journal.read_all()[-limit:]
    entries.reverse()  # most recent first
    return [JournalEntryOut(**e) for e in entries]


@app.get("/api/risk", response_model=RiskLimits)
async def get_risk() -> RiskLimits:
    state = _state(app)
    settings = state.settings
    stop_check = state.emergency_stop.check()
    return RiskLimits(
        risk_per_trade=settings.risk_per_trade,
        max_daily_loss=settings.max_daily_loss,
        max_weekly_loss=settings.max_weekly_loss,
        max_open_trades=settings.max_open_trades,
        max_trades_per_day=settings.max_trades_per_day,
        max_exposure_per_currency=settings.max_exposure_per_currency,
        max_spread_points=settings.max_spread_points,
        min_risk_reward=settings.min_risk_reward,
        max_drawdown_halt=settings.max_drawdown_halt,
        trading_sessions=[f"{s}-{e}" for s, e in settings.trading_sessions],
        emergency_stop_active=not stop_check.ok,
        emergency_stop_reason=stop_check.reason,
    )


@app.post("/api/risk/emergency-stop", response_model=RiskLimits)
async def trip_emergency_stop(body: EmergencyStopRequest) -> RiskLimits:
    state = _state(app)
    state.emergency_stop.trip(body.reason)
    state.notifier.notify_emergency_stop(body.reason)
    logger.warning(f"Emergency stop tripped via dashboard: {body.reason}")
    return await get_risk()


@app.post("/api/risk/reset-stop", response_model=RiskLimits)
async def reset_emergency_stop() -> RiskLimits:
    state = _state(app)
    state.emergency_stop.reset()
    logger.info("Emergency stop reset via dashboard")
    return await get_risk()


@app.post("/api/backtest", response_model=BacktestResponse)
async def run_backtest_endpoint(request: BacktestRequest) -> BacktestResponse:
    state = _state(app)
    try:
        broker_symbol = resolve_symbol(request.symbol, state.settings)
        spec = get_symbol_spec(broker_symbol)
    except MarketDataError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    date_to = datetime.now(timezone.utc)
    date_from = date_to - timedelta(days=request.days)
    try:
        df = get_candles_range(broker_symbol, request.timeframe, date_from, date_to)
    except MarketDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    if len(df) < 250:
        raise HTTPException(status_code=422, detail=f"Not enough history returned ({len(df)} bars) - widen the date range")

    featured = prepare_baseline_features(df)
    strategy_fn = make_ema_crossover_strategy()

    config = BacktestConfig(initial_balance=request.initial_balance, risk_per_trade=request.risk_per_trade, min_risk_reward=request.min_risk_reward)
    result = run_backtest(featured, strategy_fn, spec, config=config, warmup_bars=210)
    metrics = compute_metrics(result)

    return BacktestResponse(
        symbol=request.symbol,
        timeframe=request.timeframe,
        bars_used=len(df),
        total_trades=metrics.total_trades,
        winning_trades=metrics.winning_trades,
        losing_trades=metrics.losing_trades,
        win_rate=metrics.win_rate,
        profit_factor=metrics.profit_factor if metrics.profit_factor != float("inf") else -1.0,
        expectancy=metrics.expectancy,
        expectancy_r=metrics.expectancy_r,
        max_drawdown_pct=metrics.max_drawdown_pct,
        sharpe_ratio=metrics.sharpe_ratio,
        net_return_pct=metrics.net_return_pct,
        longest_losing_streak=metrics.longest_losing_streak,
        final_balance=result.final_balance,
        equity_curve=[EquityPoint(time=str(row.time), equity=row.equity) for row in result.equity_curve.itertuples()],
        trades=[
            TradeOut(
                direction=t.direction, entry_time=str(t.entry_time), entry_price=t.entry_price, exit_time=str(t.exit_time),
                exit_price=t.exit_price, volume=t.volume, pnl=t.pnl, r_multiple=t.r_multiple, exit_reason=t.exit_reason,
                duration_bars=t.duration_bars,
            )
            for t in result.trades
        ],
    )


@app.get("/api/logs", response_model=list[LogLine])
async def get_logs(limit: int = Query(default=200, gt=0, le=2000), level: str | None = None) -> list[LogLine]:
    state = _state(app)
    log_path = Path(state.settings.log_dir) / "trading_bot.log"
    if not log_path.exists():
        return []

    lines: deque[str] = deque(maxlen=limit * 5 if level else limit)  # overscan when filtering by level
    with log_path.open(encoding="utf-8") as f:
        for raw_line in f:
            lines.append(raw_line)

    parsed: list[LogLine] = []
    for raw_line in lines:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            data = json.loads(raw_line)
            entry = LogLine(timestamp=data.get("timestamp"), level=data.get("level"), logger=data.get("logger"), message=data.get("message", raw_line), raw=raw_line)
        except json.JSONDecodeError:
            entry = LogLine(message=raw_line, raw=raw_line)
        if level and (entry.level or "").upper() != level.upper():
            continue
        parsed.append(entry)

    return list(reversed(parsed[-limit:]))


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket) -> None:
    state = _state(app)
    if state.settings.api_key and websocket.query_params.get("api_key") != state.settings.api_key:
        # Browsers can't set custom headers on a WebSocket handshake, so
        # the key travels as a query param here instead - same mandatory
        # gate as enforce_api_key applies to every /api/* HTTP request.
        await websocket.close(code=4401)
        return
    await websocket.accept()
    try:
        while True:
            payload: dict = {"timestamp": datetime.now(timezone.utc).isoformat()}
            try:
                info = get_account_info()
                equity_state = state.equity_tracker.update(info.equity)
                payload["account"] = {
                    "balance": info.balance,
                    "equity": info.equity,
                    "daily_pnl": info.equity - equity_state.day_start_equity,
                }
                positions = get_open_positions(magic=state.settings.magic_number)
                payload["open_positions"] = len(positions)
                payload["positions_profit"] = sum(p.profit for p in positions)
            except Exception as exc:  # noqa: BLE001
                payload["error"] = str(exc)

            quotes = {}
            for canonical, broker_symbol in state.settings.symbol_map.items():
                try:
                    bid, ask = get_current_price(broker_symbol)
                    quotes[canonical] = {"bid": bid, "ask": ask}
                except MarketDataError:
                    continue
            payload["quotes"] = quotes

            await websocket.send_json(payload)
            await asyncio.sleep(3.0)
    except WebSocketDisconnect:
        logger.info("Dashboard websocket disconnected")


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run("app.api.server:app", host=settings.api_host, port=settings.api_port, reload=False)
