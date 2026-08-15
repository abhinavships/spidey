"""Gemini provider behavior; no real key or network is used."""

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


def test_gemini_key_makes_llm_available(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "demo-key")
    assert llm.available()


def test_gemini_json_mode_returns_parsed_object(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "demo-key")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        return FakeResponse({"candidates": [{"content": {"parts": [{"text": '{"kind":"question"}'}]}}]})

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    result = asyncio.run(llm.complete("system", "user", json_schema={"type": "object"}))

    assert result == {"kind": "question"}
    assert "generateContent?key=demo-key" in captured["url"]
    assert captured["body"]["generationConfig"]["responseMimeType"] == "application/json"
