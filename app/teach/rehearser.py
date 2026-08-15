"""Live rehearsal: resolve a draft against the browser and freeze a WorkflowSpec."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.schemas import (
    Event,
    PageSnapshot,
    Step,
    StepKnowledge,
    WorkflowSpec,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _step_id(index: int, intent: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", intent.lower()).strip("-")[:42] or "step"
    return f"{index + 1}-{slug}"


def _knowledge(snapshot: PageSnapshot, effect: str) -> StepKnowledge:
    return StepKnowledge(page_url=snapshot.url, page_title=snapshot.title,
                         a11y_digest=snapshot.a11y_digest, visible_text=snapshot.visible_text,
                         observed_effect=effect or "the action completed", captured_at=_now())


async def rehearse(draft, driver, resolver, guard, emit) -> WorkflowSpec:
    """Drive every draft step live and return a reusable frozen workflow.

    A failed resolution is reported as ``teach_question`` and returns an unsaved,
    ``rehearsal_passed=False`` spec so the caller can request clarification.
    """
    steps: list[Step] = []
    for index, draft_step in enumerate(draft.steps):
        await emit(Event(type="teach_progress", session_id="teach", ts=_now(),
                         text=f"Learning step {index + 1} of {len(draft.steps)}: {draft_step.intent}",
                         cursor=index))
        before = await driver.snapshot()
        locator = None
        if draft_step.action.value not in {"navigate", "scroll"}:
            locator = await resolver.resolve(draft_step.intent, before)
        step = Step(id=_step_id(index, draft_step.intent), index=index,
                    action=draft_step.action, intent=draft_step.intent, locator=locator,
                    value=draft_step.value, narration_hint=draft_step.narration_hint,
                    risk=draft_step.risk)
        result = await driver.act(step, resolver, guard)
        after = await driver.snapshot()
        if not result.ok:
            await emit(Event(type="teach_question", session_id="teach", ts=_now(),
                             text=f"I could not learn '{draft_step.intent}': {result.error or 'the page changed'}. Please clarify that step.",
                             step_id=step.id, cursor=index))
            return WorkflowSpec(id=_step_id(0, draft.title), title=draft.title,
                                target_domain=draft.target_domain, entry_url=draft.entry_url,
                                steps=steps, taught_at=_now(), rehearsal_passed=False,
                                source_utterance="")
        steps.append(step.model_copy(update={"knowledge": _knowledge(after, result.effect)}))
    spec = WorkflowSpec(id=_step_id(0, draft.title), title=draft.title,
                        target_domain=draft.target_domain, entry_url=draft.entry_url,
                        steps=steps, taught_at=_now(), rehearsal_passed=True,
                        source_utterance="")
    await emit(Event(type="teach_complete", session_id="teach", ts=_now(),
                     text=f"Learned {len(steps)} steps and saved the reusable workflow.",
                     cursor=0))
    return spec
