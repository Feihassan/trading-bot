from __future__ import annotations

from app.llm.client import LLMAnalysis
from app.llm.overlay import apply_llm_overlay
from app.strategy.signals import TradeIdea


def make_idea(**overrides) -> TradeIdea:
    defaults = dict(
        symbol="EURUSD", timestamp=None, direction="BUY", confidence=70.0,
        entry=1.1000, stop_loss=1.0950, take_profit=1.1100, reward_risk=2.0,
        technical_score=80.0, technical_direction="BUY", structure_trend="UPTREND",
        regime="STRONG_BULLISH_TREND", ml_probabilities=None, ml_direction=None,
        spread_points=5.0, reasons=["engine reason"], blocking_reasons=[],
    )
    defaults.update(overrides)
    return TradeIdea(**defaults)


def make_llm(**overrides) -> LLMAnalysis:
    defaults = dict(decision="BUY", confidence=0.8, reasoning="Agrees with trend", risk_flags=[], valid=True, validation_errors=[])
    defaults.update(overrides)
    return LLMAnalysis(**defaults)


class TestCannotCreateOrUpgradeTrades:
    def test_llm_cannot_turn_a_wait_into_a_buy(self):
        idea = make_idea(direction="WAIT")
        llm = make_llm(decision="BUY", confidence=0.99)
        result = apply_llm_overlay(idea, llm)
        assert result.direction == "WAIT"

    def test_llm_cannot_turn_a_wait_into_a_sell(self):
        idea = make_idea(direction="WAIT")
        llm = make_llm(decision="SELL", confidence=0.99)
        result = apply_llm_overlay(idea, llm)
        assert result.direction == "WAIT"

    def test_direction_never_becomes_buy_or_sell_from_llm_alone(self):
        # Exhaustive-ish check: no combination of LLM output can produce
        # a directional result when the engine itself said WAIT.
        for decision in ("BUY", "SELL", "WAIT"):
            for valid in (True, False):
                idea = make_idea(direction="WAIT")
                llm = make_llm(decision=decision, valid=valid, validation_errors=[] if valid else ["x"])
                result = apply_llm_overlay(idea, llm)
                assert result.direction == "WAIT"


class TestAgreementAndDisagreement:
    def test_agreement_preserves_direction(self):
        idea = make_idea(direction="BUY")
        llm = make_llm(decision="BUY")
        result = apply_llm_overlay(idea, llm)
        assert result.direction == "BUY"
        assert result.entry == idea.entry
        assert result.stop_loss == idea.stop_loss

    def test_disagreement_downgrades_to_wait(self):
        idea = make_idea(direction="BUY")
        llm = make_llm(decision="SELL")
        result = apply_llm_overlay(idea, llm)
        assert result.direction == "WAIT"
        assert any("disagrees" in b.lower() for b in result.blocking_reasons)

    def test_llm_wait_downgrades_engine_buy(self):
        idea = make_idea(direction="BUY")
        llm = make_llm(decision="WAIT")
        result = apply_llm_overlay(idea, llm)
        assert result.direction == "WAIT"

    def test_agreement_with_risk_flags_still_downgrades(self):
        idea = make_idea(direction="BUY")
        llm = make_llm(decision="BUY", risk_flags=["near_key_resistance"])
        result = apply_llm_overlay(idea, llm)
        assert result.direction == "WAIT"
        assert any("risk flag" in b.lower() for b in result.blocking_reasons)


class TestInvalidLlmDoesNotPunishGoodSignal:
    def test_invalid_llm_response_leaves_direction_unchanged(self):
        idea = make_idea(direction="BUY")
        llm = LLMAnalysis(decision="WAIT", confidence=0.0, reasoning="", risk_flags=[], valid=False, validation_errors=["LLM_ENABLED is false"])
        result = apply_llm_overlay(idea, llm)
        assert result.direction == "BUY"
        assert any("unavailable" in r.lower() for r in result.reasons)


class TestNeverTouchesSizingOrStops:
    def test_entry_sl_tp_reward_risk_never_modified(self):
        idea = make_idea(direction="BUY", entry=1.234, stop_loss=1.2, take_profit=1.3, reward_risk=2.0)
        for llm in (make_llm(decision="BUY"), make_llm(decision="SELL"), make_llm(decision="WAIT")):
            result = apply_llm_overlay(idea, llm)
            assert result.entry == 1.234
            assert result.stop_loss == 1.2
            assert result.take_profit == 1.3
            assert result.reward_risk == 2.0


class TestTransparencyFields:
    def test_llm_fields_populated_on_result(self):
        idea = make_idea(direction="BUY")
        llm = make_llm(decision="BUY", confidence=0.85, reasoning="Clear uptrend", risk_flags=["x"])
        result = apply_llm_overlay(idea, llm)
        assert result.llm_decision == "BUY"
        assert result.llm_confidence == 0.85
        assert result.llm_reasoning == "Clear uptrend"
        assert result.llm_risk_flags == ["x"]
