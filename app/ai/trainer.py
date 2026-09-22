"""
Training pipeline: chronological train/validation/test split, walk-forward
evaluation, and classification metrics.

Design decisions:
- Splitting is strictly positional/chronological on an already
  time-ordered (X, y) - never `train_test_split(..., shuffle=True)`. For
  time series, a random shuffle would let the model train on bars that
  come chronologically *after* some of its own test bars, which is
  look-ahead bias baked directly into model evaluation (spec section 9).
  `chronological_split` asserts train/val/test are disjoint, ordered
  ranges as a runtime invariant, not just a docstring claim.
- Three-way split (train/validation/test), not two-way: validation is for
  model/hyperparameter selection (comparing model types, thresholds),
  test is touched only once, at the end, to report a final unbiased
  estimate of generalization - reusing the test set to tune anything
  makes it a second validation set in disguise.
- `walk_forward_train_evaluate` reuses `app.backtesting.walk_forward`'s
  window generator unmodified - the leakage-prevention guarantee it
  already has (proven in tests/test_walk_forward.py) applies identically
  here, since fitting a model on train_df and scoring it on test_df is
  the same shape of problem as fitting a rule-based strategy on train_df
  and backtesting it on test_df.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix

from app.ai.features import LABEL_CLASSES, prepare_training_data
from app.ai.model import ModelMetadata, ModelType, TradingModel
from app.backtesting.walk_forward import generate_walk_forward_windows


@dataclass
class ClassificationMetrics:
    accuracy: float
    report: dict
    confusion: np.ndarray
    n_samples: int


def evaluate_classifier(model: TradingModel, X: pd.DataFrame, y: pd.Series) -> ClassificationMetrics:
    if len(X) == 0:
        return ClassificationMetrics(accuracy=0.0, report={}, confusion=np.zeros((3, 3)), n_samples=0)
    y_pred = model.predict(X)
    accuracy = accuracy_score(y, y_pred)
    report = classification_report(y, y_pred, labels=LABEL_CLASSES, output_dict=True, zero_division=0)
    confusion = confusion_matrix(y, y_pred, labels=LABEL_CLASSES)
    return ClassificationMetrics(accuracy=accuracy, report=report, confusion=confusion, n_samples=len(X))


def chronological_split(
    X: pd.DataFrame, y: pd.Series, train_frac: float = 0.6, val_frac: float = 0.2
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
    if not (0 < train_frac < 1) or not (0 < val_frac < 1) or train_frac + val_frac >= 1:
        raise ValueError("train_frac and val_frac must be positive and sum to less than 1")

    n = len(X)
    n_train = int(n * train_frac)
    n_val = int(n * val_frac)

    X_train, y_train = X.iloc[:n_train], y.iloc[:n_train]
    X_val, y_val = X.iloc[n_train : n_train + n_val], y.iloc[n_train : n_train + n_val]
    X_test, y_test = X.iloc[n_train + n_val :], y.iloc[n_train + n_val :]

    assert X_train.index.max() < X_val.index.min(), "train/validation split is not chronological"
    assert X_val.index.max() < X_test.index.min(), "validation/test split is not chronological"

    return X_train, y_train, X_val, y_val, X_test, y_test


@dataclass
class TrainingResult:
    model: TradingModel
    train_metrics: ClassificationMetrics
    val_metrics: ClassificationMetrics
    test_metrics: ClassificationMetrics
    n_train: int
    n_val: int
    n_test: int


def train_and_evaluate(
    df: pd.DataFrame,
    model_type: ModelType = ModelType.RANDOM_FOREST,
    horizon_bars: int = 8,
    atr_multiple: float = 1.0,
    train_frac: float = 0.6,
    val_frac: float = 0.2,
    symbol: str | None = None,
    timeframe: str | None = None,
    **estimator_kwargs,
) -> TrainingResult:
    X, y = prepare_training_data(df, horizon_bars=horizon_bars, atr_multiple=atr_multiple)
    if len(X) < 100:
        raise ValueError(f"Not enough labeled rows to train on ({len(X)}) - provide more history")

    X_train, y_train, X_val, y_val, X_test, y_test = chronological_split(X, y, train_frac, val_frac)

    metadata = ModelMetadata(
        model_type=model_type.value,
        horizon_bars=horizon_bars,
        atr_multiple=atr_multiple,
        symbol=symbol,
        timeframe=timeframe,
    )
    model = TradingModel(model_type, metadata=metadata, **estimator_kwargs)
    model.fit(X_train, y_train)

    return TrainingResult(
        model=model,
        train_metrics=evaluate_classifier(model, X_train, y_train),
        val_metrics=evaluate_classifier(model, X_val, y_val),
        test_metrics=evaluate_classifier(model, X_test, y_test),
        n_train=len(X_train),
        n_val=len(X_val),
        n_test=len(X_test),
    )


@dataclass
class WalkForwardTrainResult:
    window_index: int
    test_start_time: pd.Timestamp
    test_end_time: pd.Timestamp
    metrics: ClassificationMetrics


def walk_forward_train_evaluate(
    df: pd.DataFrame,
    train_bars: int,
    test_bars: int,
    model_type: ModelType = ModelType.RANDOM_FOREST,
    horizon_bars: int = 8,
    atr_multiple: float = 1.0,
    step_bars: int | None = None,
    mode: str = "rolling",
    **estimator_kwargs,
) -> list[WalkForwardTrainResult]:
    """
    Fits a fresh model on each window's train_df and scores it on that
    window's own (never-seen-by-this-model) test_df, per spec section 17.
    """
    results: list[WalkForwardTrainResult] = []
    for window in generate_walk_forward_windows(df, train_bars, test_bars, step_bars, mode):
        X_train, y_train = prepare_training_data(window.train_df, horizon_bars=horizon_bars, atr_multiple=atr_multiple)
        X_test, y_test = prepare_training_data(window.test_df, horizon_bars=horizon_bars, atr_multiple=atr_multiple)

        if len(X_train) < 50 or len(X_test) == 0:
            continue

        model = TradingModel(model_type, metadata=ModelMetadata(model_type=model_type.value, horizon_bars=horizon_bars, atr_multiple=atr_multiple), **estimator_kwargs)
        model.fit(X_train, y_train)
        metrics = evaluate_classifier(model, X_test, y_test)

        results.append(
            WalkForwardTrainResult(
                window_index=window.window_index,
                test_start_time=window.test_start_time,
                test_end_time=window.test_end_time,
                metrics=metrics,
            )
        )
    return results
