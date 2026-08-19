"""Live rehearsal: resolve a draft against the browser and freeze a WorkflowSpec."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Awaitable, Callable

from app.schemas import (
    Event,
    PageSnapshot,
    Step,
    StepKnowledge,
    WorkflowSpec,
)


# How many times one step may be re-described before teaching gives up on it.
MAX_CLARIFICATIONS = 2

# Answers that mean "this step does not exist on this page, move on". A draft
# compiled from one sentence often plans a navigation the site does not need,
# and dropping it is the honest correction — not another attempt to click it.
_SKIP_REPLIES = ("skip", "not needed", "no need", "already there", "already on",
                 "drop it", "drop this", "leave it out", "not required")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(text: str) -> str:
    """A url-safe, human-readable identifier from a title or an intent."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:42] or "step"


def _step_id(index: int, intent: str) -> str:
    return f"{index + 1}-{_slug(intent)}"


def _knowledge(snapshot: PageSnapshot, effect: str) -> StepKnowledge:
    return StepKnowledge(page_url=snapshot.url, page_title=snapshot.title,
                         a11y_digest=snapshot.a11y_digest, visible_text=snapshot.visible_text,
                         observed_effect=effect or "the action completed", captured_at=_now())


async def rehearse(draft, driver, resolver, guard, emit,
                   ask: Callable[[str], Awaitable[str | None]] | None = None) -> WorkflowSpec:
    """Drive every draft step live and return a reusable frozen workflow.

    When a step will not resolve, ``ask`` is given a plain-language question and
    its answer replaces that step's intent for another try, so one badly worded
    sentence costs a retry instead of the whole lesson. Without an ``ask`` (or
    once the retries run out) the failure is reported as ``teach_question`` and
    an unsaved ``rehearsal_passed=False`` spec is returned.
    """
    steps: list[Step] = []

    def _unfinished() -> WorkflowSpec:
        return WorkflowSpec(id=_slug(draft.title), title=draft.title,
                            target_domain=draft.target_domain, entry_url=draft.entry_url,
                            steps=steps, taught_at=_now(), rehearsal_passed=False,
                            source_utterance="")

    for index, draft_step in enumerate(draft.steps):
        await emit(Event(type="teach_progress", session_id="teach", ts=_now(),
                         text=f"Learning step {index + 1} of {len(draft.steps)}: {draft_step.intent}",
                         cursor=index))
        intent = draft_step.intent
        for attempt in range(MAX_CLARIFICATIONS + 1):
            before = await driver.snapshot()
            locator = None
            if draft_step.action.value not in {"navigate", "scroll"}:
                locator = await resolver.resolve(intent, before)
            step = Step(id=_step_id(index, intent), index=index,
                        action=draft_step.action, intent=intent, locator=locator,
                        value=draft_step.value, narration_hint=draft_step.narration_hint,
                        risk=draft_step.risk)
            result = await driver.act(step, resolver, guard)
            if result.ok:
                break
            reason = result.error or "the page changed"
            question = (f"I could not learn '{intent}': {reason}. "
                        "Describe what I should click or type instead.")
            reply = await ask(question) if ask and attempt < MAX_CLARIFICATIONS else None
            if not reply or not reply.strip():
                await emit(Event(type="teach_question", session_id="teach", ts=_now(),
                                 text=f"I could not learn '{intent}': {reason}. Please clarify that step.",
                                 step_id=step.id, cursor=index))
                return _unfinished()
            reply = reply.strip()
            if any(phrase in reply.lower() for phrase in _SKIP_REPLIES):
                await emit(Event(type="teach_progress", session_id="teach", ts=_now(),
                                 text=f"Dropping step {index + 1}: {intent}", cursor=index))
                step = None
                break
            intent = reply
            await emit(Event(type="teach_progress", session_id="teach", ts=_now(),
                             text=f"Retrying step {index + 1} as: {intent}", cursor=index))
        if step is None:  # the human said this step is not on the page
            continue
        after = await driver.snapshot()
        # index is the step's position in the finished workflow, not in the draft:
        # dropped and retried steps must not leave a hole for the cursor to fall in.
        position = len(steps)
        steps.append(step.model_copy(update={
            "id": _step_id(position, step.intent), "index": position,
            "knowledge": _knowledge(after, result.effect)}))
    spec = WorkflowSpec(id=_slug(draft.title), title=draft.title,
                        target_domain=draft.target_domain, entry_url=draft.entry_url,
                        steps=steps, taught_at=_now(), rehearsal_passed=True,
                        source_utterance="")
    await emit(Event(type="teach_complete", session_id="teach", ts=_now(),
                     text=f"Learned {len(steps)} steps and saved the reusable workflow.",
                     cursor=0))
    return spec
