"""app/runtime/tts.py behavior; no real key or network is used."""

from __future__ import annotations

import asyncio
import base64
import time
import wave
from io import BytesIO

import app.runtime.tts as tts


def test_disabled_returns_none_without_calling_request(monkeypatch):
    monkeypatch.setenv("WA_TTS_ENABLED", "false")
    monkeypatch.setenv("GEMINI_API_KEY", "demo-key")
    called = []

    def fake_request(text, key):
        called.append(text)
        return "audio"

    result = asyncio.run(tts.TTS(request=fake_request).synthesize("hello"))
    assert result is None
    assert called == []


def test_missing_api_key_returns_none(monkeypatch):
    monkeypatch.setenv("WA_TTS_ENABLED", "true")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    called = []

    def fake_request(text, key):
        called.append(text)
        return "audio"

    result = asyncio.run(tts.TTS(request=fake_request).synthesize("hello"))
    assert result is None
    assert called == []


def test_empty_text_returns_none(monkeypatch):
    monkeypatch.setenv("WA_TTS_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "demo-key")
    result = asyncio.run(tts.TTS(request=lambda t, k: "audio").synthesize("   "))
    assert result is None


def test_successful_call_returns_the_request_result(monkeypatch):
    monkeypatch.setenv("WA_TTS_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "demo-key")
    captured = {}

    def fake_request(text, key):
        captured["text"] = text
        captured["key"] = key
        return "base64-wav-data"

    result = asyncio.run(tts.TTS(request=fake_request).synthesize("opening the issues list"))
    assert result == "base64-wav-data"
    assert captured == {"text": "opening the issues list", "key": "demo-key"}


def test_request_exception_falls_back_to_none(monkeypatch):
    monkeypatch.setenv("WA_TTS_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "demo-key")

    def boom(text, key):
        raise RuntimeError("network down")

    result = asyncio.run(tts.TTS(request=boom).synthesize("hello"))
    assert result is None


def test_slow_request_times_out_and_falls_back_to_none(monkeypatch):
    monkeypatch.setenv("WA_TTS_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "demo-key")
    monkeypatch.setattr(tts, "TTS_TIMEOUT_S", 0.05)

    def slow(text, key):
        time.sleep(0.3)
        return "too-late"

    result = asyncio.run(tts.TTS(request=slow).synthesize("hello"))
    assert result is None


def test_wav_bytes_produce_a_valid_wav_file():
    pcm = (b"\x01\x00\x02\x00") * 100  # 200 frames of 16-bit mono silence-ish samples
    wav = tts._wav_bytes(pcm)
    with wave.open(BytesIO(wav), "rb") as f:
        assert f.getnchannels() == 1
        assert f.getsampwidth() == 2
        assert f.getframerate() == 24000
        assert f.getnframes() == 200
        assert f.readframes(200) == pcm


def test_request_wraps_inline_audio_data_in_a_wav(monkeypatch):
    """Exercises the real _request parsing logic against a fake HTTP response."""
    pcm = b"\x00\x01" * 50
    fake_payload = {
        "candidates": [{"content": {"parts": [
            {"inlineData": {"mimeType": "audio/L16;codec=pcm;rate=24000",
                            "data": base64.b64encode(pcm).decode("ascii")}}
        ]}}]
    }

    class FakeResponse:
        def read(self):
            import json
            return json.dumps(fake_payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    monkeypatch.setattr(tts.urllib.request, "urlopen", lambda req, timeout: FakeResponse())
    result = tts._request("hello", "demo-key")
    assert result is not None
    decoded_wav = base64.b64decode(result)
    with wave.open(BytesIO(decoded_wav), "rb") as f:
        assert f.readframes(f.getnframes()) == pcm


def test_request_returns_none_when_no_inline_audio(monkeypatch):
    fake_payload = {"candidates": [{"content": {"parts": [{"text": "no audio here"}]}}]}

    class FakeResponse:
        def read(self):
            import json
            return json.dumps(fake_payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    monkeypatch.setattr(tts.urllib.request, "urlopen", lambda req, timeout: FakeResponse())
    assert tts._request("hello", "demo-key") is None
