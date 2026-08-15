"""app/llm.py — the single model entry point.

Uses Groq when ``GROQ_API_KEY`` is configured, then Gemini when
``GEMINI_API_KEY`` is configured, otherwise Claude when ``ANTHROPIC_API_KEY``
is configured. All paths return ``None`` rather than raising so a live
walkthrough can fall back safely.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.parse
import urllib.request
from typing import Any

import structlog

log = structlog.get_logger(__name__)

MODEL = os.getenv("WA_MODEL", "claude-sonnet-4-6")
GEMINI_MODEL = os.getenv("WA_GEMINI_MODEL", "gemini-flash-latest")
GROQ_MODEL = os.getenv("WA_GROQ_MODEL", "llama-3.3-70b-versatile")
TIMEOUT_S = float(os.getenv("WA_LLM_TIMEOUT", "6"))

# Groq fronts its API with Cloudflare, which blocks Python's default
# urllib User-Agent as a bot signature (HTTP 403, error code 1010).
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def available() -> bool:
    """True when any configured model provider can be called."""
    return bool(os.getenv("GROQ_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("ANTHROPIC_API_KEY"))


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        t = t.rsplit("```", 1)[0]
    return t.strip()


def _groq_request(system: str, user: str, json_schema: dict | None,
                  max_tokens: int, key: str) -> Any:
    """Make one Groq (OpenAI-compatible) chat completion request; called in a worker thread."""
    body: dict[str, Any] = {
        "model": os.getenv("WA_GROQ_MODEL", GROQ_MODEL),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
    }
    if json_schema:
        body["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": _BROWSER_UA,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310 - fixed Groq endpoint
        payload = json.loads(response.read().decode("utf-8"))
    text = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not text:
        log.warning("groq_empty_text", finish_reason=payload.get("choices", [{}])[0].get("finish_reason"))
        return None
    return json.loads(_strip_fences(text)) if json_schema else text


async def _complete_groq(system: str, user: str, json_schema: dict | None,
                         max_tokens: int, key: str) -> Any:
    last: Exception | None = None
    for attempt in (1, 2):
        try:
            return await asyncio.to_thread(_groq_request, system, user, json_schema, max_tokens, key)
        except Exception as exc:  # noqa: BLE001 - provider boundary must not stop the demo
            last = exc
            log.warning("groq_call_failed", attempt=attempt, error=str(exc))
            if attempt == 1:
                await asyncio.sleep(1)
    log.error("groq_gave_up", error=str(last))
    return None


def _gemini_request(system: str, user: str, json_schema: dict | None,
                    max_tokens: int, key: str) -> Any:
    """Make one Gemini REST request; called in a worker thread."""
    # Newer "-latest" aliases can resolve to a reasoning model that spends the
    # output budget on hidden thinking tokens before writing anything, leaving
    # no room for the actual answer. These calls are short structured JSON
    # replies with no need for extended reasoning, so turn thinking off.
    config: dict[str, Any] = {"maxOutputTokens": max_tokens, "thinkingConfig": {"thinkingBudget": 0}}
    if json_schema:
        config["responseMimeType"] = "application/json"
        config["responseJsonSchema"] = json_schema
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": config,
    }
    model = os.getenv("WA_GEMINI_MODEL", GEMINI_MODEL)
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urllib.parse.quote(model, safe='')}:generateContent?key={urllib.parse.quote(key, safe='')}"
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310 - fixed Google endpoint
        payload = json.loads(response.read().decode("utf-8"))
    candidate = payload.get("candidates", [{}])[0]
    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    if not text:
        log.warning("gemini_empty_text", finish_reason=candidate.get("finishReason"))
        return None
    return json.loads(_strip_fences(text)) if json_schema else text


async def _complete_gemini(system: str, user: str, json_schema: dict | None,
                           max_tokens: int, key: str) -> Any:
    last: Exception | None = None
    for attempt in (1, 2):
        try:
            return await asyncio.to_thread(_gemini_request, system, user, json_schema, max_tokens, key)
        except Exception as exc:  # noqa: BLE001 - provider boundary must not stop the demo
            last = exc
            log.warning("gemini_call_failed", attempt=attempt, error=str(exc))
            if attempt == 1:
                await asyncio.sleep(1)
    log.error("gemini_gave_up", error=str(last))
    return None


async def complete(system: str, user: str, json_schema: dict | None = None,
                   max_tokens: int = 1024) -> Any:
    """Call Groq, Gemini, or Claude once, returning text/dict or ``None`` on failure.

    Groq is tried first when configured, then Gemini, then Claude, allowing a
    no-cost demo without changing any caller.
    """
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        return await _complete_groq(system, user, json_schema, max_tokens, groq_key)

    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        return await _complete_gemini(system, user, json_schema, max_tokens, gemini_key)

    if not os.getenv("ANTHROPIC_API_KEY"):
        log.warning("llm_unavailable_no_key")
        return None

    import anthropic  # lazy: Gemini-only and no-key runs do not require this SDK

    client = anthropic.AsyncAnthropic(timeout=TIMEOUT_S)
    suffix = "\n\nReply with JSON only. No prose, no code fences." if json_schema else ""
    last: Exception | None = None
    for attempt in (1, 2):
        try:
            msg = await client.messages.create(
                model=os.getenv("WA_MODEL", MODEL),
                max_tokens=max_tokens,
                system=system + suffix,
                messages=[{"role": "user", "content": user}],
            )
            text = "".join(block.text for block in msg.content if block.type == "text")
            return json.loads(_strip_fences(text)) if json_schema else text
        except Exception as exc:  # noqa: BLE001 - boundary: a demo must not die here
            last = exc
            log.warning("llm_call_failed", attempt=attempt, error=str(exc))
    log.error("llm_gave_up", error=str(last))
    return None
