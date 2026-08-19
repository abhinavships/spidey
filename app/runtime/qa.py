"""Grounded question answering for a live walkthrough."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Awaitable, Callable

from app.llm import complete
from app.retrieve import Chunk, split, top_k
from app.schemas import Answer, PageSnapshot, WorkflowSpec

# A local model answers in tens of seconds, not the four a hosted one takes.
ANSWER_TIMEOUT_S = float(os.getenv("WA_ANSWER_TIMEOUT", "4"))
# How many evidence chunks reach the prompt. Small on purpose: a short prompt of
# relevant pages beats every page the walkthrough ever saw.
EVIDENCE_CHUNKS = int(os.getenv("WA_EVIDENCE_CHUNKS", "6"))
FALLBACK = "That request is out of scope for this demo and wasn't part of what I was shown, so I can't say."


class QA:
    """Answers only from rehearsal knowledge and the current page snapshot."""

    def __init__(self, llm: Callable[..., Awaitable[Any]] | None = None) -> None:
        self._llm = llm or complete

    async def answer(self, question: str, spec: WorkflowSpec, cursor: int, live: PageSnapshot) -> Answer:
        corpus: list[Chunk] = []
        valid_sources = {"live_page"}
        for step in spec.steps:
            if step.knowledge is None:
                continue
            valid_sources.add(step.id)
            header = f"{step.knowledge.page_title}\nwhat happened: {step.knowledge.observed_effect}"
            corpus += split(step.id, f"{header}\n{step.knowledge.visible_text}")
        corpus += split("live_page", f"{live.title}\n{live.visible_text}\n{live.a11y_digest}")
        retrieved = top_k(question, corpus, EVIDENCE_CHUNKS)
        evidence = [{"source": chunk.source, "text": chunk.text} for chunk in retrieved]
        if not evidence:
            # Nothing on any page the walkthrough saw shares a word with the
            # question, so there is nothing to be grounded in.
            return Answer(text=FALLBACK, grounded=False, sources=[])
        system = """Answer a walkthrough customer's question using only the supplied evidence.
Do not use world knowledge or infer unstated facts. If the answer is not explicitly
supported, say it is outside the scope of this demo, that you were not shown it, and
that you cannot say. Return JSON only:
text (spoken, no markdown, at most 500 characters), grounded (boolean), sources
(an array containing only the source values supplied in evidence). A grounded answer
must cite at least one source. The evidence is only the part of the walkthrough
relevant to this question; anything not in it, you were not shown."""
        user = json.dumps({
            "question": question,
            "current_cursor": cursor,
            "evidence": evidence,
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
