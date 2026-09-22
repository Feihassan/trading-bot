"""
API tests use FastAPI's TestClient with MT5 calls monkeypatched at the
app.api.server module level - the same pattern used for the executor
tests (tests/test_executor.py). The lifespan tries to open a real MT5
connection on startup, so MT5Connection itself is replaced with a stub
before the client is constructed.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.api.server as server_mod
from app.config import Settings
from app.execution.trade_manager import EquityTracker, TradeJournal
from app.mt5.connection import MT5ConnectionError
from app.risk.safeguards import EmergencyStop


class StubConnection:
    def __init__(self, *args, **kwargs):
        pass

    def connect(self):
        pass

    def shutdown(self):
        pass

    def is_connected(self):
        return True


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(server_mod, "MT5Connection", StubConnection)
    monkeypatch.setattr(server_mod, "EquityTracker", lambda: EquityTracker(tmp_path / "equity.json"))
    monkeypatch.setattr(server_mod, "TradeJournal", lambda: TradeJournal(tmp_path / "journal.jsonl"))
    monkeypatch.setattr(server_mod, "EmergencyStop", lambda: EmergencyStop(tmp_path / "STOP"))
    with TestClient(server_mod.app) as c:
        yield c


@pytest.fixture()
def client_with_api_key(monkeypatch, tmp_path):
    monkeypatch.setattr(server_mod, "MT5Connection", StubConnection)
    monkeypatch.setattr(server_mod, "EquityTracker", lambda: EquityTracker(tmp_path / "equity.json"))
    monkeypatch.setattr(server_mod, "TradeJournal", lambda: TradeJournal(tmp_path / "journal.jsonl"))
    monkeypatch.setattr(server_mod, "EmergencyStop", lambda: EmergencyStop(tmp_path / "STOP"))
    monkeypatch.setattr(server_mod, "get_settings", lambda: Settings(_env_file=None, API_KEY="secret123"))
    with TestClient(server_mod.app) as c:
        yield c


FAKE_ACCOUNT = SimpleNamespace(
    login=123, balance=10_000.0, equity=10_000.0, margin=0.0, margin_free=10_000.0, margin_level=0.0,
    profit=0.0, currency="USD", leverage=100, trade_allowed=True, server="Demo-Server", trade_mode=0,
)


class TestStatusAndAccount:
    def test_status_reports_connected(self, client, monkeypatch):
        monkeypatch.setattr(server_mod, "get_account_info", lambda: FAKE_ACCOUNT)
        resp = client.get("/api/status")
        assert resp.status_code == 200
        assert resp.json()["connected"] is True
        assert resp.json()["server"] == "Demo-Server"

    def test_account_summary_computes_pnl_from_equity_tracker(self, client, monkeypatch):
        monkeypatch.setattr(server_mod, "get_account_info", lambda: FAKE_ACCOUNT)
        resp = client.get("/api/account")
        assert resp.status_code == 200
        body = resp.json()
        assert body["balance"] == 10_000.0
        assert body["is_demo"] is True
        assert body["daily_pnl"] == 0.0  # first call of the day seeds day_start_equity to current equity

    def test_account_unavailable_returns_503(self, client, monkeypatch):
        def raise_error():
            raise RuntimeError("no connection")

        monkeypatch.setattr(server_mod, "get_account_info", raise_error)
        resp = client.get("/api/account")
        assert resp.status_code == 503


class TestPositions:
    def test_positions_serialized(self, client, monkeypatch):
        fake_position = SimpleNamespace(
            ticket=1, symbol="EURUSD", type="BUY", volume=0.1, price_open=1.1, price_current=1.105,
            sl=1.09, tp=1.12, profit=5.0, swap=0.0, magic=1, comment="", open_time=1700000000,
        )
        monkeypatch.setattr(server_mod, "get_open_positions", lambda magic=None: [fake_position])
        resp = client.get("/api/positions")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["symbol"] == "EURUSD"


class TestRiskAndEmergencyStop:
    def test_get_risk_reflects_settings(self, client):
        resp = client.get("/api/risk")
        assert resp.status_code == 200
        body = resp.json()
        assert body["emergency_stop_active"] is False
        assert body["risk_per_trade"] == pytest.approx(0.01)

    def test_trip_and_reset_emergency_stop(self, client):
        resp = client.post("/api/risk/emergency-stop", json={"reason": "test stop"})
        assert resp.status_code == 200
        assert resp.json()["emergency_stop_active"] is True
        assert "test stop" in resp.json()["emergency_stop_reason"]

        resp = client.post("/api/risk/reset-stop")
        assert resp.status_code == 200
        assert resp.json()["emergency_stop_active"] is False


class TestSignalsEndpoint:
    def test_unknown_symbol_returns_404(self, client):
        resp = client.get("/api/signals/NOTASYMBOL")
        assert resp.status_code == 404

    def test_known_symbol_returns_signal(self, client, monkeypatch):
        import pandas as pd

        from app.strategy.signals import TradeIdea

        monkeypatch.setattr(server_mod, "resolve_symbol", lambda symbol, settings: "EURUSD")
        monkeypatch.setattr(server_mod, "get_candles", lambda *a, **k: pd.DataFrame({"time": [pd.Timestamp.now(tz="UTC")], "close": [1.1]}))

        fake_idea = TradeIdea(
            symbol="EURUSD", timestamp=pd.Timestamp.now(tz="UTC"), direction="WAIT", confidence=10.0,
            entry=None, stop_loss=None, take_profit=None, reward_risk=None, technical_score=0.0,
            technical_direction="NEUTRAL", structure_trend="UNKNOWN", regime="UNKNOWN",
            ml_probabilities=None, ml_direction=None, spread_points=1.0, reasons=["test"], blocking_reasons=[],
        )
        monkeypatch.setattr(server_mod, "generate_signal", lambda *a, **k: fake_idea)

        resp = client.get("/api/signals/EURUSD")
        assert resp.status_code == 200
        assert resp.json()["direction"] == "WAIT"
        assert resp.json()["reasons"] == ["test"]


class TestJournalEndpoint:
    def test_journal_returns_recorded_entries_most_recent_first(self, client):
        state = server_mod._state(server_mod.app)
        state.journal.record({"symbol": "EURUSD", "executed": True})
        state.journal.record({"symbol": "GBPUSD", "executed": False})

        resp = client.get("/api/journal")
        assert resp.status_code == 200
        symbols = [e["symbol"] for e in resp.json()]
        assert symbols == ["GBPUSD", "EURUSD"]


class TestSymbolsEndpoint:
    def test_returns_quotes_for_configured_symbols(self, client, monkeypatch):
        monkeypatch.setattr(server_mod, "get_current_price", lambda broker_symbol: (1.1000, 1.1002))
        monkeypatch.setattr(server_mod, "get_current_spread_points", lambda broker_symbol: 2)
        resp = client.get("/api/symbols")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) > 0
        assert body[0]["bid"] == 1.1000

    def test_symbol_lookup_failure_is_skipped_not_fatal(self, client, monkeypatch):
        from app.mt5.market_data import MarketDataError

        def raise_error(broker_symbol):
            raise MarketDataError("no tick")

        monkeypatch.setattr(server_mod, "get_current_price", raise_error)
        resp = client.get("/api/symbols")
        assert resp.status_code == 200
        assert resp.json() == []  # every symbol failed to quote, but the endpoint itself still succeeds


class TestSignalsEndpointWithMl:
    def test_use_ml_true_calls_model_training_path(self, client, monkeypatch):
        import pandas as pd

        from app.strategy.signals import TradeIdea

        monkeypatch.setattr(server_mod, "resolve_symbol", lambda symbol, settings: "EURUSD")
        monkeypatch.setattr(server_mod, "get_candles", lambda *a, **k: pd.DataFrame({"time": [pd.Timestamp.now(tz="UTC")], "close": [1.1]}))

        train_calls = {"n": 0}
        monkeypatch.setattr(server_mod, "_get_or_train_model", lambda *a, **k: train_calls.update(n=train_calls["n"] + 1) or object())

        fake_idea = TradeIdea(
            symbol="EURUSD", timestamp=pd.Timestamp.now(tz="UTC"), direction="WAIT", confidence=0.0,
            entry=None, stop_loss=None, take_profit=None, reward_risk=None, technical_score=0.0,
            technical_direction="NEUTRAL", structure_trend="UNKNOWN", regime="UNKNOWN",
            ml_probabilities=None, ml_direction=None, spread_points=1.0, reasons=[], blocking_reasons=[],
        )
        monkeypatch.setattr(server_mod, "generate_signal", lambda *a, **k: fake_idea)

        resp = client.get("/api/signals/EURUSD?use_ml=true")
        assert resp.status_code == 200
        assert train_calls["n"] == 1

    def test_model_training_failure_does_not_break_endpoint(self, client, monkeypatch):
        import pandas as pd

        from app.strategy.signals import TradeIdea

        monkeypatch.setattr(server_mod, "resolve_symbol", lambda symbol, settings: "EURUSD")
        monkeypatch.setattr(server_mod, "get_candles", lambda *a, **k: pd.DataFrame({"time": [pd.Timestamp.now(tz="UTC")], "close": [1.1]}))

        def raise_error(*a, **k):
            raise RuntimeError("not enough history")

        monkeypatch.setattr(server_mod, "_get_or_train_model", raise_error)

        fake_idea = TradeIdea(
            symbol="EURUSD", timestamp=pd.Timestamp.now(tz="UTC"), direction="WAIT", confidence=0.0,
            entry=None, stop_loss=None, take_profit=None, reward_risk=None, technical_score=0.0,
            technical_direction="NEUTRAL", structure_trend="UNKNOWN", regime="UNKNOWN",
            ml_probabilities=None, ml_direction=None, spread_points=1.0, reasons=[], blocking_reasons=[],
        )
        monkeypatch.setattr(server_mod, "generate_signal", lambda *a, **k: fake_idea)

        resp = client.get("/api/signals/EURUSD?use_ml=true")
        assert resp.status_code == 200  # training failure is logged, not fatal


class TestBacktestEndpoint:
    def test_runs_end_to_end_against_synthetic_history(self, client, monkeypatch):
        from tests._synthetic import make_trending_candles
        from tests.test_backtest_engine import SPEC

        monkeypatch.setattr(server_mod, "resolve_symbol", lambda symbol, settings: "EURUSD")
        monkeypatch.setattr(server_mod, "get_symbol_spec", lambda broker_symbol: SPEC)
        monkeypatch.setattr(server_mod, "get_candles_range", lambda *a, **k: make_trending_candles(n=400))

        resp = client.post(
            "/api/backtest",
            json={"symbol": "EURUSD", "timeframe": "H1", "days": 30, "initial_balance": 10000, "risk_per_trade": 0.01, "min_risk_reward": 2.0},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["bars_used"] == 400
        assert "equity_curve" in body
        assert body["final_balance"] > 0

    def test_insufficient_history_returns_422(self, client, monkeypatch):
        from tests._synthetic import make_trending_candles
        from tests.test_backtest_engine import SPEC

        monkeypatch.setattr(server_mod, "resolve_symbol", lambda symbol, settings: "EURUSD")
        monkeypatch.setattr(server_mod, "get_symbol_spec", lambda broker_symbol: SPEC)
        monkeypatch.setattr(server_mod, "get_candles_range", lambda *a, **k: make_trending_candles(n=50))

        resp = client.post(
            "/api/backtest",
            json={"symbol": "EURUSD", "timeframe": "H1", "days": 30, "initial_balance": 10000, "risk_per_trade": 0.01, "min_risk_reward": 2.0},
        )
        assert resp.status_code == 422

    def test_unknown_symbol_returns_404(self, client):
        resp = client.post(
            "/api/backtest",
            json={"symbol": "NOTASYMBOL", "timeframe": "H1", "days": 30, "initial_balance": 10000, "risk_per_trade": 0.01, "min_risk_reward": 2.0},
        )
        assert resp.status_code == 404


class TestLiveWebsocket:
    def test_websocket_streams_a_payload(self, client, monkeypatch):
        monkeypatch.setattr(server_mod, "get_account_info", lambda: FAKE_ACCOUNT)
        monkeypatch.setattr(server_mod, "get_open_positions", lambda magic=None: [])
        monkeypatch.setattr(server_mod, "get_current_price", lambda broker_symbol: (1.1, 1.1002))

        with client.websocket_connect("/ws/live") as ws:
            payload = ws.receive_json()
        assert "timestamp" in payload
        assert "quotes" in payload

    def test_websocket_rejects_wrong_key_when_configured(self, client_with_api_key):
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect):
            with client_with_api_key.websocket_connect("/ws/live?api_key=wrong"):
                pass

    def test_websocket_accepts_correct_key_when_configured(self, client_with_api_key, monkeypatch):
        monkeypatch.setattr(server_mod, "get_account_info", lambda: FAKE_ACCOUNT)
        monkeypatch.setattr(server_mod, "get_open_positions", lambda magic=None: [])
        monkeypatch.setattr(server_mod, "get_current_price", lambda broker_symbol: (1.1, 1.1002))

        with client_with_api_key.websocket_connect("/ws/live?api_key=secret123") as ws:
            payload = ws.receive_json()
        assert "timestamp" in payload


class TestApiKeyEnforcement:
    def test_no_key_configured_allows_requests_through(self, client):
        # Default fixture has no API_KEY set - matches the local-only default.
        resp = client.get("/api/risk")
        assert resp.status_code == 200

    def test_key_configured_rejects_request_without_header(self, client_with_api_key):
        resp = client_with_api_key.get("/api/risk")
        assert resp.status_code == 401

    def test_key_configured_rejects_wrong_key(self, client_with_api_key):
        resp = client_with_api_key.get("/api/risk", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401

    def test_key_configured_accepts_correct_key(self, client_with_api_key):
        resp = client_with_api_key.get("/api/risk", headers={"X-API-Key": "secret123"})
        assert resp.status_code == 200

    def test_non_api_paths_are_not_gated(self, client_with_api_key):
        # /docs (FastAPI's auto-generated OpenAPI UI) isn't under /api/ -
        # the key only guards the actual data/control endpoints.
        resp = client_with_api_key.get("/docs")
        assert resp.status_code == 200


class TestBoundedQueryParams:
    def test_journal_limit_rejects_zero_or_negative(self, client):
        assert client.get("/api/journal?limit=0").status_code == 422
        assert client.get("/api/journal?limit=-5").status_code == 422

    def test_journal_limit_rejects_excessive_value(self, client):
        assert client.get("/api/journal?limit=999999").status_code == 422

    def test_logs_limit_rejects_zero_or_negative(self, client):
        assert client.get("/api/logs?limit=0").status_code == 422

    def test_backtest_days_rejects_excessive_value(self, client):
        resp = client.post(
            "/api/backtest",
            json={"symbol": "EURUSD", "timeframe": "H1", "days": 999999, "initial_balance": 10000, "risk_per_trade": 0.01, "min_risk_reward": 2.0},
        )
        assert resp.status_code == 422


class TestLifespanConnectionFailure:
    def test_startup_without_mt5_still_serves_requests_and_notifies(self, monkeypatch, tmp_path):
        class FailingConnection:
            def __init__(self, *a, **kw):
                pass

            def connect(self):
                raise MT5ConnectionError("terminal not running")

            def shutdown(self):
                pass

            def is_connected(self):
                return False

        monkeypatch.setattr(server_mod, "MT5Connection", FailingConnection)
        monkeypatch.setattr(server_mod, "EquityTracker", lambda: EquityTracker(tmp_path / "equity.json"))
        monkeypatch.setattr(server_mod, "TradeJournal", lambda: TradeJournal(tmp_path / "journal.jsonl"))
        monkeypatch.setattr(server_mod, "EmergencyStop", lambda: EmergencyStop(tmp_path / "STOP"))

        notified = {"n": 0}
        monkeypatch.setattr(server_mod.TelegramNotifier, "notify_disconnected", lambda self, detail: notified.update(n=notified["n"] + 1))

        with TestClient(server_mod.app) as c:
            resp = c.get("/api/status")
            assert resp.status_code == 200
            assert resp.json()["connected"] is False
        assert notified["n"] == 1


class TestClosedTradeWatcher:
    async def test_detects_closed_position_and_notifies_and_journals(self, monkeypatch, tmp_path):
        from app.api.state import AppState
        from app.config import Settings
        from app.mt5.account import ClosedPositionInfo

        settings = Settings(_env_file=None)
        state = AppState(
            settings=settings,
            connection=None,
            risk_manager=None,
            equity_tracker=EquityTracker(tmp_path / "equity.json"),
            journal=TradeJournal(tmp_path / "journal.jsonl"),
            emergency_stop=EmergencyStop(tmp_path / "STOP"),
            notifier=server_mod.TelegramNotifier(settings),
        )
        state.known_open_tickets = {111}  # ticket 111 was open last tick, now gone -> "closed"

        monkeypatch.setattr(server_mod, "get_open_positions", lambda magic=None: [])  # nothing open now
        closed_info = ClosedPositionInfo(ticket=111, symbol="EURUSD", direction="BUY", entry_price=1.10, exit_price=1.095, pnl=-50.0, exit_reason="SL")
        monkeypatch.setattr(server_mod, "get_closed_position_info", lambda ticket: closed_info)

        notified = {"calls": []}
        monkeypatch.setattr(state.notifier, "notify_trade_closed", lambda *a, **k: notified["calls"].append(a) or True)

        class StopLoop(Exception):
            pass

        async def fake_sleep(_):
            raise StopLoop

        monkeypatch.setattr(server_mod.asyncio, "sleep", fake_sleep)

        with pytest.raises(StopLoop):
            await server_mod._closed_trade_watcher(state)

        assert len(notified["calls"]) == 1
        assert state.known_open_tickets == set()
        journal_entries = state.journal.read_all()
        assert journal_entries[0]["ticket"] == 111
        assert journal_entries[0]["closed"] is True

    async def test_bad_tick_does_not_crash_watcher(self, monkeypatch, tmp_path):
        from app.api.state import AppState
        from app.config import Settings

        settings = Settings(_env_file=None)
        state = AppState(
            settings=settings, connection=None, risk_manager=None,
            equity_tracker=EquityTracker(tmp_path / "equity.json"), journal=TradeJournal(tmp_path / "journal.jsonl"),
            emergency_stop=EmergencyStop(tmp_path / "STOP"), notifier=server_mod.TelegramNotifier(settings),
        )

        def raise_error(magic=None):
            raise RuntimeError("MT5 disconnected mid-tick")

        monkeypatch.setattr(server_mod, "get_open_positions", raise_error)

        class StopLoop(Exception):
            pass

        async def fake_sleep(_):
            raise StopLoop

        monkeypatch.setattr(server_mod.asyncio, "sleep", fake_sleep)

        with pytest.raises(StopLoop):  # the watcher's own except swallows the RuntimeError and reaches sleep()
            await server_mod._closed_trade_watcher(state)
