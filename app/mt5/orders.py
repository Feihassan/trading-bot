"""
Low-level MT5 order submission: market orders, SL/TP modification, and
position closing. This module only knows how to talk to the MT5 trade
API correctly - it has no opinion on risk, trading mode, or whether a
trade *should* happen. That decision is made entirely above this module
(risk manager, executor); nothing here ever runs unless something else
has already approved it.

Design decisions:
- Every call goes through `mt5.order_check()` before `mt5.order_send()`.
  `order_check` validates the request (margin, volume, stops distance)
  without sending it to the trade server, so a request that would be
  rejected fails fast with a specific reason instead of consuming a
  round-trip to the server (or worse, partially executing).
- `deviation` (max acceptable slippage, in points) is always required
  explicitly, never defaulted silently inside a request dict, since
  slippage tolerance is a risk decision the caller must make deliberately.
- Every result is a structured `OrderResult`, whether the underlying call
  succeeded or failed - callers check `.success`, they never need to
  inspect raw MT5 return codes themselves.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import MetaTrader5 as mt5

from app.logging_setup import get_logger
from app.mt5.connection import MT5Error

logger = get_logger("mt5.orders")

Direction = Literal["BUY", "SELL"]

_ORDER_TYPE = {"BUY": mt5.ORDER_TYPE_BUY, "SELL": mt5.ORDER_TYPE_SELL}
_CLOSE_ORDER_TYPE = {"BUY": mt5.ORDER_TYPE_SELL, "SELL": mt5.ORDER_TYPE_BUY}  # closing a BUY = selling, and vice versa


@dataclass
class OrderResult:
    success: bool
    retcode: int | None
    retcode_description: str
    ticket: int | None
    volume: float | None
    price: float | None
    request: dict


def _retcode_name(retcode: int | None) -> str:
    if retcode is None:
        return "N/A"
    return next((name for name in dir(mt5) if name.startswith("TRADE_RETCODE_") and getattr(mt5, name) == retcode), str(retcode))


def _select_filling_mode(broker_symbol: str) -> int:
    """
    Not every broker/symbol supports every order-filling mode, and
    sending an unsupported one is rejected outright (TRADE_RETCODE_
    INVALID_FILL) rather than falling back automatically. `symbol_info().
    filling_mode` is a bitmask of what this specific symbol supports;
    picking from it here means this module works across brokers rather
    than assuming IOC (or any other single mode) is always available.
    """
    # SYMBOL_FILLING_FOK (bit 0, value 1) / SYMBOL_FILLING_IOC (bit 1, value 2):
    # the MetaTrader5 Python package doesn't expose these as named constants
    # (only the ORDER_FILLING_* ones it sends back), but the bitmask values
    # are fixed by the MQL5 platform, not broker-specific.
    SYMBOL_FILLING_FOK = 1
    SYMBOL_FILLING_IOC = 2

    info = mt5.symbol_info(broker_symbol)
    if info is None:
        return mt5.ORDER_FILLING_IOC
    modes = info.filling_mode
    if modes & SYMBOL_FILLING_FOK:
        return mt5.ORDER_FILLING_FOK
    if modes & SYMBOL_FILLING_IOC:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def submit_market_order(
    broker_symbol: str,
    direction: Direction,
    volume: float,
    stop_loss: float,
    take_profit: float,
    deviation_points: int,
    magic: int,
    comment: str = "",
) -> OrderResult:
    tick = mt5.symbol_info_tick(broker_symbol)
    if tick is None:
        err = MT5Error.last()
        return OrderResult(False, None, f"No tick available: [{err.code}] {err.description}", None, None, None, {})

    price = tick.ask if direction == "BUY" else tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": broker_symbol,
        "volume": volume,
        "type": _ORDER_TYPE[direction],
        "price": price,
        "sl": stop_loss,
        "tp": take_profit,
        "deviation": deviation_points,
        "magic": magic,
        "comment": comment[:31],  # MT5 truncates comments; be explicit about it rather than let it happen silently
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _select_filling_mode(broker_symbol),
    }

    check = mt5.order_check(request)
    # order_check's success code is 0 ("request is correct and can be
    # executed"), NOT TRADE_RETCODE_DONE (10009) - that code is specific
    # to order_send's result, a different call with a different meaning.
    if check is None or check.retcode != 0:
        retcode = check.retcode if check is not None else None
        description = check.comment if check is not None else "order_check returned None"
        logger.warning(f"Order pre-check failed for {broker_symbol} {direction}: [{retcode}] {description}")
        return OrderResult(False, retcode, f"{_retcode_name(retcode)}: {description}", None, None, None, request)

    result = mt5.order_send(request)
    if result is None:
        err = MT5Error.last()
        return OrderResult(False, None, f"order_send returned None: [{err.code}] {err.description}", None, None, None, request)

    success = result.retcode == mt5.TRADE_RETCODE_DONE
    if not success:
        logger.warning(f"Order rejected for {broker_symbol} {direction}: [{result.retcode}] {result.comment}")
    else:
        logger.info(
            f"Order filled: {broker_symbol} {direction} {result.volume} lots @ {result.price} (ticket {result.order})",
            extra={"extra_fields": {"symbol": broker_symbol, "direction": direction, "ticket": result.order}},
        )

    return OrderResult(
        success=success,
        retcode=result.retcode,
        retcode_description=f"{_retcode_name(result.retcode)}: {result.comment}",
        ticket=result.order if success else None,
        volume=result.volume if success else None,
        price=result.price if success else None,
        request=request,
    )


def modify_position_sltp(ticket: int, stop_loss: float, take_profit: float) -> OrderResult:
    position = next((p for p in (mt5.positions_get(ticket=ticket) or ())), None)
    if position is None:
        return OrderResult(False, None, f"Position {ticket} not found", None, None, None, {})

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "symbol": position.symbol,
        "sl": stop_loss,
        "tp": take_profit,
    }
    result = mt5.order_send(request)
    if result is None:
        err = MT5Error.last()
        return OrderResult(False, None, f"order_send returned None: [{err.code}] {err.description}", None, None, None, request)

    success = result.retcode == mt5.TRADE_RETCODE_DONE
    return OrderResult(
        success=success,
        retcode=result.retcode,
        retcode_description=f"{_retcode_name(result.retcode)}: {result.comment}",
        ticket=ticket if success else None,
        volume=position.volume,
        price=None,
        request=request,
    )


def close_position(ticket: int, deviation_points: int) -> OrderResult:
    position = next((p for p in (mt5.positions_get(ticket=ticket) or ())), None)
    if position is None:
        return OrderResult(False, None, f"Position {ticket} not found", None, None, None, {})

    direction: Direction = "BUY" if position.type == mt5.ORDER_TYPE_BUY else "SELL"
    tick = mt5.symbol_info_tick(position.symbol)
    if tick is None:
        err = MT5Error.last()
        return OrderResult(False, None, f"No tick available: [{err.code}] {err.description}", None, None, None, {})
    price = tick.bid if direction == "BUY" else tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": position.symbol,
        "volume": position.volume,
        "type": _CLOSE_ORDER_TYPE[direction],
        "position": ticket,
        "price": price,
        "deviation": deviation_points,
        "magic": position.magic,
        "comment": "close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _select_filling_mode(position.symbol),
    }
    result = mt5.order_send(request)
    if result is None:
        err = MT5Error.last()
        return OrderResult(False, None, f"order_send returned None: [{err.code}] {err.description}", None, None, None, request)

    success = result.retcode == mt5.TRADE_RETCODE_DONE
    if success:
        logger.info(f"Position {ticket} closed @ {result.price}")
    else:
        logger.warning(f"Failed to close position {ticket}: [{result.retcode}] {result.comment}")

    return OrderResult(
        success=success,
        retcode=result.retcode,
        retcode_description=f"{_retcode_name(result.retcode)}: {result.comment}",
        ticket=ticket if success else None,
        volume=result.volume if success else None,
        price=result.price if success else None,
        request=request,
    )
