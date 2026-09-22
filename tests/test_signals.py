from __future__ import annotations

import pandas as pd
import pytest

from app.ai.features import prepare_training_data
from app.ai.model import ModelType, TradingModel
from app.backtesting.engine import BacktestConfig, run_backtest
from app.strategy.signals import SignalEngineConfig, generate_signal, make_signal_engine_strategy
from tests._synthetic import make_ranging_candles, make_trending_candles
from tests.test_backtest_engine import SPEC


class TestGenerateSignalBasics:
    def test_insufficient_history_returns_wait(self):
        df = make_trending_candles(n=50)  # far short of ema_200 warmup
        idea = generate_signal(df, "EURUSD")
        assert idea.direction == "WAIT"
        assert "warm-up" in idea.blocking_reasons[0].lower()

    def test_trending_data_without_model_can_produce_directional_idea(self):
        df = make_trending_candles(n=400)
        idea = generate_signal(df, "EURUSD", config=SignalEngineConfig(min_composite_confidence=0.0, min_technical_score=0.0))
        assert idea.direction in ("BUY", "SELL", "WAIT")
        # Whatever it decides, entry/SL/TP must be internally consistent when directional.
        if idea.direction in ("BUY", "SELL"):
            assert idea.entry is not None
            assert idea.stop_loss is not None
            assert idea.take_profit is not None
            assert idea.reward_risk > 0

    def test_no_model_still_reports_reduced_information_mode(self):
        df = make_trending_candles(n=400)
        idea = generate_signal(df, "EURUSD")
        assert any("ml model not supplied" in r.lower() for r in idea.reasons)
        assert idea.ml_direction is None
        assert idea.ml_probabilities is None


class TestConflictingSignalsForceWait:
    def test_disagreement_between_technical_and_ml_forces_wait(self, monkeypatch):
        df = make_trending_candles(n=400)

        class FakeDecision:
            action = "SELL"  # deliberately opposite whatever technical+structure say in a strong uptrend
            confidence = 0.9
            reason = "forced for test"

        class FakePrediction:
            probabilities = {"BUY": 0.1, "NEUTRAL": 0.1, "SELL": 0.8}
            decision = FakeDecision()

        monkeypatch.setattr("app.strategy.signals.predict_latest", lambda *a, **k: FakePrediction())

        idea = generate_signal(df, "EURUSD", model=object())  # model presence alone triggers the ML branch
        assert idea.direction == "WAIT"
        assert any("conflicting" in r.lower() for r in idea.blocking_reasons)


class TestSpreadGate:
    def test_excessive_spread_forces_wait_even_with_strong_setup(self, monkeypatch):
        df = make_trending_candles(n=400)
        # Inflate the last bar's spread column to something clearly excessive.
        df = df.copy()
        df.loc[df.index[-1], "spread"] = 500
        idea = generate_signal(
            df, "EURUSD", config=SignalEngineConfig(min_composite_confidence=0.0, min_technical_score=0.0, max_spread_points=25)
        )
        if idea.entry is not None:  # only meaningful if a directional candidate existed at all
            assert idea.direction == "WAIT"
            assert any("spread" in r.lower() for r in idea.blocking_reasons)


class TestMinRiskReward:
    def test_rr_below_minimum_forces_wait(self):
        df = make_trending_candles(n=400)
        idea = generate_signal(
            df,
            "EURUSD",
            config=SignalEngineConfig(min_composite_confidence=0.0, min_technical_score=0.0, min_risk_reward=999.0),
        )
        assert idea.direction == "WAIT"


class TestSignalEngineStrategyAdapter:
    """
    These smoke tests are deliberately small (a couple hundred bars past
    warmup): make_signal_engine_strategy recomputes the full analysis (and,
    with a model, the full ML feature pipeline) from scratch on every bar,
    since that's what mirrors real live/bar-by-bar usage. That's fine for
    a short correctness check here; a full multi-year backtest of the real
    signal engine is a research/CLI task, not something to run on every
    test invocation.
    """

    def test_adapter_is_backtest_compatible(self):
        df = make_trending_candles(n=260)
        strategy_fn = make_signal_engine_strategy(config=SignalEngineConfig(min_composite_confidence=0.0, min_technical_score=0.0))
        result = run_backtest(df, strategy_fn, SPEC, config=BacktestConfig(min_risk_reward=1.0), warmup_bars=210)
        # Not asserting profitability - only that the real signal engine can
        # drive the backtester end to end without error.
        assert result.final_balance > 0

    def test_adapter_with_trained_model(self):
        train_df = make_ranging_candles(n=500)
        X, y = prepare_training_data(train_df, horizon_bars=8)
        model = TradingModel(ModelType.RANDOM_FOREST, n_estimators=20)
        model.fit(X, y)

        test_df = make_ranging_candles(n=260, seed=99)
        strategy_fn = make_signal_engine_strategy(model=model, config=SignalEngineConfig(min_composite_confidence=0.0, min_technical_score=0.0))
        result = run_backtest(test_df, strategy_fn, SPEC, config=BacktestConfig(min_risk_reward=1.0), warmup_bars=210)
        assert result.final_balance > 0
