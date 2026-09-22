from __future__ import annotations

import pytest

from app.ai.confidence import ConfidenceThresholds, decide


class TestDecide:
    def test_clear_buy_signal(self):
        probs = {"BUY": 0.75, "NEUTRAL": 0.15, "SELL": 0.10}
        d = decide(probs)
        assert d.action == "BUY"
        assert d.confidence == pytest.approx(0.75)

    def test_clear_sell_signal(self):
        probs = {"BUY": 0.05, "NEUTRAL": 0.15, "SELL": 0.80}
        d = decide(probs)
        assert d.action == "SELL"

    def test_neutral_highest_forces_wait(self):
        probs = {"BUY": 0.30, "NEUTRAL": 0.50, "SELL": 0.20}
        d = decide(probs)
        assert d.action == "WAIT"

    def test_low_confidence_forces_wait(self):
        probs = {"BUY": 0.40, "NEUTRAL": 0.35, "SELL": 0.25}
        d = decide(probs, ConfidenceThresholds(min_confidence=0.55))
        assert d.action == "WAIT"

    def test_narrow_margin_forces_wait(self):
        # BUY clears min_confidence on its own, but is only barely ahead
        # of NEUTRAL - too close to call, so this must still be WAIT.
        probs = {"BUY": 0.56, "NEUTRAL": 0.50, "SELL": 0.00}
        d = decide(probs, ConfidenceThresholds(min_confidence=0.55, min_margin=0.10))
        assert d.action == "WAIT"
        assert "margin" in d.reason.lower()

    def test_missing_class_raises(self):
        with pytest.raises(ValueError):
            decide({"BUY": 0.6, "NEUTRAL": 0.4})
