"""LLM client abstraction with OpenAI-compatible (llama.cpp), Ollama, and mock support."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class LLMResponse:
    text: str
    model: str
    usage: dict[str, int] | None = None

    def as_json(self) -> Any:
        text = self.text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return json.loads(text.strip())


class LLMClient(ABC):
    @abstractmethod
    def generate(self, prompt: str, system: str = "", temperature: float = 0.3) -> LLMResponse:
        ...


class LlamaCppClient(LLMClient):
    """Client for llama.cpp server with OpenAI-compatible /v1/chat/completions API."""

    def __init__(
        self,
        base_url: str = "http://spark.local:30000",
        model: str = "Nemotron-3-Nano-30B-A3B-UD-Q8_K_XL.gguf",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def generate(self, prompt: str, system: str = "", temperature: float = 0.3) -> LLMResponse:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }

        resp = httpx.post(
            f"{self.base_url}/v1/chat/completions",
            json=payload,
            timeout=300.0,
        )
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        usage = data.get("usage", {})
        return LLMResponse(
            text=choice["message"]["content"],
            model=self.model,
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
            },
        )


class OllamaClient(LLMClient):
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "nemotron") -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def generate(self, prompt: str, system: str = "", temperature: float = 0.3) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if system:
            payload["system"] = system

        resp = httpx.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return LLMResponse(
            text=data.get("response", ""),
            model=self.model,
            usage={
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
            },
        )


def probe_endpoint(base_url: str, backend_type: str = "llamacpp") -> dict:
    """Probe an LLM endpoint and return available models and status."""
    base_url = base_url.rstrip("/")
    result: dict = {"reachable": False, "models": [], "error": None}

    try:
        if backend_type == "ollama":
            resp = httpx.get(f"{base_url}/api/tags", timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
            result["reachable"] = True
            result["models"] = [
                m.get("name", m.get("model", "unknown"))
                for m in data.get("models", [])
            ]
        else:
            # OpenAI-compatible (llama.cpp, vLLM, etc.)
            resp = httpx.get(f"{base_url}/v1/models", timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
            result["reachable"] = True
            models = data.get("data", [])
            if not models:
                models = data.get("models", [])
            result["models"] = [
                m.get("id", m.get("name", "unknown"))
                for m in models
            ]
    except Exception as e:
        result["error"] = str(e)

    return result


def create_client_from_config(config) -> LLMClient:
    """Create an LLMClient from an LLMConfig dataclass."""
    if config.backend_type == "ollama":
        return OllamaClient(base_url=config.base_url, model=config.model)
    return LlamaCppClient(base_url=config.base_url, model=config.model)


class MockLLMClient(LLMClient):
    """Deterministic mock client for testing. Returns canned JSON responses."""

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self._responses = responses or {}
        self._call_log: list[dict[str, str]] = []

    def generate(self, prompt: str, system: str = "", temperature: float = 0.3) -> LLMResponse:
        self._call_log.append({"prompt": prompt, "system": system})

        for keyword, response in self._responses.items():
            if keyword.lower() in prompt.lower():
                return LLMResponse(text=response, model="mock")

        return LLMResponse(text=self._default_response(prompt), model="mock")

    def _default_response(self, prompt: str) -> str:
        if "summar" in prompt.lower():
            return json.dumps({
                "title": "Test Document",
                "summary": "A test document about important topics.",
                "key_ideas": ["Key idea 1", "Key idea 2"],
                "entities": [
                    {"name": "TestEntity", "type": "concept", "aliases": []}
                ],
                "tags": ["test", "document"],
                "related_concepts": ["Related Concept A"],
                "open_questions": ["What is the next step?"],
            })
        if "backlink" in prompt.lower() or "link" in prompt.lower():
            return json.dumps({
                "links": [
                    {"target": "Related Concept A", "reason": "Directly related", "confidence": 0.9}
                ]
            })
        if "answer" in prompt.lower() or "question" in prompt.lower():
            return json.dumps({
                "answer": "Based on the vault notes, this is the answer.",
                "confidence": "high",
                "sources": ["note-1"],
                "answer_type": "synthesized",
            })
        return json.dumps({"result": "ok"})
