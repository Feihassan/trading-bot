from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app.mt5.market_data as md
from app.config import Settings
from app.mt5.market_data import (
    MarketDataError,
    get_candles,
    get_candles_range,
    get_current_price,
    get_current_spread_points,
    get_symbol_spec,
    get_ticks,
    resolve_symbol,
)


def settings(**overrides) -> Settings:
    defaults = dict(_env_file=None, SYMBOLS="EURUSD,XAUUSD:XAUUSDm")
    defaults.update(overrides)
    return Settings(**defaults)


def visible_symbol_info(**overrides):
    defaults = dict(
        visible=True, digits=5, point=0.00001, spread=10, spread_float=True, trade_contract_size=100_000.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01, trade_tick_value=1.0, trade_tick_size=0.00001,
        currency_base="EUR", currency_profit="USD", currency_margin="EUR", name="EURUSD",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


FAKE_RATES = [
    dict(time=1700000000, open=1.10, high=1.11, low=1.09, close=1.105, tick_volume=100, spread=1, real_volume=0),
    dict(time=1700000900, open=1.105, high=1.115, low=1.10, close=1.11, tick_volume=120, spread=1, real_volume=0),
]


class TestResolveSymbol:
    def test_known_canonical_symbol_resolves(self):
        assert resolve_symbol("EURUSD", settings()) == "EURUSD"

    def test_broker_suffix_mapping_resolves(self):
        assert resolve_symbol("XAUUSD", settings()) == "XAUUSDm"

    def test_case_insensitive(self):
        assert resolve_symbol("eurusd", settings()) == "EURUSD"

    def test_unknown_symbol_raises(self):
        with pytest.raises(MarketDataError):
            resolve_symbol("NOTASYMBOL", settings())


class TestEnsureSymbolSelected:
    def test_unknown_broker_symbol_raises(self, monkeypatch):
        monkeypatch.setattr(md.mt5, "symbol_info", lambda s: None)
        monkeypatch.setattr(md.mt5, "last_error", lambda: (-1, "unknown symbol"))
        with pytest.raises(MarketDataError):
            get_candles("NOTREAL", "H1")

    def test_invisible_symbol_gets_selected(self, monkeypatch):
        select_calls = {"args": None}
        monkeypatch.setattr(md.mt5, "symbol_info", lambda s: visible_symbol_info(visible=False))
        monkeypatch.setattr(md.mt5, "symbol_select", lambda s, v: select_calls.update(args=(s, v)) or True)
        monkeypatch.setattr(md.mt5, "copy_rates_from_pos", lambda *a, **kw: FAKE_RATES)
        get_candles("EURUSD", "H1")
        assert select_calls["args"] == ("EURUSD", True)

    def test_symbol_select_failure_raises(self, monkeypatch):
        monkeypatch.setattr(md.mt5, "symbol_info", lambda s: visible_symbol_info(visible=False))
        monkeypatch.setattr(md.mt5, "symbol_select", lambda s, v: False)
        monkeypatch.setattr(md.mt5, "last_error", lambda: (-1, "cannot select"))
        with pytest.raises(MarketDataError):
            get_candles("EURUSD", "H1")


class TestGetCandles:
    def test_unsupported_timeframe_raises(self):
        with pytest.raises(MarketDataError):
            get_candles("EURUSD", "M2")

    def test_returns_dataframe_with_expected_columns(self, monkeypatch):
        monkeypatch.setattr(md.mt5, "symbol_info", lambda s: visible_symbol_info())
        monkeypatch.setattr(md.mt5, "copy_rates_from_pos", lambda *a, **kw: FAKE_RATES)
        df = get_candles("EURUSD", "H1", count=2)
        assert list(df.columns) == md.CANDLE_COLUMNS
        assert len(df) == 2
        assert str(df["time"].dt.tz) == "UTC"

    def test_none_rates_raises(self, monkeypatch):
        monkeypatch.setattr(md.mt5, "symbol_info", lambda s: visible_symbol_info())
        monkeypatch.setattr(md.mt5, "copy_rates_from_pos", lambda *a, **kw: None)
        monkeypatch.setattr(md.mt5, "last_error", lambda: (-1, "no data"))
        with pytest.raises(MarketDataError):
            get_candles("EURUSD", "H1")

    def test_empty_rates_raises(self, monkeypatch):
        monkeypatch.setattr(md.mt5, "symbol_info", lambda s: visible_symbol_info())
        monkeypatch.setattr(md.mt5, "copy_rates_from_pos", lambda *a, **kw: [])
        monkeypatch.setattr(md.mt5, "last_error", lambda: (-1, "no data"))
        with pytest.raises(MarketDataError):
            get_candles("EURUSD", "H1")


class TestGetCandlesRange:
    def test_returns_expected_columns(self, monkeypatch):
        monkeypatch.setattr(md.mt5, "symbol_info", lambda s: visible_symbol_info())
        monkeypatch.setattr(md.mt5, "copy_rates_range", lambda *a, **kw: FAKE_RATES)
        df = get_candles_range("EURUSD", "H1", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))
        assert list(df.columns) == md.CANDLE_COLUMNS

    def test_empty_range_returns_empty_dataframe_not_error(self, monkeypatch):
        monkeypatch.setattr(md.mt5, "symbol_info", lambda s: visible_symbol_info())
        monkeypatch.setattr(md.mt5, "copy_rates_range", lambda *a, **kw: [])
        df = get_candles_range("EURUSD", "H1", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))
        assert df.empty
        assert list(df.columns) == md.CANDLE_COLUMNS

    def test_none_raises(self, monkeypatch):
        monkeypatch.setattr(md.mt5, "symbol_info", lambda s: visible_symbol_info())
        monkeypatch.setattr(md.mt5, "copy_rates_range", lambda *a, **kw: None)
        monkeypatch.setattr(md.mt5, "last_error", lambda: (-1, "no data"))
        with pytest.raises(MarketDataError):
            get_candles_range("EURUSD", "H1", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))


class TestGetTicks:
    def test_uses_copy_ticks_from_when_count_given(self, monkeypatch):
        monkeypatch.setattr(md.mt5, "symbol_info", lambda s: visible_symbol_info())
        called = {}
        monkeypatch.setattr(md.mt5, "copy_ticks_from", lambda *a, **kw: called.update(used="from") or [{"time": 1700000000, "bid": 1.1, "ask": 1.1001}])
        monkeypatch.setattr(md.mt5, "copy_ticks_range", lambda *a, **kw: called.update(used="range") or [])
        df = get_ticks("EURUSD", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc), count=100)
        assert called["used"] == "from"
        assert not df.empty

    def test_uses_copy_ticks_range_when_no_count(self, monkeypatch):
        monkeypatch.setattr(md.mt5, "symbol_info", lambda s: visible_symbol_info())
        called = {}
        monkeypatch.setattr(md.mt5, "copy_ticks_range", lambda *a, **kw: called.update(used="range") or [{"time": 1700000000, "bid": 1.1, "ask": 1.1001}])
        df = get_ticks("EURUSD", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))
        assert called["used"] == "range"

    def test_none_raises(self, monkeypatch):
        monkeypatch.setattr(md.mt5, "symbol_info", lambda s: visible_symbol_info())
        monkeypatch.setattr(md.mt5, "copy_ticks_range", lambda *a, **kw: None)
        monkeypatch.setattr(md.mt5, "last_error", lambda: (-1, "no ticks"))
        with pytest.raises(MarketDataError):
            get_ticks("EURUSD", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))

    def test_empty_ticks_returns_empty_dataframe(self, monkeypatch):
        monkeypatch.setattr(md.mt5, "symbol_info", lambda s: visible_symbol_info())
        monkeypatch.setattr(md.mt5, "copy_ticks_range", lambda *a, **kw: [])
        df = get_ticks("EURUSD", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))
        assert df.empty


class TestGetSymbolSpec:
    def test_returns_populated_spec(self, monkeypatch):
        monkeypatch.setattr(md.mt5, "symbol_info", lambda s: visible_symbol_info(name="EURUSD"))
        spec = get_symbol_spec("EURUSD")
        assert spec.name == "EURUSD"
        assert spec.trade_tick_value == 1.0
        assert spec.currency_base == "EUR"

    def test_missing_symbol_after_select_raises(self, monkeypatch):
        # First call (inside _ensure_symbol_selected) succeeds; simulate the
        # second lookup failing by tracking call count.
        calls = {"n": 0}

        def flaky_symbol_info(s):
            calls["n"] += 1
            return visible_symbol_info() if calls["n"] == 1 else None

        monkeypatch.setattr(md.mt5, "symbol_info", flaky_symbol_info)
        monkeypatch.setattr(md.mt5, "last_error", lambda: (-1, "vanished"))
        with pytest.raises(MarketDataError):
            get_symbol_spec("EURUSD")


class TestSpreadAndPrice:
    def test_current_spread_points_computed_from_tick(self, monkeypatch):
        monkeypatch.setattr(md.mt5, "symbol_info", lambda s: visible_symbol_info(point=0.0001))
        monkeypatch.setattr(md.mt5, "symbol_info_tick", lambda s: SimpleNamespace(bid=1.1000, ask=1.1002))
        spread = get_current_spread_points("EURUSD")
        assert spread == 2

    def test_current_spread_points_no_tick_raises(self, monkeypatch):
        monkeypatch.setattr(md.mt5, "symbol_info", lambda s: visible_symbol_info())
        monkeypatch.setattr(md.mt5, "symbol_info_tick", lambda s: None)
        monkeypatch.setattr(md.mt5, "last_error", lambda: (-1, "no tick"))
        with pytest.raises(MarketDataError):
            get_current_spread_points("EURUSD")

    def test_current_price_returns_bid_ask(self, monkeypatch):
        monkeypatch.setattr(md.mt5, "symbol_info", lambda s: visible_symbol_info())
        monkeypatch.setattr(md.mt5, "symbol_info_tick", lambda s: SimpleNamespace(bid=1.1000, ask=1.1002))
        bid, ask = get_current_price("EURUSD")
        assert bid == 1.1000
        assert ask == 1.1002

    def test_current_price_no_tick_raises(self, monkeypatch):
        monkeypatch.setattr(md.mt5, "symbol_info", lambda s: visible_symbol_info())
        monkeypatch.setattr(md.mt5, "symbol_info_tick", lambda s: None)
        monkeypatch.setattr(md.mt5, "last_error", lambda: (-1, "no tick"))
        with pytest.raises(MarketDataError):
            get_current_price("EURUSD")
