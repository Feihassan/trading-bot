"""
Market data retrieval from MT5: candles, ticks, symbol specs, spread.

Design decisions:
- All functions take a `broker_symbol` that has already been resolved from
  the canonical name via `Settings.symbol_map` (see `resolve_symbol`) - this
  module never assumes a broker's symbol naming convention.
- Candle data is always returned as a pandas DataFrame with a fixed,
  documented schema (time as UTC tz-aware datetime, OHLC, tick_volume,
  spread, real_volume) so downstream feature engineering doesn't need to
  know anything about the raw MT5 tuple format.
- `mt5.symbol_select()` is called before every data request: a symbol not
  visible in "Market Watch" returns no data from MT5, which is a common
  silent-failure trap this wrapper avoids.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import MetaTrader5 as mt5
import pandas as pd

from app.config import Settings, get_settings
from app.logging_setup import get_logger
from app.mt5.connection import MT5Error

logger = get_logger("mt5.market_data")

TIMEFRAME_MAP: dict[str, int] = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

CANDLE_COLUMNS = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]


class MarketDataError(Exception):
    """Raised when MT5 returns no data / an error for a market data request."""


def resolve_symbol(canonical: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    broker_symbol = settings.symbol_map.get(canonical.upper())
    if broker_symbol is None:
        raise MarketDataError(f"Symbol '{canonical}' is not configured in SYMBOLS mapping")
    return broker_symbol


def _ensure_symbol_selected(broker_symbol: str) -> None:
    info = mt5.symbol_info(broker_symbol)
    if info is None:
        err = MT5Error.last()
        raise MarketDataError(f"Unknown symbol '{broker_symbol}' on this broker: [{err.code}] {err.description}")
    if not info.visible:
        if not mt5.symbol_select(broker_symbol, True):
            err = MT5Error.last()
            raise MarketDataError(f"Could not select symbol '{broker_symbol}': [{err.code}] {err.description}")


def get_candles(
    broker_symbol: str,
    timeframe: str,
    count: int = 500,
    start_pos: int = 0,
) -> pd.DataFrame:
    """Fetch the most recent `count` candles ending `start_pos` bars back."""
    if timeframe not in TIMEFRAME_MAP:
        raise MarketDataError(f"Unsupported timeframe '{timeframe}'. Supported: {list(TIMEFRAME_MAP)}")

    _ensure_symbol_selected(broker_symbol)
    rates = mt5.copy_rates_from_pos(broker_symbol, TIMEFRAME_MAP[timeframe], start_pos, count)
    if rates is None or len(rates) == 0:
        err = MT5Error.last()
        raise MarketDataError(
            f"No candle data for {broker_symbol} {timeframe}: [{err.code}] {err.description}"
        )

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df[CANDLE_COLUMNS]


def get_candles_range(
    broker_symbol: str,
    timeframe: str,
    date_from: datetime,
    date_to: datetime,
) -> pd.DataFrame:
    """Fetch candles between two UTC datetimes - used by backtesting/training."""
    if timeframe not in TIMEFRAME_MAP:
        raise MarketDataError(f"Unsupported timeframe '{timeframe}'. Supported: {list(TIMEFRAME_MAP)}")

    _ensure_symbol_selected(broker_symbol)
    rates = mt5.copy_rates_range(broker_symbol, TIMEFRAME_MAP[timeframe], date_from, date_to)
    if rates is None:
        err = MT5Error.last()
        raise MarketDataError(
            f"No candle range data for {broker_symbol} {timeframe}: [{err.code}] {err.description}"
        )

    df = pd.DataFrame(rates)
    if df.empty:
        return pd.DataFrame(columns=CANDLE_COLUMNS)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df[CANDLE_COLUMNS]


def get_ticks(broker_symbol: str, date_from: datetime, date_to: datetime, count: int | None = None) -> pd.DataFrame:
    _ensure_symbol_selected(broker_symbol)
    if count is not None:
        ticks = mt5.copy_ticks_from(broker_symbol, date_from, count, mt5.COPY_TICKS_ALL)
    else:
        ticks = mt5.copy_ticks_range(broker_symbol, date_from, date_to, mt5.COPY_TICKS_ALL)
    if ticks is None:
        err = MT5Error.last()
        raise MarketDataError(f"No tick data for {broker_symbol}: [{err.code}] {err.description}")

    df = pd.DataFrame(ticks)
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df


@dataclass
class SymbolSpec:
    name: str
    digits: int
    point: float
    spread: int
    spread_float: bool
    trade_contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    trade_tick_value: float
    trade_tick_size: float
    currency_base: str
    currency_profit: str
    currency_margin: str


def get_symbol_spec(broker_symbol: str) -> SymbolSpec:
    _ensure_symbol_selected(broker_symbol)
    info = mt5.symbol_info(broker_symbol)
    if info is None:
        err = MT5Error.last()
        raise MarketDataError(f"Could not fetch symbol info for '{broker_symbol}': [{err.code}] {err.description}")
    return SymbolSpec(
        name=info.name,
        digits=info.digits,
        point=info.point,
        spread=info.spread,
        spread_float=info.spread_float,
        trade_contract_size=info.trade_contract_size,
        volume_min=info.volume_min,
        volume_max=info.volume_max,
        volume_step=info.volume_step,
        trade_tick_value=info.trade_tick_value,
        trade_tick_size=info.trade_tick_size,
        currency_base=info.currency_base,
        currency_profit=info.currency_profit,
        currency_margin=info.currency_margin,
    )


def get_current_spread_points(broker_symbol: str) -> int:
    """Live spread in points, computed from the current tick rather than the
    (sometimes stale) SymbolInfo.spread field."""
    _ensure_symbol_selected(broker_symbol)
    tick = mt5.symbol_info_tick(broker_symbol)
    info = mt5.symbol_info(broker_symbol)
    if tick is None or info is None or info.point == 0:
        err = MT5Error.last()
        raise MarketDataError(f"Could not fetch live tick for '{broker_symbol}': [{err.code}] {err.description}")
    return round((tick.ask - tick.bid) / info.point)


def get_current_price(broker_symbol: str) -> tuple[float, float]:
    """Returns (bid, ask)."""
    _ensure_symbol_selected(broker_symbol)
    tick = mt5.symbol_info_tick(broker_symbol)
    if tick is None:
        err = MT5Error.last()
        raise MarketDataError(f"Could not fetch live tick for '{broker_symbol}': [{err.code}] {err.description}")
    return tick.bid, tick.ask
