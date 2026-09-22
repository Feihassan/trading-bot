from __future__ import annotations

from types import SimpleNamespace

import app.mt5.orders as orders_mod


class TestSubmitMarketOrder:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(orders_mod.mt5, "symbol_info_tick", lambda s: SimpleNamespace(ask=1.1005, bid=1.1000))
        monkeypatch.setattr(orders_mod.mt5, "order_check", lambda req: SimpleNamespace(retcode=0, comment="ok"))
        monkeypatch.setattr(
            orders_mod.mt5,
            "order_send",
            lambda req: SimpleNamespace(retcode=orders_mod.mt5.TRADE_RETCODE_DONE, comment="Request executed", order=12345, volume=0.2, price=1.1005),
        )

        result = orders_mod.submit_market_order("EURUSD", "BUY", 0.2, 1.0950, 1.1100, deviation_points=20, magic=123, comment="test")
        assert result.success
        assert result.ticket == 12345
        assert result.volume == 0.2
        assert result.price == 1.1005

    def test_no_tick_fails_cleanly(self, monkeypatch):
        monkeypatch.setattr(orders_mod.mt5, "symbol_info_tick", lambda s: None)
        monkeypatch.setattr(orders_mod.MT5Error, "last", classmethod(lambda cls: orders_mod.MT5Error(code=-1, description="no connection")))

        result = orders_mod.submit_market_order("EURUSD", "BUY", 0.2, 1.0950, 1.1100, deviation_points=20, magic=123)
        assert not result.success
        assert result.ticket is None

    def test_order_check_rejection_prevents_send(self, monkeypatch):
        monkeypatch.setattr(orders_mod.mt5, "symbol_info_tick", lambda s: SimpleNamespace(ask=1.1005, bid=1.1000))
        monkeypatch.setattr(orders_mod.mt5, "order_check", lambda req: SimpleNamespace(retcode=10019, comment="No money"))
        sent = {"called": False}
        monkeypatch.setattr(orders_mod.mt5, "order_send", lambda req: sent.update(called=True))

        result = orders_mod.submit_market_order("EURUSD", "BUY", 100.0, 1.0950, 1.1100, deviation_points=20, magic=123)
        assert not result.success
        assert sent["called"] is False  # order_send must never be reached if order_check rejects

    def test_order_send_rejection(self, monkeypatch):
        monkeypatch.setattr(orders_mod.mt5, "symbol_info_tick", lambda s: SimpleNamespace(ask=1.1005, bid=1.1000))
        monkeypatch.setattr(orders_mod.mt5, "order_check", lambda req: SimpleNamespace(retcode=0, comment="ok"))
        monkeypatch.setattr(orders_mod.mt5, "order_send", lambda req: SimpleNamespace(retcode=10004, comment="Requote", order=0, volume=0, price=0))

        result = orders_mod.submit_market_order("EURUSD", "BUY", 0.2, 1.0950, 1.1100, deviation_points=20, magic=123)
        assert not result.success
        assert result.ticket is None

    def test_request_uses_ask_for_buy_and_bid_for_sell(self, monkeypatch):
        monkeypatch.setattr(orders_mod.mt5, "symbol_info_tick", lambda s: SimpleNamespace(ask=1.1005, bid=1.1000))
        monkeypatch.setattr(orders_mod.mt5, "order_check", lambda req: SimpleNamespace(retcode=0, comment="ok"))
        captured = {}

        def fake_send(req):
            captured.update(req)
            return SimpleNamespace(retcode=orders_mod.mt5.TRADE_RETCODE_DONE, comment="ok", order=1, volume=req["volume"], price=req["price"])

        monkeypatch.setattr(orders_mod.mt5, "order_send", fake_send)

        orders_mod.submit_market_order("EURUSD", "BUY", 0.1, 1.09, 1.12, deviation_points=20, magic=1)
        assert captured["price"] == 1.1005  # ask

        orders_mod.submit_market_order("EURUSD", "SELL", 0.1, 1.11, 1.08, deviation_points=20, magic=1)
        assert captured["price"] == 1.1000  # bid


class TestFillingModeSelection:
    def test_prefers_fok_when_supported(self, monkeypatch):
        monkeypatch.setattr(orders_mod.mt5, "symbol_info", lambda s: SimpleNamespace(filling_mode=1))  # SYMBOL_FILLING_FOK bit
        assert orders_mod._select_filling_mode("EURUSD") == orders_mod.mt5.ORDER_FILLING_FOK

    def test_falls_back_to_ioc_when_fok_unsupported(self, monkeypatch):
        monkeypatch.setattr(orders_mod.mt5, "symbol_info", lambda s: SimpleNamespace(filling_mode=2))  # SYMBOL_FILLING_IOC bit
        assert orders_mod._select_filling_mode("EURUSD") == orders_mod.mt5.ORDER_FILLING_IOC

    def test_falls_back_to_return_when_neither_supported(self, monkeypatch):
        monkeypatch.setattr(orders_mod.mt5, "symbol_info", lambda s: SimpleNamespace(filling_mode=0))
        assert orders_mod._select_filling_mode("EURUSD") == orders_mod.mt5.ORDER_FILLING_RETURN

    def test_request_uses_selected_filling_mode(self, monkeypatch):
        monkeypatch.setattr(orders_mod.mt5, "symbol_info", lambda s: SimpleNamespace(filling_mode=1))
        monkeypatch.setattr(orders_mod.mt5, "symbol_info_tick", lambda s: SimpleNamespace(ask=1.1005, bid=1.1000))
        monkeypatch.setattr(orders_mod.mt5, "order_check", lambda req: SimpleNamespace(retcode=0, comment="ok"))
        captured = {}

        def fake_send(req):
            captured.update(req)
            return SimpleNamespace(retcode=orders_mod.mt5.TRADE_RETCODE_DONE, comment="ok", order=1, volume=req["volume"], price=req["price"])

        monkeypatch.setattr(orders_mod.mt5, "order_send", fake_send)
        orders_mod.submit_market_order("EURUSD", "BUY", 0.1, 1.09, 1.12, deviation_points=20, magic=1)
        assert captured["type_filling"] == orders_mod.mt5.ORDER_FILLING_FOK


class TestModifyPositionSlTp:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(orders_mod.mt5, "positions_get", lambda **kw: [SimpleNamespace(symbol="EURUSD", volume=0.2, type=orders_mod.mt5.ORDER_TYPE_BUY)])
        monkeypatch.setattr(orders_mod.mt5, "order_send", lambda req: SimpleNamespace(retcode=orders_mod.mt5.TRADE_RETCODE_DONE, comment="ok"))

        result = orders_mod.modify_position_sltp(111, 1.0980, 1.1120)
        assert result.success
        assert result.ticket == 111

    def test_position_not_found(self, monkeypatch):
        monkeypatch.setattr(orders_mod.mt5, "positions_get", lambda **kw: [])
        result = orders_mod.modify_position_sltp(999, 1.0980, 1.1120)
        assert not result.success

    def test_order_send_none_handled_cleanly(self, monkeypatch):
        monkeypatch.setattr(orders_mod.mt5, "positions_get", lambda **kw: [SimpleNamespace(symbol="EURUSD", volume=0.2, type=orders_mod.mt5.ORDER_TYPE_BUY)])
        monkeypatch.setattr(orders_mod.mt5, "order_send", lambda req: None)
        monkeypatch.setattr(orders_mod.MT5Error, "last", classmethod(lambda cls: orders_mod.MT5Error(code=-1, description="disconnected")))

        result = orders_mod.modify_position_sltp(111, 1.0980, 1.1120)
        assert not result.success
        assert result.ticket is None

    def test_rejected_retcode_is_not_success(self, monkeypatch):
        monkeypatch.setattr(orders_mod.mt5, "positions_get", lambda **kw: [SimpleNamespace(symbol="EURUSD", volume=0.2, type=orders_mod.mt5.ORDER_TYPE_BUY)])
        monkeypatch.setattr(orders_mod.mt5, "order_send", lambda req: SimpleNamespace(retcode=10004, comment="Requote"))

        result = orders_mod.modify_position_sltp(111, 1.0980, 1.1120)
        assert not result.success
        assert result.ticket is None


class TestClosePosition:
    def test_closing_a_buy_sells_at_bid(self, monkeypatch):
        monkeypatch.setattr(
            orders_mod.mt5, "positions_get", lambda **kw: [SimpleNamespace(symbol="EURUSD", volume=0.2, type=orders_mod.mt5.ORDER_TYPE_BUY, magic=1)]
        )
        monkeypatch.setattr(orders_mod.mt5, "symbol_info_tick", lambda s: SimpleNamespace(ask=1.1005, bid=1.1000))
        captured = {}

        def fake_send(req):
            captured.update(req)
            return SimpleNamespace(retcode=orders_mod.mt5.TRADE_RETCODE_DONE, comment="ok", order=1, volume=req["volume"], price=req["price"])

        monkeypatch.setattr(orders_mod.mt5, "order_send", fake_send)

        result = orders_mod.close_position(111, deviation_points=20)
        assert result.success
        assert captured["type"] == orders_mod.mt5.ORDER_TYPE_SELL  # closing a BUY means selling
        assert captured["price"] == 1.1000  # bid

    def test_closing_a_sell_buys_at_ask(self, monkeypatch):
        monkeypatch.setattr(
            orders_mod.mt5, "positions_get", lambda **kw: [SimpleNamespace(symbol="EURUSD", volume=0.2, type=orders_mod.mt5.ORDER_TYPE_SELL, magic=1)]
        )
        monkeypatch.setattr(orders_mod.mt5, "symbol_info_tick", lambda s: SimpleNamespace(ask=1.1005, bid=1.1000))
        captured = {}

        def fake_send(req):
            captured.update(req)
            return SimpleNamespace(retcode=orders_mod.mt5.TRADE_RETCODE_DONE, comment="ok", order=1, volume=req["volume"], price=req["price"])

        monkeypatch.setattr(orders_mod.mt5, "order_send", fake_send)

        orders_mod.close_position(222, deviation_points=20)
        assert captured["type"] == orders_mod.mt5.ORDER_TYPE_BUY
        assert captured["price"] == 1.1005  # ask

    def test_position_not_found_returns_failure(self, monkeypatch):
        monkeypatch.setattr(orders_mod.mt5, "positions_get", lambda **kw: [])
        result = orders_mod.close_position(999, deviation_points=20)
        assert not result.success

    def test_no_tick_available_returns_failure(self, monkeypatch):
        monkeypatch.setattr(
            orders_mod.mt5, "positions_get", lambda **kw: [SimpleNamespace(symbol="EURUSD", volume=0.2, type=orders_mod.mt5.ORDER_TYPE_BUY, magic=1)]
        )
        monkeypatch.setattr(orders_mod.mt5, "symbol_info_tick", lambda s: None)
        monkeypatch.setattr(orders_mod.MT5Error, "last", classmethod(lambda cls: orders_mod.MT5Error(code=-1, description="no tick")))

        result = orders_mod.close_position(111, deviation_points=20)
        assert not result.success

    def test_order_send_none_handled_cleanly(self, monkeypatch):
        monkeypatch.setattr(
            orders_mod.mt5, "positions_get", lambda **kw: [SimpleNamespace(symbol="EURUSD", volume=0.2, type=orders_mod.mt5.ORDER_TYPE_BUY, magic=1)]
        )
        monkeypatch.setattr(orders_mod.mt5, "symbol_info_tick", lambda s: SimpleNamespace(ask=1.1005, bid=1.1000))
        monkeypatch.setattr(orders_mod.mt5, "order_send", lambda req: None)
        monkeypatch.setattr(orders_mod.MT5Error, "last", classmethod(lambda cls: orders_mod.MT5Error(code=-1, description="disconnected")))

        result = orders_mod.close_position(111, deviation_points=20)
        assert not result.success


class TestRetcodeName:
    def test_none_retcode_returns_na(self):
        assert orders_mod._retcode_name(None) == "N/A"

    def test_known_retcode_resolves_to_name(self):
        assert orders_mod._retcode_name(orders_mod.mt5.TRADE_RETCODE_DONE) == "TRADE_RETCODE_DONE"

    def test_unknown_retcode_falls_back_to_str(self):
        assert orders_mod._retcode_name(999999) == "999999"
