from __future__ import annotations

from app.backtesting.engine import BacktestConfig
from app.backtesting.example_strategy import make_ema_crossover_strategy, prepare_features
from app.backtesting.walk_forward import generate_walk_forward_windows, run_walk_forward_backtest
from tests._synthetic import make_trending_candles
from tests.test_backtest_engine import SPEC


class TestGenerateWalkForwardWindows:
    def test_windows_are_non_overlapping_and_chronological(self):
        df = make_trending_candles(n=1000)
        windows = list(generate_walk_forward_windows(df, train_bars=300, test_bars=100, mode="rolling"))
        assert len(windows) > 0
        for w in windows:
            assert w.train_end_time < w.test_start_time
            assert len(w.train_df) == 300
            assert len(w.test_df) == 100

    def test_successive_windows_move_forward_without_leaking_test_data_into_next_train(self):
        df = make_trending_candles(n=1000)
        windows = list(generate_walk_forward_windows(df, train_bars=300, test_bars=100, mode="rolling"))
        for a, b in zip(windows, windows[1:]):
            # Next window's train set must not extend past what was b's own boundary in a leaking way -
            # specifically, window b's test set never appears inside window a's train set.
            assert a.train_df.index.max() < b.test_df.index.min()

    def test_expanding_mode_grows_train_window_from_start(self):
        df = make_trending_candles(n=1000)
        windows = list(generate_walk_forward_windows(df, train_bars=300, test_bars=100, mode="expanding"))
        sizes = [len(w.train_df) for w in windows]
        assert sizes == sorted(sizes)  # non-decreasing
        assert all(w.train_df.index.min() == 0 for w in windows)

    def test_no_windows_when_data_too_short(self):
        df = make_trending_candles(n=50)
        windows = list(generate_walk_forward_windows(df, train_bars=300, test_bars=100))
        assert windows == []


class TestRunWalkForwardBacktest:
    def test_runs_across_all_windows_and_returns_metrics(self):
        df = make_trending_candles(n=1500)

        def strategy_factory(train_df):
            # Ignores train_df (fixed rule-based strategy) - the point
            # being tested is that windowing/backtesting glue works, not
            # that this particular strategy is profitable.
            return make_ema_crossover_strategy()

        featured = prepare_features(df)
        results = run_walk_forward_backtest(
            featured,
            strategy_factory,
            SPEC,
            train_bars=400,
            test_bars=200,
            config=BacktestConfig(initial_balance=10_000, min_risk_reward=1.5),
            warmup_bars=50,
        )
        assert len(results) > 0
        for r in results:
            assert r.metrics.total_trades >= 0
            assert r.backtest_result.final_balance > 0
