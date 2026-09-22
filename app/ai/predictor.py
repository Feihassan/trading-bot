"""
Inference: turns a live/current OHLCV DataFrame into a decision using a
trained TradingModel.

Design decision: this calls the exact same `build_feature_matrix` used in
training (imported, not reimplemented), on the exact same FEATURE_COLUMNS
order enforced inside `TradingModel.predict_proba`. That's what prevents
train/serve skew - there is no second, "live" feature computation path
that could quietly drift from the training one.

The caller must pass enough trailing history for the slowest indicator to
warm up (ema_200 needs 200 bars) - only the LAST row's features are used
for the prediction, but that row's indicator values depend on everything
before it.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.ai.confidence import ConfidenceThresholds, Decision, decide
from app.ai.features import build_feature_matrix
from app.ai.model import TradingModel


@dataclass
class PredictionResult:
    timestamp: pd.Timestamp
    probabilities: dict[str, float]
    decision: Decision


def predict_latest(
    model: TradingModel,
    df: pd.DataFrame,
    thresholds: ConfidenceThresholds | None = None,
) -> PredictionResult:
    features = build_feature_matrix(df)
    last_row = features.iloc[[-1]]

    if last_row.isna().any(axis=1).iloc[0]:
        raise ValueError(
            "Latest row has NaN features - not enough warm-up history was provided "
            "(need enough bars for the slowest indicator, e.g. ema_200)."
        )

    proba_df = model.predict_proba(last_row)
    probabilities = proba_df.iloc[0].to_dict()
    decision = decide(probabilities, thresholds)

    return PredictionResult(
        timestamp=df["time"].iloc[-1],
        probabilities=probabilities,
        decision=decision,
    )
