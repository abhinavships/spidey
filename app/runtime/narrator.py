"""app/runtime/narrator.py — M6. Speaks about what is actually on screen.

With an API key it narrates from the live snapshot. Without one it still
grounds in the snapshot (title / nearby text) rather than reading the hint
verbatim, so the demo never sounds canned.

Usage:
    narrator = Narrator()
    line = await narrator.narrate(step, snap)
"""

from __future__ import annotations

import asyncio
import re

import structlog

from app.llm import complete
from app.schemas import PageSnapshot, Step

log = structlog.get_logger(__name__)

MAX_CHARS = 300
TIMEOUT_S = 6.0

SYSTEM = (
    "You are an agent walking a customer through a website live, speaking aloud. "
    "One or two short sentences, first person, present tense. No markdown, no "
    "emoji, no lists. Mention something you can actually see on the current page. "
    "Under 40 words."
)


def _trim(text: str, limit: int = MAX_CHARS) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if stop > 60:
        return cut[: stop + 1]
    return cut[: cut.rfind(" ")] + "..."


class Narrator:
    """Turns a step plus a live snapshot into one spoken line."""

    def __init__(self, llm=complete) -> None:
        self._llm = llm

    async def narrate(self, step: Step, snap: PageSnapshot) -> str:
        """One spoken line about this step. Never raises, never empty.

        Usage:
            await narrator.narrate(step, snap)
        """
        try:
            out = await asyncio.wait_for(self._ask(step, snap), timeout=TIMEOUT_S)
            if out and out.strip():
                return _trim(out)
        except Exception as exc:  # noqa: BLE001 - narration must never stall the demo
            log.warning("narration_failed", step_id=step.id, error=str(exc))
        return _trim(self._local(step, snap))

    async def resume_line(self, step: Step, cursor: int, total: int) -> str:
        """"back to it — step 3 of 5, ...". Templated; latency beats variety.

        Usage:
            await narrator.resume_line(step, 2, 5)
        """
        return _trim(f"Back to it — step {cursor + 1} of {total}, {step.intent}.")

    async def _ask(self, step: Step, snap: PageSnapshot) -> str | None:
        user = (f"Page title: {snap.title}\nURL: {snap.url}\n"
                f"Visible text (excerpt):\n{snap.visible_text[:1200]}\n\n"
                f"What I am about to do: {step.intent}\n"
                f"Rough note to myself: {step.narration_hint}\n\n"
                "Say it out loud to the customer.")
        return await self._llm(SYSTEM, user, max_tokens=150)

    # Site chrome that shows up first in visible_text on nearly every page and
    # says nothing about what's actually happening in this step.
    _BOILERPLATE = (
        "skip to content", "skip to main content", "sign in", "sign up",
        "log in", "cookie", "accept all", "notifications", "menu", "search",
    )

    @classmethod
    def _local(cls, step: Step, snap: PageSnapshot) -> str:
        """Snapshot-grounded fallback. Still differs when the page differs."""
        base = step.narration_hint or step.intent
        anchor = snap.title.split("·")[0].strip() or snap.url
        words = [w.strip() for w in re.findall(r"[A-Za-z][A-Za-z' ]{3,30}", snap.visible_text)]
        detail = next((w for w in words if w.lower() not in cls._BOILERPLATE), "")
        if detail and detail.lower() not in base.lower():
            return f"{base.capitalize()} — I'm on {anchor}, and I can see \"{detail}\"."
        return f"{base.capitalize()} — I'm on {anchor} now."
