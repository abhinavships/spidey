"""Groq provider behavior; no real key or network is used."""

from __future__ import annotations

import asyncio
import json

import app.llm as llm


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None


def test_groq_key_makes_llm_available(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "demo-key")
    assert llm.available()


def test_groq_is_tried_before_gemini_and_anthropic(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "demo-gemini-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "demo-anthropic-key")
    monkeypatch.setenv("GROQ_API_KEY", "demo-groq-key")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        return FakeResponse({"choices": [{"message": {"content": "plain text"}}]})

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    result = asyncio.run(llm.complete("system", "user"))

    assert result == "plain text"
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"


def test_groq_json_mode_returns_parsed_object(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "demo-key")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.data)
        return FakeResponse({"choices": [{"message": {"content": '{"kind":"question"}'}}]})

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    result = asyncio.run(llm.complete("system", "user", json_schema={"type": "object"}))

    assert result == {"kind": "question"}
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["headers"]["Authorization"] == "Bearer demo-key"


def test_groq_empty_content_returns_none(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "demo-key")

    def fake_urlopen(request, timeout):
        return FakeResponse({"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    result = asyncio.run(llm.complete("system", "user"))

    assert result is None
