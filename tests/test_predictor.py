from __future__ import annotations

import pytest

from app.ai.features import LABEL_CLASSES, prepare_training_data
from app.ai.model import ModelType, TradingModel
from app.ai.predictor import predict_latest
from tests._synthetic import make_trending_candles


def trained_model():
    df = make_trending_candles(n=600)
    X, y = prepare_training_data(df, horizon_bars=8)
    model = TradingModel(ModelType.RANDOM_FOREST, n_estimators=30)
    model.fit(X, y)
    return model


class TestPredictLatest:
    def test_returns_valid_decision_structure(self):
        model = trained_model()
        df = make_trending_candles(n=600)  # enough history for ema_200 warmup
        result = predict_latest(model, df)

        assert set(result.probabilities.keys()) == set(LABEL_CLASSES)
        assert abs(sum(result.probabilities.values()) - 1.0) < 1e-6
        assert result.decision.action in {"BUY", "SELL", "WAIT"}
        assert result.timestamp == df["time"].iloc[-1]

    def test_insufficient_warmup_history_raises(self):
        model = trained_model()
        short_df = make_trending_candles(n=50)  # far short of ema_200's 200-bar warmup
        with pytest.raises(ValueError):
            predict_latest(model, short_df)
