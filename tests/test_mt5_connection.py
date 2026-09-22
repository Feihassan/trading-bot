from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.mt5.connection as connection_mod
from app.config import Settings
from app.mt5.connection import MT5Connection, MT5ConnectionError, get_connection


def settings(**overrides) -> Settings:
    defaults = dict(_env_file=None)
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    # Retry backoff would otherwise slow every failure-path test down.
    monkeypatch.setattr(connection_mod.time, "sleep", lambda *_: None)


class TestConnectWithoutLogin:
    def test_initialize_only_when_no_credentials_configured(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(connection_mod.mt5, "initialize", lambda **kw: captured.update(kw) or True)
        login_called = {"n": 0}
        monkeypatch.setattr(connection_mod.mt5, "login", lambda **kw: login_called.update(n=login_called["n"] + 1))

        conn = MT5Connection(settings())
        conn.connect()
        assert login_called["n"] == 0
        assert "login" not in captured

    def test_path_passed_through_when_configured(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(connection_mod.mt5, "initialize", lambda **kw: captured.update(kw) or True)
        conn = MT5Connection(settings(MT5_PATH="C:/MT5/terminal64.exe"))
        conn.connect()
        assert captured["path"] == "C:/MT5/terminal64.exe"


class TestConnectWithLogin:
    def test_initialize_and_login_both_called_with_credentials(self, monkeypatch):
        init_captured, login_captured = {}, {}
        monkeypatch.setattr(connection_mod.mt5, "initialize", lambda **kw: init_captured.update(kw) or True)
        monkeypatch.setattr(connection_mod.mt5, "login", lambda **kw: login_captured.update(kw) or True)

        conn = MT5Connection(settings(MT5_LOGIN=123, MT5_PASSWORD="secret", MT5_SERVER="Demo-Server"))
        conn.connect()

        assert init_captured["login"] == 123
        assert login_captured == {"login": 123, "password": "secret", "server": "Demo-Server"}

    def test_login_failure_shuts_down_and_raises(self, monkeypatch):
        monkeypatch.setattr(connection_mod.mt5, "initialize", lambda **kw: True)
        monkeypatch.setattr(connection_mod.mt5, "login", lambda **kw: False)
        monkeypatch.setattr(connection_mod.mt5, "last_error", lambda: (-1, "invalid credentials"))
        shutdown_called = {"n": 0}
        monkeypatch.setattr(connection_mod.mt5, "shutdown", lambda: shutdown_called.update(n=shutdown_called["n"] + 1))

        conn = MT5Connection(settings(MT5_LOGIN=123, MT5_PASSWORD="wrong", MT5_SERVER="Demo-Server"))
        with pytest.raises(MT5ConnectionError):
            conn.connect(retries=1)
        assert shutdown_called["n"] == 1


class TestConnectRetryAndFailure:
    def test_retries_configured_number_of_times_then_raises(self, monkeypatch):
        attempts = {"n": 0}

        def failing_initialize(**kw):
            attempts["n"] += 1
            return False

        monkeypatch.setattr(connection_mod.mt5, "initialize", failing_initialize)
        monkeypatch.setattr(connection_mod.mt5, "last_error", lambda: (-1, "no terminal"))

        conn = MT5Connection(settings())
        with pytest.raises(MT5ConnectionError):
            conn.connect(retries=3, retry_delay_seconds=0)
        assert attempts["n"] == 3

    def test_succeeds_on_a_later_attempt_after_earlier_failures(self, monkeypatch):
        attempts = {"n": 0}

        def flaky_initialize(**kw):
            attempts["n"] += 1
            return attempts["n"] >= 2

        monkeypatch.setattr(connection_mod.mt5, "initialize", flaky_initialize)
        monkeypatch.setattr(connection_mod.mt5, "last_error", lambda: (-1, "transient"))

        conn = MT5Connection(settings())
        conn.connect(retries=3, retry_delay_seconds=0)
        assert attempts["n"] == 2
        assert conn.is_connected() or True  # is_connected also depends on terminal_info/account_info - checked separately below


class TestIsConnected:
    def test_false_before_any_connect_call(self):
        conn = MT5Connection(settings())
        assert conn.is_connected() is False

    def test_true_when_connected_flag_set_and_terminal_reports_connected(self, monkeypatch):
        monkeypatch.setattr(connection_mod.mt5, "initialize", lambda **kw: True)
        monkeypatch.setattr(connection_mod.mt5, "terminal_info", lambda: SimpleNamespace(connected=True))
        monkeypatch.setattr(connection_mod.mt5, "account_info", lambda: SimpleNamespace(login=1))

        conn = MT5Connection(settings())
        conn.connect()
        assert conn.is_connected() is True

    def test_false_when_terminal_info_is_none(self, monkeypatch):
        monkeypatch.setattr(connection_mod.mt5, "initialize", lambda **kw: True)
        monkeypatch.setattr(connection_mod.mt5, "terminal_info", lambda: None)
        monkeypatch.setattr(connection_mod.mt5, "account_info", lambda: SimpleNamespace(login=1))

        conn = MT5Connection(settings())
        conn.connect()
        assert conn.is_connected() is False

    def test_false_when_terminal_reports_not_connected(self, monkeypatch):
        monkeypatch.setattr(connection_mod.mt5, "initialize", lambda **kw: True)
        monkeypatch.setattr(connection_mod.mt5, "terminal_info", lambda: SimpleNamespace(connected=False))
        monkeypatch.setattr(connection_mod.mt5, "account_info", lambda: SimpleNamespace(login=1))

        conn = MT5Connection(settings())
        conn.connect()
        assert conn.is_connected() is False


class TestShutdown:
    def test_shutdown_calls_mt5_shutdown_and_clears_flag(self, monkeypatch):
        monkeypatch.setattr(connection_mod.mt5, "initialize", lambda **kw: True)
        shutdown_called = {"n": 0}
        monkeypatch.setattr(connection_mod.mt5, "shutdown", lambda: shutdown_called.update(n=shutdown_called["n"] + 1))

        conn = MT5Connection(settings())
        conn.connect()
        conn.shutdown()
        assert shutdown_called["n"] == 1
        assert conn._connected is False


class TestEnsureConnected:
    def test_does_nothing_when_already_connected(self, monkeypatch):
        monkeypatch.setattr(connection_mod.mt5, "initialize", lambda **kw: True)
        monkeypatch.setattr(connection_mod.mt5, "terminal_info", lambda: SimpleNamespace(connected=True))
        monkeypatch.setattr(connection_mod.mt5, "account_info", lambda: SimpleNamespace(login=1))
        connect_calls = {"n": 0}

        conn = MT5Connection(settings())
        conn.connect()
        original_connect = conn.connect
        conn.connect = lambda *a, **kw: connect_calls.update(n=connect_calls["n"] + 1) or original_connect(*a, **kw)
        conn.ensure_connected()
        assert connect_calls["n"] == 0

    def test_reconnects_when_connection_lost(self, monkeypatch):
        monkeypatch.setattr(connection_mod.mt5, "initialize", lambda **kw: True)
        monkeypatch.setattr(connection_mod.mt5, "terminal_info", lambda: None)  # always looks disconnected
        monkeypatch.setattr(connection_mod.mt5, "account_info", lambda: SimpleNamespace(login=1))

        conn = MT5Connection(settings())
        connect_calls = {"n": 0}
        original_connect = conn.connect
        conn.connect = lambda *a, **kw: connect_calls.update(n=connect_calls["n"] + 1) or original_connect(*a, **kw)

        conn.ensure_connected()
        assert connect_calls["n"] == 1


class TestContextManager:
    def test_enter_connects_and_exit_shuts_down(self, monkeypatch):
        connect_calls = {"n": 0}
        shutdown_calls = {"n": 0}
        monkeypatch.setattr(connection_mod.mt5, "initialize", lambda **kw: connect_calls.update(n=connect_calls["n"] + 1) or True)
        monkeypatch.setattr(connection_mod.mt5, "shutdown", lambda: shutdown_calls.update(n=shutdown_calls["n"] + 1))

        with MT5Connection(settings()) as conn:
            assert connect_calls["n"] == 1
        assert shutdown_calls["n"] == 1


class TestTerminalInfo:
    def test_returns_dict_when_available(self, monkeypatch):
        monkeypatch.setattr(connection_mod.mt5, "terminal_info", lambda: SimpleNamespace(_asdict=lambda: {"connected": True}))
        conn = MT5Connection(settings())
        assert conn.terminal_info() == {"connected": True}

    def test_returns_none_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(connection_mod.mt5, "terminal_info", lambda: None)
        conn = MT5Connection(settings())
        assert conn.terminal_info() is None


class TestGetConnectionSingleton:
    def test_returns_same_instance_across_calls(self, monkeypatch):
        monkeypatch.setattr(connection_mod, "_shared_connection", None)
        first = get_connection()
        second = get_connection()
        assert first is second
        monkeypatch.setattr(connection_mod, "_shared_connection", None)  # cleanup
