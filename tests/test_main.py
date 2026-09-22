from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

import app.main as main_mod
from app.mt5.connection import MT5ConnectionError


class StubConnectionOk:
    def __init__(self, *a, **kw):
        pass

    def connect(self):
        pass

    def shutdown(self):
        pass


class StubConnectionFails:
    def __init__(self, *a, **kw):
        pass

    def connect(self):
        raise MT5ConnectionError("no terminal running")

    def shutdown(self):
        pass


FAKE_ACCOUNT = SimpleNamespace(
    login=123, balance=10_000.0, equity=10_000.0, margin=0.0, margin_free=10_000.0, margin_level=0.0,
    profit=0.0, currency="USD", leverage=100, trade_allowed=True, server="Demo-Server", trade_mode=0,
)


class TestRunDiagnostics:
    def test_connection_failure_returns_nonzero_and_notifies(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(main_mod, "MT5Connection", StubConnectionFails)
        notified = {"n": 0}
        monkeypatch.setattr(main_mod.TelegramNotifier, "notify_disconnected", lambda self, detail: notified.update(n=notified["n"] + 1))

        exit_code = main_mod.run_diagnostics()
        assert exit_code == 1
        assert notified["n"] == 1

    def test_successful_connection_returns_zero(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(main_mod, "MT5Connection", StubConnectionOk)
        monkeypatch.setattr(main_mod, "get_account_info", lambda: FAKE_ACCOUNT)
        monkeypatch.setattr(main_mod, "get_open_positions", lambda: [])
        monkeypatch.setattr(main_mod, "resolve_symbol", lambda canonical, settings: canonical)
        monkeypatch.setattr(main_mod, "get_current_spread_points", lambda broker_symbol: 1)
        monkeypatch.setattr(main_mod, "get_candles", lambda *a, **k: pd.DataFrame({"close": [1.1, 1.2]}))

        exit_code = main_mod.run_diagnostics()
        assert exit_code == 0

    def test_market_data_error_for_one_symbol_does_not_fail_whole_run(self, monkeypatch, tmp_path):
        from app.mt5.market_data import MarketDataError

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(main_mod, "MT5Connection", StubConnectionOk)
        monkeypatch.setattr(main_mod, "get_account_info", lambda: FAKE_ACCOUNT)
        monkeypatch.setattr(main_mod, "get_open_positions", lambda: [])
        monkeypatch.setattr(main_mod, "resolve_symbol", lambda canonical, settings: canonical)

        def raise_error(broker_symbol):
            raise MarketDataError("no spread")

        monkeypatch.setattr(main_mod, "get_current_spread_points", raise_error)

        exit_code = main_mod.run_diagnostics()
        assert exit_code == 0  # a single symbol's failure is logged, not fatal
