"""
Telegram notifications (spec section 20) - optional, outbound-only.

Design decisions:
- A plain HTTP POST to Telegram's Bot API `sendMessage` endpoint via
  `httpx`, not the `python-telegram-bot` framework. That library is built
  for bots that receive and react to updates (polling/webhooks, an
  internal event loop, handler registration); this codebase only ever
  needs to push one-way alerts, so a framework built around the opposite
  problem would be dead weight for one HTTP call.
- Never raises. A Telegram outage, bad token, or network blip must not
  interrupt trading logic - every `notify_*` method catches its own
  errors, logs them, and returns False. Callers never need to wrap a
  notification call in their own try/except.
- A silent no-op when `telegram_enabled` is False or the token/chat ID
  are unset, rather than requiring every call site to check
  `settings.telegram_enabled` first - notifications are meant to be
  sprinkled through execution/risk code without cluttering it with
  feature-flag checks.
"""
from __future__ import annotations

import httpx

from app.config import Settings
from app.logging_setup import get_logger

logger = get_logger("notifications.telegram")

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramNotifier:
    def __init__(self, settings: Settings, timeout_seconds: float = 10.0):
        self.enabled = settings.telegram_enabled
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.timeout_seconds = timeout_seconds

    def _send(self, text: str) -> bool:
        if not self.enabled:
            return False
        if not self.token or not self.chat_id:
            logger.warning("Telegram is enabled but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID is not set")
            return False
        try:
            response = httpx.post(
                f"{TELEGRAM_API_BASE}/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            # Deliberately not logging str(exc)/repr(exc): the request URL
            # embeds the bot token (Telegram's API has no header-based auth
            # option), and some httpx exception subtypes echo the full
            # request URL in their string form. Logging only the exception
            # type (plus a response status code when there is one) avoids
            # ever writing the token to disk via this path.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning(f"Telegram notification failed: {type(exc).__name__}" + (f" (HTTP {status})" if status else ""))
            return False

    # ---- spec section 20 message types ----

    def notify_signal(self, symbol: str, direction: str, entry: float, sl: float, tp: float, risk_pct: float, reward_risk: float, reason: str) -> bool:
        text = (
            f"*NEW SIGNAL*\n\n"
            f"{symbol} {direction}\n\n"
            f"Entry: `{entry:.5f}`\n"
            f"SL: `{sl:.5f}`\n"
            f"TP: `{tp:.5f}`\n\n"
            f"Risk: {risk_pct:.1%}\n"
            f"R:R: 1:{reward_risk:.1f}\n\n"
            f"Reason:\n{reason}"
        )
        return self._send(text)

    def notify_trade_opened(self, symbol: str, direction: str, volume: float, price: float, sl: float, tp: float, ticket: int) -> bool:
        text = f"*TRADE OPENED*\n\n{symbol} {direction} {volume} lots @ `{price:.5f}`\nSL: `{sl:.5f}` | TP: `{tp:.5f}`\nTicket: `{ticket}`"
        return self._send(text)

    def notify_trade_closed(self, symbol: str, direction: str, exit_reason: str, entry_price: float, exit_price: float, pnl: float, ticket: int) -> bool:
        headline = {"SL": "STOP LOSS HIT", "TP": "TAKE PROFIT HIT"}.get(exit_reason, "TRADE CLOSED")
        sign = "+" if pnl >= 0 else ""
        text = (
            f"*{headline}*\n\n"
            f"{symbol} {direction}\n"
            f"Entry: `{entry_price:.5f}` -> Exit: `{exit_price:.5f}`\n"
            f"P/L: {sign}{pnl:.2f}\n"
            f"Ticket: `{ticket}`"
        )
        return self._send(text)

    def notify_order_failed(self, symbol: str, direction: str, reason: str) -> bool:
        return self._send(f"*ORDER FAILED*\n\n{symbol} {direction}\n{reason}")

    def notify_risk_halt(self, reason_code: str, detail: str) -> bool:
        return self._send(f"*TRADING HALTED*\n\n{reason_code.replace('_', ' ')}\n{detail}")

    def notify_disconnected(self, detail: str) -> bool:
        return self._send(f"*BOT DISCONNECTED*\n\n{detail}")

    def notify_emergency_stop(self, reason: str) -> bool:
        return self._send(f"*EMERGENCY STOP ACTIVATED*\n\n{reason}\n\nNo new trades will be opened until this is reset.")
