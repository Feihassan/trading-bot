from __future__ import annotations

from app.strategy.indicators import add_all_indicators
from app.strategy.regime import MarketRegime, classify_regime, regime_allows_trend_following
from tests._synthetic import make_ranging_candles, make_trending_candles


class TestRegimeClassification:
    def test_strong_trend_detected_in_trending_data(self):
        df = classify_regime(add_all_indicators(make_trending_candles(n=300)))
        late = df["regime"].iloc[-30:]
        trend_labels = {MarketRegime.STRONG_BULLISH_TREND.value, MarketRegime.WEAK_BULLISH_TREND.value}
        assert (late.isin(trend_labels)).sum() >= 15

    def test_ranging_data_mostly_classified_as_range_or_low_vol(self):
        df = classify_regime(add_all_indicators(make_ranging_candles(n=300)))
        late = df["regime"].iloc[-30:]
        non_trend_labels = {
            MarketRegime.RANGE.value,
            MarketRegime.LOW_VOLATILITY.value,
            MarketRegime.HIGH_VOLATILITY.value,
        }
        assert (late.isin(non_trend_labels)).sum() >= 15

    def test_early_warmup_rows_are_unknown(self):
        df = classify_regime(add_all_indicators(make_trending_candles(n=300)))
        assert (df["regime"].iloc[:190] == MarketRegime.UNKNOWN.value).all()


class TestTrendFollowingGate:
    def test_trend_regimes_allow_trend_following(self):
        assert regime_allows_trend_following(MarketRegime.STRONG_BULLISH_TREND)
        assert regime_allows_trend_following(MarketRegime.WEAK_BEARISH_TREND)

    def test_range_and_volatility_regimes_block_trend_following(self):
        assert not regime_allows_trend_following(MarketRegime.RANGE)
        assert not regime_allows_trend_following(MarketRegime.HIGH_VOLATILITY)
        assert not regime_allows_trend_following(MarketRegime.LOW_VOLATILITY)
