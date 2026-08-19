"""Model-backed interruption classification for the live walkthrough."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Awaitable, Callable

from app.llm import complete
from app.schemas import InterruptDecision, InterruptKind, WorkflowSpec

# A locally served model answers in seconds, not milliseconds. Timing out here is
# indistinguishable to the customer from the agent refusing to answer, so the
# budget has to cover a real local call.
CLASSIFY_TIMEOUT_S = float(os.getenv("WA_CLASSIFY_TIMEOUT", "20"))


def _unclear(rationale: str) -> InterruptDecision:
    return InterruptDecision(kind=InterruptKind.UNCLEAR, rationale=rationale or "I could not classify that request.")


def _steps(spec: WorkflowSpec) -> list[dict[str, Any]]:
    return [{"index": index, "id": step.id, "intent": step.intent} for index, step in enumerate(spec.steps)]


async def classify(text: str, spec: WorkflowSpec, cursor: int) -> InterruptDecision:
    """Classify one customer interruption without allowing errors into the run loop."""
    return await Interrupter().classify(text, spec, cursor)


class Interrupter:
    """Classifies interruptions; `llm` is injectable for deterministic tests."""

    def __init__(self, llm: Callable[..., Awaitable[Any]] | None = None) -> None:
        self._llm = llm or complete

    async def classify(self, text: str, spec: WorkflowSpec, cursor: int) -> InterruptDecision:
        if not text.strip():
            return _unclear("The message was empty.")

        system = """You classify customer interruptions during a browser walkthrough.
Return JSON only with keys kind, target_step_index, rationale. kind must be one of
question, skip, jump, repeat, stop, pause, unclear. A question about what the agent
just did or is looking at is question even if phrased as a complaint, such as 'wait,
why did you click there?'. A bare request to hold on for a moment before continuing
- such as 'wait', 'hold on', 'pause', 'give me a second', 'one moment' - with no
question and no request to change the plan, is pause. Requests to change what
happens next are skip, jump, or repeat. Choose jump only when a requested
destination maps to a listed step. If the customer asks to show, open, navigate to,
or demonstrate something different that is not one of the listed workflow steps,
return unclear so the app can say it is out of scope for this demo. Use unclear if
there is not enough information. rationale must be brief and non-empty."""
        user = json.dumps({"message": text, "current_cursor": cursor, "steps": _steps(spec)})
        schema = {
            "type": "object",
            "properties": {
                # Spelling the kinds out lets a constrained decoder hold a small
                # model to one of them instead of inventing a label.
                "kind": {"type": "string", "enum": [k.value for k in InterruptKind]},
                "target_step_index": {"type": ["integer", "null"]},
                "rationale": {"type": "string"},
            },
            "required": ["kind", "rationale"],
        }
        try:
            raw = await asyncio.wait_for(self._llm(system, user, json_schema=schema, max_tokens=180), CLASSIFY_TIMEOUT_S)
            if isinstance(raw, str):
                raw = json.loads(raw)
            if not isinstance(raw, dict):
                return _unclear("The classifier returned no usable decision.")
            if isinstance(raw.get("kind"), str):  # models capitalise; the enum does not
                raw["kind"] = raw["kind"].strip().lower()
            decision = InterruptDecision.model_validate(raw)
        except Exception:  # classification is a non-fatal live-loop boundary
            return _unclear("I could not classify that request.")

        if not decision.rationale.strip():
            return _unclear("The classifier did not explain its decision.")
        if decision.kind is InterruptKind.JUMP:
            target = decision.target_step_index
            if not isinstance(target, int) or isinstance(target, bool) or not 0 <= target < len(spec.steps):
                return _unclear("The requested destination is not a workflow step.")
        return decision
