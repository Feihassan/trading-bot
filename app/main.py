"""
Phase 1 entry point: verifies configuration and MT5 connectivity end to end.

Run with:
    python -m app.main

This does NOT start the FastAPI server (see app/api/server.py for that) -
it's a diagnostic CLI so each phase's MT5 integration can be verified
against a real terminal as it's built, per the project's "verify, then
continue" workflow.
"""
from __future__ import annotations

import sys

from app.config import get_settings
from app.logging_setup import configure_logging, get_logger
from app.mt5.account import get_account_info, get_open_positions
from app.mt5.connection import MT5Connection, MT5ConnectionError
from app.mt5.market_data import MarketDataError, get_candles, get_current_spread_points, resolve_symbol
from app.notifications.telegram import TelegramNotifier

logger = get_logger("main")


def run_diagnostics() -> int:
    settings = get_settings()
    settings.ensure_directories()
    configure_logging(log_dir=settings.log_dir, level=settings.log_level)

    logger.info(f"Trading mode: {settings.trading_mode.value}")
    logger.info(f"Configured symbols: {list(settings.symbol_map.keys())}")
    logger.info(f"Primary timeframes: {settings.primary_timeframes}")

    conn = MT5Connection(settings)
    try:
        conn.connect()
    except MT5ConnectionError as exc:
        logger.error(f"Failed to connect to MT5: {exc}")
        logger.error(
            "Make sure the MetaTrader 5 terminal is installed and, if MT5_PATH "
            "is unset, already running and logged into your demo account."
        )
        TelegramNotifier(settings).notify_disconnected(f"Diagnostic CLI could not connect to MT5: {exc}")
        return 1

    try:
        account = get_account_info()
        logger.info(
            "Account info",
            extra={
                "extra_fields": {
                    "login": account.login,
                    "server": account.server,
                    "balance": account.balance,
                    "equity": account.equity,
                    "currency": account.currency,
                    "leverage": account.leverage,
                    "trade_allowed": account.trade_allowed,
                }
            },
        )

        positions = get_open_positions()
        logger.info(f"Open positions: {len(positions)}")

        for canonical in settings.symbol_map:
            try:
                broker_symbol = resolve_symbol(canonical, settings)
                spread = get_current_spread_points(broker_symbol)
                candles = get_candles(broker_symbol, settings.primary_timeframes[0], count=5)
                last_close = candles.iloc[-1]["close"]
                logger.info(
                    f"{canonical} ({broker_symbol}): spread={spread}pts last_close={last_close}"
                )
            except MarketDataError as exc:
                logger.warning(f"{canonical}: market data check failed: {exc}")

        return 0
    finally:
        conn.shutdown()


if __name__ == "__main__":
    sys.exit(run_diagnostics())
