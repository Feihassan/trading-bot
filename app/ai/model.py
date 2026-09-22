"""
Model wrapper: a thin, interpretable layer over scikit-learn/XGBoost
classifiers with a fixed 3-class output (SELL/NEUTRAL/BUY) and joblib
persistence.

Design decisions:
- Random Forest is the default (spec section 8: start with practical,
  interpretable models, not deep learning "because it sounds advanced").
  Gradient Boosting and XGBoost are available as drop-in alternatives
  behind the same interface for later comparison.
- `predict_proba` always returns all three classes in a fixed column
  order, even if the fitted estimator's `.classes_` doesn't include one
  (e.g. a training window with zero SELL examples) - a missing class is
  filled with probability 0.0 rather than silently reshaping the output,
  since a caller (confidence.py, the dashboard) should never have to
  guard against a variable-width probability vector.
- Everything needed to reproduce or sanity-check a saved model travels
  with it: feature column order, label horizon/threshold, training
  timestamp, symbol/timeframe it was trained on. Loading a model without
  this metadata and blindly feeding it today's features is exactly how
  train/serve skew bugs happen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier

from app.ai.features import FEATURE_COLUMNS, LABEL_CLASSES, LABEL_TO_INT


class ModelType(str, Enum):
    RANDOM_FOREST = "random_forest"
    GRADIENT_BOOSTING = "gradient_boosting"
    XGBOOST = "xgboost"


def _build_estimator(model_type: ModelType, **kwargs: Any):
    if model_type == ModelType.RANDOM_FOREST:
        params = dict(n_estimators=300, max_depth=8, min_samples_leaf=20, class_weight="balanced", random_state=42, n_jobs=-1)
        params.update(kwargs)
        return RandomForestClassifier(**params)
    if model_type == ModelType.GRADIENT_BOOSTING:
        params = dict(n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42)
        params.update(kwargs)
        return GradientBoostingClassifier(**params)
    if model_type == ModelType.XGBOOST:
        import xgboost as xgb

        params = dict(
            n_estimators=300,
            max_depth=4,
            learning_rate=0.05,
            objective="multi:softprob",
            num_class=3,
            eval_metric="mlogloss",
            random_state=42,
            n_jobs=-1,
        )
        params.update(kwargs)
        return xgb.XGBClassifier(**params)
    raise ValueError(f"Unknown model_type: {model_type}")


@dataclass
class ModelMetadata:
    model_type: str
    feature_columns: list[str] = field(default_factory=lambda: list(FEATURE_COLUMNS))
    label_classes: list[str] = field(default_factory=lambda: list(LABEL_CLASSES))
    horizon_bars: int = 8
    atr_multiple: float = 1.0
    trained_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    symbol: str | None = None
    timeframe: str | None = None
    train_rows: int = 0


class TradingModel:
    def __init__(self, model_type: ModelType = ModelType.RANDOM_FOREST, metadata: ModelMetadata | None = None, **estimator_kwargs: Any):
        self.model_type = model_type
        self.estimator = _build_estimator(model_type, **estimator_kwargs)
        self.metadata = metadata or ModelMetadata(model_type=model_type.value)
        self._fitted = False

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "TradingModel":
        if list(X.columns) != FEATURE_COLUMNS:
            raise ValueError("X columns do not match FEATURE_COLUMNS - build X via app.ai.features.build_feature_matrix")
        y_int = y.map(LABEL_TO_INT)
        if y_int.isna().any():
            raise ValueError("y contains labels outside LABEL_CLASSES")
        self.estimator.fit(X.to_numpy(), y_int.to_numpy())
        self.metadata.train_rows = len(X)
        self._fitted = True
        return self

    def predict_proba(self, X: pd.DataFrame) -> pd.DataFrame:
        if not self._fitted:
            raise RuntimeError("Model has not been fit yet")
        if list(X.columns) != FEATURE_COLUMNS:
            raise ValueError("X columns do not match FEATURE_COLUMNS")

        raw_proba = self.estimator.predict_proba(X.to_numpy())
        fitted_classes = self.estimator.classes_  # subset of {0,1,2} actually seen in training

        full = np.zeros((len(X), len(LABEL_CLASSES)), dtype=float)
        for col_idx, class_int in enumerate(fitted_classes):
            full[:, class_int] = raw_proba[:, col_idx]

        return pd.DataFrame(full, columns=LABEL_CLASSES, index=X.index)

    def predict(self, X: pd.DataFrame) -> pd.Series:
        proba = self.predict_proba(X)
        return proba.idxmax(axis=1).rename("prediction")

    def feature_importances(self) -> pd.Series | None:
        importances = getattr(self.estimator, "feature_importances_", None)
        if importances is None:
            return None
        return pd.Series(importances, index=FEATURE_COLUMNS).sort_values(ascending=False)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"estimator": self.estimator, "model_type": self.model_type, "metadata": self.metadata}, path)

    @classmethod
    def load(cls, path: str | Path) -> "TradingModel":
        payload = joblib.load(Path(path))
        obj = cls.__new__(cls)
        obj.model_type = payload["model_type"]
        obj.estimator = payload["estimator"]
        obj.metadata = payload["metadata"]
        obj._fitted = True
        return obj
