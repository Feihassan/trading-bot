"""
Account information and open positions.

Design decision: returned as typed dataclasses rather than raw MT5
namedtuples, so the rest of the app (risk manager, dashboard API, DB
repository) never needs to know MT5's field names or import the
MetaTrader5 package directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import MetaTrader5 as mt5

from app.logging_setup import get_logger
from app.mt5.connection import MT5Error

logger = get_logger("mt5.account")


class AccountError(Exception):
    pass


@dataclass
class AccountInfo:
    login: int
    balance: float
    equity: float
    margin: float
    margin_free: float
    margin_level: float
    profit: float
    currency: str
    leverage: int
    trade_allowed: bool
    server: str
    trade_mode: int  # mt5.ACCOUNT_TRADE_MODE_{DEMO=0, CONTEST=1, REAL=2}


def is_demo_account(info: AccountInfo) -> bool:
    return info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO


def is_real_account(info: AccountInfo) -> bool:
    return info.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL


@dataclass
class Position:
    ticket: int
    symbol: str
    type: str  # "BUY" or "SELL"
    volume: float
    price_open: float
    price_current: float
    sl: float
    tp: float
    profit: float
    swap: float
    magic: int
    comment: str
    open_time: int  # unix seconds, UTC


_POSITION_TYPE_MAP = {0: "BUY", 1: "SELL"}


def get_account_info() -> AccountInfo:
    info = mt5.account_info()
    if info is None:
        err = MT5Error.last()
        raise AccountError(f"Could not fetch account info: [{err.code}] {err.description}")
    return AccountInfo(
        login=info.login,
        balance=info.balance,
        equity=info.equity,
        margin=info.margin,
        margin_free=info.margin_free,
        margin_level=info.margin_level,
        profit=info.profit,
        currency=info.currency,
        leverage=info.leverage,
        trade_allowed=bool(info.trade_allowed),
        server=info.server,
        trade_mode=info.trade_mode,
    )


def get_open_positions(symbol: str | None = None, magic: int | None = None) -> list[Position]:
    positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    if positions is None:
        # positions_get returns None on error, empty tuple when there are none
        err = MT5Error.last()
        if err.code != 1:  # 1 == RES_S_OK; None with S_OK just means "no positions"
            raise AccountError(f"Could not fetch open positions: [{err.code}] {err.description}")
        positions = ()

    result = [
        Position(
            ticket=p.ticket,
            symbol=p.symbol,
            type=_POSITION_TYPE_MAP.get(p.type, "UNKNOWN"),
            volume=p.volume,
            price_open=p.price_open,
            price_current=p.price_current,
            sl=p.sl,
            tp=p.tp,
            profit=p.profit,
            swap=p.swap,
            magic=p.magic,
            comment=p.comment,
            open_time=p.time,
        )
        for p in positions
    ]
    if magic is not None:
        result = [p for p in result if p.magic == magic]
    return result


def get_closed_pnl_between(date_from: datetime, date_to: datetime, magic: int | None = None) -> float:
    """
    Sum of realized profit (+ swap + commission) for deals closed in
    [date_from, date_to], per the broker's own trade history - not our
    own bookkeeping. Used by the risk manager for daily/weekly loss
    tracking (spec section 12) as ground truth: no local database is
    required for this, and it stays correct even after a bot restart
    since MT5 retains full deal history independent of this process.
    """
    deals = mt5.history_deals_get(date_from, date_to)
    if deals is None:
        err = MT5Error.last()
        if err.code != 1:
            raise AccountError(f"Could not fetch deal history: [{err.code}] {err.description}")
        deals = ()

    if magic is not None:
        deals = [d for d in deals if d.magic == magic]

    # entry==1 is DEAL_ENTRY_OUT (position close) - entry deals (DEAL_ENTRY_IN)
    # never carry realized profit and would double-count if included.
    return sum(d.profit + d.swap + d.commission for d in deals if d.entry == 1)


_DEAL_REASON_TO_EXIT = {
    mt5.DEAL_REASON_SL: "SL",
    mt5.DEAL_REASON_TP: "TP",
}


@dataclass
class ClosedPositionInfo:
    ticket: int
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    pnl: float
    exit_reason: str  # "SL" | "TP" | "MANUAL"


def get_closed_position_info(ticket: int) -> ClosedPositionInfo | None:
    """
    Looks up the closing deal for a position ticket via MT5's own history
    (`history_deals_get(position=ticket)`) - this is how a "trade closed /
    SL hit / TP hit" notification (spec section 20) can be produced
    without a persistent monitoring loop or local database: call this
    once a ticket that used to be open no longer appears in
    `get_open_positions()`.
    """
    deals = mt5.history_deals_get(position=ticket)
    if not deals:
        return None
    closing_deals = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
    if not closing_deals:
        return None
    closing = closing_deals[-1]
    opening_deals = [d for d in deals if d.entry == mt5.DEAL_ENTRY_IN]
    direction = _POSITION_TYPE_MAP.get(opening_deals[0].type, "UNKNOWN") if opening_deals else "UNKNOWN"
    entry_price = opening_deals[0].price if opening_deals else closing.price

    return ClosedPositionInfo(
        ticket=ticket,
        symbol=closing.symbol,
        direction=direction,
        entry_price=entry_price,
        exit_price=closing.price,
        pnl=sum(d.profit + d.swap + d.commission for d in closing_deals),
        exit_reason=_DEAL_REASON_TO_EXIT.get(closing.reason, "MANUAL"),
    )
