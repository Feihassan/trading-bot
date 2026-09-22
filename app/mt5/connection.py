"""
MT5 terminal connection management.

Design decisions:
- The `MetaTrader5` package talks to a running MT5 terminal via a local IPC
  channel, not a network API - there is no "server" to point at beyond the
  terminal path/login. `mt5.initialize()` launches/attaches to the
  terminal; `mt5.login()` then authenticates the trading account.
- The MT5 API is a global, stateful, single-connection module (not a
  client object) and is documented as NOT thread-safe. Every call in this
  codebase must go through `MT5Connection`, which serializes access with a
  lock, rather than calling the `MetaTrader5` module directly elsewhere.
- Reconnection is explicit and bounded (not an infinite silent retry loop)
  so persistent failures surface as errors/safety events instead of hanging.
- Credentials are only ever read from Settings (env vars); nothing here
  accepts or logs a raw password.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - Windows-only package, absent on Linux (e.g. Render)
    mt5 = None

from app.config import Settings, get_settings
from app.logging_setup import get_logger

logger = get_logger("mt5.connection")


class MT5ConnectionError(Exception):
    """Raised when the MT5 terminal cannot be reached or login fails."""


@dataclass
class MT5Error:
    code: int
    description: str

    @classmethod
    def last(cls) -> "MT5Error":
        code, description = mt5.last_error()
        return cls(code=code, description=description)


class MT5Connection:
    """
    Thread-safe wrapper around the global MetaTrader5 module connection.

    Usage:
        conn = MT5Connection(settings)
        conn.connect()
        ...
        conn.shutdown()

    Or as a context manager:
        with MT5Connection(settings) as conn:
            ...
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._lock = threading.RLock()
        self._connected = False

    # ---- lifecycle ----

    def connect(self, retries: int = 3, retry_delay_seconds: float = 2.0) -> None:
        with self._lock:
            last_exc: Exception | None = None
            for attempt in range(1, retries + 1):
                try:
                    self._do_connect()
                    self._connected = True
                    logger.info(
                        "Connected to MT5 terminal",
                        extra={"extra_fields": {"attempt": attempt, "server": self.settings.mt5_server}},
                    )
                    return
                except MT5ConnectionError as exc:
                    last_exc = exc
                    logger.warning(
                        f"MT5 connection attempt {attempt}/{retries} failed: {exc}",
                    )
                    if attempt < retries:
                        time.sleep(retry_delay_seconds)
            self._connected = False
            raise MT5ConnectionError(f"Could not connect to MT5 after {retries} attempts") from last_exc

    def _do_connect(self) -> None:
        if mt5 is None:
            raise MT5ConnectionError(
                "MetaTrader5 package is not installed in this environment (it only ships for "
                "Windows, next to a running MT5 terminal) - this process cannot connect to MT5."
            )
        init_kwargs: dict[str, Any] = {}
        if self.settings.mt5_path:
            init_kwargs["path"] = self.settings.mt5_path
        if self.settings.mt5_login:
            init_kwargs["login"] = self.settings.mt5_login
        if self.settings.mt5_password:
            init_kwargs["password"] = self.settings.mt5_password
        if self.settings.mt5_server:
            init_kwargs["server"] = self.settings.mt5_server

        ok = mt5.initialize(**init_kwargs)
        if not ok:
            err = MT5Error.last()
            raise MT5ConnectionError(f"mt5.initialize() failed: [{err.code}] {err.description}")

        # If login wasn't already implied by initialize() kwargs, or we want
        # to switch accounts on an already-running terminal, log in explicitly.
        if self.settings.mt5_login and self.settings.mt5_password and self.settings.mt5_server:
            ok = mt5.login(
                login=self.settings.mt5_login,
                password=self.settings.mt5_password,
                server=self.settings.mt5_server,
            )
            if not ok:
                err = MT5Error.last()
                mt5.shutdown()
                raise MT5ConnectionError(f"mt5.login() failed: [{err.code}] {err.description}")

    def shutdown(self) -> None:
        with self._lock:
            if mt5 is not None:
                mt5.shutdown()
            self._connected = False
            logger.info("MT5 connection shut down")

    def ensure_connected(self) -> None:
        """Verify the connection is alive; reconnect once if it has dropped."""
        with self._lock:
            if self.is_connected():
                return
            logger.warning("MT5 connection appears lost; attempting reconnect")
            self.connect()

    def is_connected(self) -> bool:
        with self._lock:
            if not self._connected:
                return False
            info = mt5.terminal_info()
            account = mt5.account_info()
            return info is not None and account is not None and bool(info.connected)

    # ---- context manager ----

    def __enter__(self) -> "MT5Connection":
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.shutdown()

    # ---- diagnostics ----

    def terminal_info(self) -> dict[str, Any] | None:
        with self._lock:
            if mt5 is None:
                return None
            info = mt5.terminal_info()
            return info._asdict() if info else None


_shared_connection: MT5Connection | None = None


def get_connection() -> MT5Connection:
    """Process-wide shared connection, since MT5 only supports one active session."""
    global _shared_connection
    if _shared_connection is None:
        _shared_connection = MT5Connection()
    return _shared_connection
