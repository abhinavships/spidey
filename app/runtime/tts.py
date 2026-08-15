"""app/runtime/tts.py — server-side narration audio via the Gemini TTS API.

Reuses GEMINI_API_KEY (no separate key). Gemini's TTS endpoint returns raw
16-bit PCM with no container, so a minimal WAV header is added here before
the audio is base64-encoded and sent to the client — the browser can then
play it with a plain `new Audio(data:audio/wav;base64,...)`, no client-side
decoding needed.

Usage:
    tts = TTS()
    audio_b64 = await tts.synthesize("opening the issues list")  # or None
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
import urllib.parse
import urllib.request
from typing import Callable

import structlog

log = structlog.get_logger(__name__)

TTS_MODEL = os.getenv("WA_TTS_MODEL", "gemini-2.5-flash-preview-tts")
TTS_VOICE = os.getenv("WA_TTS_VOICE", "Kore")
TTS_TIMEOUT_S = float(os.getenv("WA_TTS_TIMEOUT_S", "8"))

_SAMPLE_RATE = 24000  # matches Gemini TTS's fixed output: audio/L16;codec=pcm;rate=24000
_SAMPLE_WIDTH = 2  # 16-bit
_CHANNELS = 1


def _tts_enabled() -> bool:
    return os.getenv("WA_TTS_ENABLED", "true").strip().lower() not in {"0", "false", "no"}


def _wav_bytes(pcm: bytes) -> bytes:
    """Wrap headerless 16-bit mono PCM in a minimal 44-byte WAV header."""
    byte_rate = _SAMPLE_RATE * _CHANNELS * _SAMPLE_WIDTH
    block_align = _CHANNELS * _SAMPLE_WIDTH
    header = (
        b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, _CHANNELS, _SAMPLE_RATE, byte_rate, block_align, _SAMPLE_WIDTH * 8)
        + b"data" + struct.pack("<I", len(pcm))
    )
    return header + pcm


def _request(text: str, key: str) -> str | None:
    """One synchronous Gemini TTS call; called in a worker thread. Returns base64 WAV or None."""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urllib.parse.quote(TTS_MODEL, safe='')}:generateContent?key={urllib.parse.quote(key, safe='')}"
    )
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": TTS_VOICE}}},
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TTS_TIMEOUT_S) as response:  # noqa: S310 - fixed Google endpoint
        payload = json.loads(response.read().decode("utf-8"))
    parts = payload.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    inline = next((p.get("inlineData") for p in parts if isinstance(p, dict) and p.get("inlineData")), None)
    if not inline or not inline.get("data"):
        log.warning("tts_empty_audio")
        return None
    pcm = base64.b64decode(inline["data"])
    return base64.b64encode(_wav_bytes(pcm)).decode("ascii")


class TTS:
    """Converts narration text to playable audio. Never raises, never blocks the run loop.

    `request` is injectable for tests: a callable `(text, key) -> str | None`
    run in a worker thread instead of making a real HTTP call.
    """

    def __init__(self, request: Callable[[str, str], str | None] | None = None) -> None:
        self._request = request or _request

    async def synthesize(self, text: str) -> str | None:
        """Base64 WAV audio for `text`, or None if disabled/unavailable/failed/timed out.

        Usage:
            audio_b64 = await tts.synthesize("opening the issues list")
        """
        if not text.strip() or not _tts_enabled():
            return None
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            return None
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._request, text, key), timeout=TTS_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - narration audio must never break the run
            log.warning("tts_failed", error=str(exc))
            return None
