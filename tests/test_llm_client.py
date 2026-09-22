from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

import app.llm.client as client_mod
from app.config import Settings
from app.llm.client import call_llm
from app.llm.context import MarketContext


def settings(**overrides) -> Settings:
    defaults = dict(_env_file=None, LLM_ENABLED=True, LLM_API_KEY="test-key", LLM_MODEL="gpt-4o-mini")
    defaults.update(overrides)
    return Settings(**defaults)


def fake_context() -> MarketContext:
    return MarketContext(
        symbol="EURUSD", timeframe="H1", timestamp="2024-01-01T00:00:00Z", trend="UPTREND", regime="STRONG_BULLISH_TREND",
        indicators={"rsi": 60.0}, market_structure={}, volatility={}, support_resistance={"support": 1.09, "resistance": 1.12},
        ml_probabilities=None, current_positions=[], risk_state={},
    )


def fake_openai_response(content: str):
    return SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"choices": [{"message": {"content": content}}]},
        text=content,
    )


class TestDisabledOrMisconfigured:
    def test_disabled_returns_wait_without_http_call(self, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(client_mod.httpx, "post", lambda *a, **kw: called.update(n=called["n"] + 1))
        result = call_llm(fake_context(), settings(LLM_ENABLED=False))
        assert result.decision == "WAIT"
        assert result.valid is False
        assert called["n"] == 0

    def test_missing_api_key_returns_wait_without_http_call(self, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(client_mod.httpx, "post", lambda *a, **kw: called.update(n=called["n"] + 1))
        result = call_llm(fake_context(), settings(LLM_API_KEY=None))
        assert result.decision == "WAIT"
        assert called["n"] == 0


class TestSuccessfulParsing:
    def test_valid_json_response_parsed_correctly(self, monkeypatch):
        content = '{"decision": "BUY", "confidence": 0.8, "reasoning": "Strong uptrend", "risk_flags": []}'
        monkeypatch.setattr(client_mod.httpx, "post", lambda *a, **kw: fake_openai_response(content))
        result = call_llm(fake_context(), settings())
        assert result.decision == "BUY"
        assert result.confidence == pytest.approx(0.8)
        assert result.reasoning == "Strong uptrend"
        assert result.valid is True

    def test_markdown_fenced_json_is_stripped(self, monkeypatch):
        content = '```json\n{"decision": "SELL", "confidence": 0.6, "reasoning": "Bearish", "risk_flags": ["high_volatility"]}\n```'
        monkeypatch.setattr(client_mod.httpx, "post", lambda *a, **kw: fake_openai_response(content))
        result = call_llm(fake_context(), settings())
        assert result.decision == "SELL"
        assert result.risk_flags == ["high_volatility"]
        assert result.valid is True


class TestValidationCoercion:
    def test_invalid_decision_defaults_to_wait(self, monkeypatch):
        content = '{"decision": "STRONG_BUY", "confidence": 0.9, "reasoning": "x", "risk_flags": []}'
        monkeypatch.setattr(client_mod.httpx, "post", lambda *a, **kw: fake_openai_response(content))
        result = call_llm(fake_context(), settings())
        assert result.decision == "WAIT"
        assert result.valid is False
        assert any("invalid decision" in e for e in result.validation_errors)

    def test_out_of_range_confidence_is_clamped(self, monkeypatch):
        content = '{"decision": "BUY", "confidence": 5.0, "reasoning": "x", "risk_flags": []}'
        monkeypatch.setattr(client_mod.httpx, "post", lambda *a, **kw: fake_openai_response(content))
        result = call_llm(fake_context(), settings())
        assert result.confidence == 1.0
        assert result.valid is False

    def test_non_numeric_confidence_defaults_to_zero(self, monkeypatch):
        content = '{"decision": "BUY", "confidence": "very high", "reasoning": "x", "risk_flags": []}'
        monkeypatch.setattr(client_mod.httpx, "post", lambda *a, **kw: fake_openai_response(content))
        result = call_llm(fake_context(), settings())
        assert result.confidence == 0.0
        assert result.valid is False

    def test_non_list_risk_flags_discarded(self, monkeypatch):
        content = '{"decision": "BUY", "confidence": 0.7, "reasoning": "x", "risk_flags": "not_a_list"}'
        monkeypatch.setattr(client_mod.httpx, "post", lambda *a, **kw: fake_openai_response(content))
        result = call_llm(fake_context(), settings())
        assert result.risk_flags == []
        assert result.valid is False

    def test_malformed_json_returns_wait(self, monkeypatch):
        monkeypatch.setattr(client_mod.httpx, "post", lambda *a, **kw: fake_openai_response("not json at all"))
        result = call_llm(fake_context(), settings())
        assert result.decision == "WAIT"
        assert result.valid is False
        assert "INVALID_JSON" in result.risk_flags

    def test_non_object_json_returns_wait(self, monkeypatch):
        monkeypatch.setattr(client_mod.httpx, "post", lambda *a, **kw: fake_openai_response("[1, 2, 3]"))
        result = call_llm(fake_context(), settings())
        assert result.decision == "WAIT"
        assert result.valid is False


class TestNetworkFailures:
    def test_http_error_returns_wait(self, monkeypatch):
        def raise_error(*a, **kw):
            raise httpx.ConnectTimeout("timed out")

        monkeypatch.setattr(client_mod.httpx, "post", raise_error)
        result = call_llm(fake_context(), settings())
        assert result.decision == "WAIT"
        assert result.valid is False
        assert "LLM_REQUEST_FAILED" in result.risk_flags

    def test_unexpected_response_shape_returns_wait(self, monkeypatch):
        monkeypatch.setattr(
            client_mod.httpx, "post",
            lambda *a, **kw: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"unexpected": "shape"}, text="{}"),
        )
        result = call_llm(fake_context(), settings())
        assert result.decision == "WAIT"
        assert "INVALID_RESPONSE_SHAPE" in result.risk_flags


class TestRequestShape:
    def test_sends_context_as_user_message_and_auth_header(self, monkeypatch):
        captured = {}

        def fake_post(url, headers, json, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return fake_openai_response('{"decision": "WAIT", "confidence": 0.5, "reasoning": "x", "risk_flags": []}')

        monkeypatch.setattr(client_mod.httpx, "post", fake_post)
        ctx = fake_context()
        call_llm(ctx, settings(LLM_API_KEY="secret-key", LLM_MODEL="my-model"))

        assert captured["headers"]["Authorization"] == "Bearer secret-key"
        assert captured["json"]["model"] == "my-model"
        assert "EURUSD" in captured["json"]["messages"][1]["content"]
