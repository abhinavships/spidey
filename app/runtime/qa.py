"""Grounded question answering for a live walkthrough."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from app.llm import complete
from app.schemas import Answer, PageSnapshot, WorkflowSpec

ANSWER_TIMEOUT_S = 4.0
FALLBACK = "That request is out of scope for this demo and wasn't part of what I was shown, so I can't say."


class QA:
    """Answers only from rehearsal knowledge and the current page snapshot."""

    def __init__(self, llm: Callable[..., Awaitable[Any]] | None = None) -> None:
        self._llm = llm or complete

    async def answer(self, question: str, spec: WorkflowSpec, cursor: int, live: PageSnapshot) -> Answer:
        evidence: list[dict[str, str]] = []
        valid_sources = {"live_page"}
        for step in spec.steps:
            if step.knowledge is None:
                continue
            valid_sources.add(step.id)
            evidence.append({
                "step_id": step.id,
                "page_title": step.knowledge.page_title,
                "visible_text": step.knowledge.visible_text,
                "observed_effect": step.knowledge.observed_effect,
            })
        live_evidence = {
            "page_title": live.title,
            "visible_text": live.visible_text,
            "a11y_digest": live.a11y_digest,
        }
        system = """Answer a walkthrough customer's question using only the supplied evidence.
Do not use world knowledge or infer unstated facts. If the answer is not explicitly
supported, say it is outside the scope of this demo, that you were not shown it, and
that you cannot say. Return JSON only:
text (spoken, no markdown, at most 500 characters), grounded (boolean), sources
(an array containing only supplied step ids and/or live_page). A grounded answer
must cite at least one source."""
        user = json.dumps({
            "question": question,
            "current_cursor": cursor,
            "step_knowledge": evidence,
            "live_page": live_evidence,
        })
        schema = {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "grounded": {"type": "boolean"},
                "sources": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["text", "grounded", "sources"],
        }
        try:
            raw = await asyncio.wait_for(self._llm(system, user, json_schema=schema, max_tokens=220), ANSWER_TIMEOUT_S)
            if isinstance(raw, str):
                raw = json.loads(raw)
            if not isinstance(raw, dict):
                return Answer(text=FALLBACK, grounded=False, sources=[])
            answer = Answer.model_validate(raw)
            if not answer.text.strip() or len(answer.text) > 500:
                return Answer(text=FALLBACK, grounded=False, sources=[])
            if any(source not in valid_sources for source in answer.sources):
                return Answer(text=FALLBACK, grounded=False, sources=[])
            if answer.grounded and not answer.sources:
                return Answer(text=FALLBACK, grounded=False, sources=[])
            if not answer.grounded:
                return Answer(text=FALLBACK, grounded=False, sources=[])
            return answer
        except Exception:  # answering must never interrupt a live walkthrough
            return Answer(text=FALLBACK, grounded=False, sources=[])
