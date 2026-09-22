from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.ai.features import FEATURE_COLUMNS, LABEL_CLASSES, prepare_training_data
from app.ai.model import ModelType, TradingModel
from tests._synthetic import make_ranging_candles, make_trending_candles


def small_training_set():
    df = make_trending_candles(n=500)
    return prepare_training_data(df, horizon_bars=8)


def mixed_class_training_set():
    """A oscillating (ranging) price series, rather than a one-directional
    trend, so labels actually include a mix of BUY/SELL/NEUTRAL - needed
    for tests that check the model produces real, non-degenerate splits."""
    df = make_ranging_candles(n=500)
    return prepare_training_data(df, horizon_bars=8)


class TestTradingModelFitPredict:
    def test_predict_proba_has_all_three_classes_summing_to_one(self):
        X, y = small_training_set()
        model = TradingModel(ModelType.RANDOM_FOREST, n_estimators=30)
        model.fit(X, y)
        proba = model.predict_proba(X.iloc[:10])
        assert list(proba.columns) == LABEL_CLASSES
        assert np.allclose(proba.sum(axis=1).to_numpy(), 1.0, atol=1e-6)

    def test_fit_rejects_wrong_columns(self):
        X, y = small_training_set()
        bad_X = X.rename(columns={FEATURE_COLUMNS[0]: "wrong_name"})
        model = TradingModel(ModelType.RANDOM_FOREST, n_estimators=10)
        with pytest.raises(ValueError):
            model.fit(bad_X, y)

    def test_predict_proba_before_fit_raises(self):
        X, _ = small_training_set()
        model = TradingModel(ModelType.RANDOM_FOREST)
        with pytest.raises(RuntimeError):
            model.predict_proba(X.iloc[:5])

    def test_missing_class_in_training_data_still_produces_full_width_output(self):
        X, y = small_training_set()
        # Force a training set with no SELL examples at all.
        mask = y != "SELL"
        X_no_sell, y_no_sell = X.loc[mask], y.loc[mask]
        model = TradingModel(ModelType.RANDOM_FOREST, n_estimators=20)
        model.fit(X_no_sell, y_no_sell)
        proba = model.predict_proba(X.iloc[:20])
        assert list(proba.columns) == LABEL_CLASSES
        assert (proba["SELL"] == 0.0).all()
        assert np.allclose(proba.sum(axis=1).to_numpy(), 1.0, atol=1e-6)

    def test_feature_importances_available_for_tree_models(self):
        X, y = mixed_class_training_set()
        model = TradingModel(ModelType.RANDOM_FOREST, n_estimators=20)
        model.fit(X, y)
        importances = model.feature_importances()
        assert importances is not None
        assert set(importances.index) == set(FEATURE_COLUMNS)
        assert abs(importances.sum() - 1.0) < 1e-6


class TestAlternativeEstimators:
    """Random Forest is the default (spec section 8); Gradient Boosting and
    XGBoost are available behind the same TradingModel interface - these
    just confirm both actually build and fit, not that they outperform."""

    def test_gradient_boosting_fits_and_predicts(self):
        # Ranging (not trending) data, same reason as mixed_class_training_set
        # elsewhere in this file: a one-directional trend produces near-single-
        # class labels, which XGBoost/GradientBoosting (unlike RandomForest)
        # can fail to fit against their own num_class expectations.
        X, y = mixed_class_training_set()
        model = TradingModel(ModelType.GRADIENT_BOOSTING, n_estimators=10, max_depth=2)
        model.fit(X, y)
        proba = model.predict_proba(X.iloc[:5])
        assert list(proba.columns) == LABEL_CLASSES
        assert np.allclose(proba.sum(axis=1).to_numpy(), 1.0, atol=1e-6)

    def test_xgboost_fits_and_predicts(self):
        X, y = mixed_class_training_set()
        model = TradingModel(ModelType.XGBOOST, n_estimators=10, max_depth=2)
        model.fit(X, y)
        proba = model.predict_proba(X.iloc[:5])
        assert list(proba.columns) == LABEL_CLASSES
        assert np.allclose(proba.sum(axis=1).to_numpy(), 1.0, atol=1e-6)

    def test_unknown_model_type_raises(self):
        with pytest.raises(ValueError):
            TradingModel("not_a_real_model_type")


class TestSaveLoad:
    def test_round_trip_preserves_predictions_and_metadata(self, tmp_path):
        X, y = small_training_set()
        model = TradingModel(ModelType.RANDOM_FOREST, n_estimators=20)
        model.metadata.symbol = "EURUSD"
        model.metadata.timeframe = "H1"
        model.fit(X, y)

        proba_before = model.predict_proba(X.iloc[:10])

        path = tmp_path / "model.joblib"
        model.save(path)
        loaded = TradingModel.load(path)

        proba_after = loaded.predict_proba(X.iloc[:10])
        pd.testing.assert_frame_equal(proba_before, proba_after)
        assert loaded.metadata.symbol == "EURUSD"
        assert loaded.metadata.timeframe == "H1"
