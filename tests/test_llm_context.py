from __future__ import annotations

from app.llm.context import build_market_context
from tests._synthetic import make_trending_candles


class TestBuildMarketContext:
    def test_produces_all_expected_sections(self):
        df = make_trending_candles(n=400)
        ctx = build_market_context(
            df, "EURUSD", "H1",
            current_positions=[{"ticket": 1, "type": "BUY", "volume": 0.1}],
            risk_state={"daily_loss_pct": 0.01, "open_positions": 1},
            ml_probabilities={"BUY": 0.6, "NEUTRAL": 0.3, "SELL": 0.1},
        )
        assert ctx.symbol == "EURUSD"
        assert ctx.timeframe == "H1"
        assert ctx.trend in ("UPTREND", "DOWNTREND", "SIDEWAYS", "UNKNOWN")
        assert "rsi" in ctx.indicators
        assert "bos_bullish" in ctx.market_structure
        assert "atr_percentile" in ctx.volatility
        assert set(ctx.support_resistance.keys()) == {"support", "resistance"}
        assert ctx.ml_probabilities == {"BUY": 0.6, "NEUTRAL": 0.3, "SELL": 0.1}
        assert ctx.current_positions == [{"ticket": 1, "type": "BUY", "volume": 0.1}]
        assert ctx.risk_state == {"daily_loss_pct": 0.01, "open_positions": 1}

    def test_to_dict_is_json_serializable(self):
        import json

        df = make_trending_candles(n=400)
        ctx = build_market_context(df, "EURUSD", "H1")
        json.dumps(ctx.to_dict())  # raises if anything isn't serializable

    def test_defaults_are_empty_not_none_for_optional_inputs(self):
        df = make_trending_candles(n=400)
        ctx = build_market_context(df, "EURUSD", "H1")
        assert ctx.current_positions == []
        assert ctx.risk_state == {}
        assert ctx.ml_probabilities is None
