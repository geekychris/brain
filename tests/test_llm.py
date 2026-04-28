"""Tests for the LLM client abstraction."""

import json

import pytest

from secondbrain.llm.client import MockLLMClient, LLMResponse


class TestLLMResponse:
    def test_as_json_plain(self):
        resp = LLMResponse(text='{"key": "value"}', model="mock")
        assert resp.as_json() == {"key": "value"}

    def test_as_json_with_code_fence(self):
        resp = LLMResponse(text='```json\n{"key": "value"}\n```', model="mock")
        assert resp.as_json() == {"key": "value"}

    def test_as_json_invalid(self):
        resp = LLMResponse(text="not json at all", model="mock")
        with pytest.raises(json.JSONDecodeError):
            resp.as_json()


class TestMockLLMClient:
    def test_default_summarize_response(self):
        client = MockLLMClient()
        resp = client.generate("Please summarize this document.")
        data = resp.as_json()
        assert "title" in data
        assert "summary" in data
        assert "key_ideas" in data

    def test_default_link_response(self):
        client = MockLLMClient()
        resp = client.generate("Propose backlinks for this note.")
        data = resp.as_json()
        assert "links" in data

    def test_default_answer_response(self):
        client = MockLLMClient()
        resp = client.generate("Answer this question from the vault.")
        data = resp.as_json()
        assert "answer" in data

    def test_custom_response(self):
        client = MockLLMClient(responses={
            "kafka": '{"topic": "kafka", "details": "distributed log"}'
        })
        resp = client.generate("Tell me about Kafka.")
        data = resp.as_json()
        assert data["topic"] == "kafka"

    def test_call_log(self):
        client = MockLLMClient()
        client.generate("First call")
        client.generate("Second call")
        assert len(client._call_log) == 2
        assert "First call" in client._call_log[0]["prompt"]

    def test_model_name(self):
        client = MockLLMClient()
        resp = client.generate("test")
        assert resp.model == "mock"
