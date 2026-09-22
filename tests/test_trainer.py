from __future__ import annotations

import pytest

from app.ai.features import prepare_training_data
from app.ai.model import ModelType
from app.ai.trainer import chronological_split, train_and_evaluate, walk_forward_train_evaluate
from tests._synthetic import make_trending_candles


class TestChronologicalSplit:
    def test_split_sizes_and_order(self):
        df = make_trending_candles(n=800)
        X, y = prepare_training_data(df, horizon_bars=8)
        X_train, y_train, X_val, y_val, X_test, y_test = chronological_split(X, y, train_frac=0.6, val_frac=0.2)

        assert len(X_train) + len(X_val) + len(X_test) == len(X)
        assert X_train.index.max() < X_val.index.min()
        assert X_val.index.max() < X_test.index.min()
        assert len(y_train) == len(X_train)

    @pytest.mark.parametrize("train_frac,val_frac", [(0.9, 0.2), (0, 0.2), (0.6, 0)])
    def test_invalid_fractions_raise(self, train_frac, val_frac):
        df = make_trending_candles(n=500)
        X, y = prepare_training_data(df, horizon_bars=8)
        with pytest.raises(ValueError):
            chronological_split(X, y, train_frac=train_frac, val_frac=val_frac)


class TestTrainAndEvaluate:
    def test_runs_end_to_end_on_trending_data(self):
        df = make_trending_candles(n=1200)
        result = train_and_evaluate(df, model_type=ModelType.RANDOM_FOREST, horizon_bars=8, n_estimators=30, symbol="TEST", timeframe="H1")

        assert result.n_train + result.n_val + result.n_test > 0
        assert 0.0 <= result.train_metrics.accuracy <= 1.0
        assert 0.0 <= result.val_metrics.accuracy <= 1.0
        assert 0.0 <= result.test_metrics.accuracy <= 1.0
        assert result.model.metadata.symbol == "TEST"

    def test_raises_when_too_little_data(self):
        df = make_trending_candles(n=210)  # barely past warmup, nowhere near 100 labeled rows
        with pytest.raises(ValueError):
            train_and_evaluate(df, n_estimators=10)


class TestWalkForwardTrainEvaluate:
    def test_produces_results_across_multiple_windows(self):
        df = make_trending_candles(n=2500)
        results = walk_forward_train_evaluate(
            df, train_bars=800, test_bars=400, model_type=ModelType.RANDOM_FOREST, horizon_bars=8, n_estimators=20
        )
        assert len(results) > 0
        for r in results:
            assert 0.0 <= r.metrics.accuracy <= 1.0
            assert r.test_start_time < r.test_end_time

    def test_windows_are_chronological(self):
        df = make_trending_candles(n=2500)
        results = walk_forward_train_evaluate(
            df, train_bars=800, test_bars=400, model_type=ModelType.RANDOM_FOREST, horizon_bars=8, n_estimators=10
        )
        for a, b in zip(results, results[1:]):
            assert a.test_end_time < b.test_start_time
