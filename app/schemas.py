"""app/schemas.py — M0. The single source of shared types.

Every module imports from here and nowhere sideways. This file is the
executable form of docs/contracts.md v2. If code and contracts.md disagree,
contracts.md wins and this file is the bug.

Usage:
    from app.schemas import Step, ActionType, LocatorBundle, Locator
    step = Step(
        id="open-settings",
        index=0,
        action=ActionType.CLICK,
        intent="open the settings menu",
        locator=LocatorBundle(
            intent="the settings gear icon, top right",
            candidates=[Locator(strategy="role", value="button", name="Settings")],
        ),
        narration_hint="clicking the gear to open settings",
    )
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, model_validator

# --------------------------------------------------------------------------
# 1. Core enums
# --------------------------------------------------------------------------


class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    SCROLL = "scroll"
    WAIT_FOR = "wait_for"
    ASSERT = "assert"


class RiskLevel(str, Enum):
    SAFE = "safe"
    CAUTION = "caution"
    DESTRUCTIVE = "destructive"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class RunState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    ABORTED = "aborted"


class InterruptKind(str, Enum):
    QUESTION = "question"
    SKIP = "skip"
    JUMP = "jump"
    REPEAT = "repeat"
    STOP = "stop"
    PAUSE = "pause"
    UNCLEAR = "unclear"


# Steps that never carry a locator.
LOCATORLESS_ACTIONS: frozenset[ActionType] = frozenset(
    {ActionType.NAVIGATE, ActionType.SCROLL}
)


# --------------------------------------------------------------------------
# 2. Locators
# --------------------------------------------------------------------------

LocatorStrategy = Literal["role", "test_id", "label", "placeholder", "text", "css"]

STRATEGY_PREFERENCE: tuple[str, ...] = (
    "test_id",
    "role",
    "label",
    "placeholder",
    "text",
    "css",
)


class Locator(BaseModel):
    """One way to find one element.

    Usage:
        Locator(strategy="test_id", value="signature-create")
    """

    strategy: LocatorStrategy
    value: str
    name: str | None = None
    exact: bool = False


class LocatorBundle(BaseModel):
    """Ordered candidates plus the natural-language intent used to self-heal.

    Usage:
        LocatorBundle(intent="the Create button under Signature",
                      candidates=[Locator(strategy="role", value="button",
                                          name="Create new")])
    """

    intent: str
    candidates: list[Locator] = Field(min_length=1)
    healed_at: datetime | None = None
    heal_count: int = 0

    def preference_rank(self) -> list[int]:
        """Rank of each candidate under the §2 ordering. Lower is preferred.

        Usage:
            bundle.preference_rank()  # -> [1, 5]
        """
        return [STRATEGY_PREFERENCE.index(c.strategy) for c in self.candidates]


# --------------------------------------------------------------------------
# 3. Page observation (shared: produced by M1, consumed by M2/M4/M6/M8/M9)
# --------------------------------------------------------------------------

A11Y_DIGEST_LIMIT = 4000
VISIBLE_TEXT_LIMIT = 3000


class PageSnapshot(BaseModel):
    """What the agent can see right now.

    Usage:
        PageSnapshot(url="https://github.com/", title="GitHub",
                     a11y_digest="- button \"New issue\"", visible_text="New issue")
    """

    url: str
    title: str
    a11y_digest: str = ""
    visible_text: str = ""


class ActResult(BaseModel):
    """Outcome of exactly one driver action.

    Usage:
        ActResult(ok=True, effect="the settings panel opened")
    """

    ok: bool
    healed: bool = False
    error: str | None = None
    effect: str = ""


# --------------------------------------------------------------------------
# 4. Workflow artifact
# --------------------------------------------------------------------------


class StepKnowledge(BaseModel):
    """Captured during rehearsal. This is what QA is grounded in.

    Usage:
        StepKnowledge(page_url="https://github.com/o/r/issues/new",
                      page_title="New issue", a11y_digest="...",
                      visible_text="...", observed_effect="the issue form opened",
                      captured_at=datetime.now(timezone.utc))
    """

    page_url: str
    page_title: str
    a11y_digest: str = ""
    visible_text: str = ""
    screenshot_path: str | None = None
    observed_effect: str = ""
    captured_at: datetime

    @model_validator(mode="after")
    def _truncate(self) -> "StepKnowledge":
        if len(self.a11y_digest) > A11Y_DIGEST_LIMIT:
            object.__setattr__(self, "a11y_digest", self.a11y_digest[:A11Y_DIGEST_LIMIT])
        if len(self.visible_text) > VISIBLE_TEXT_LIMIT:
            object.__setattr__(self, "visible_text", self.visible_text[:VISIBLE_TEXT_LIMIT])
        return self


class Step(BaseModel):
    """One frozen, replayable action.

    Usage:
        Step(id="new-issue", index=0, action=ActionType.NAVIGATE,
             intent="open the new issue form", value="https://github.com/o/r/issues/new",
             narration_hint="heading to the new issue page")
    """

    id: str
    index: int
    action: ActionType
    intent: str
    locator: LocatorBundle | None = None
    value: str | None = None
    narration_hint: str = ""
    risk: RiskLevel = RiskLevel.SAFE
    knowledge: StepKnowledge | None = None
    timeout_ms: int = 8000

    @model_validator(mode="after")
    def _locator_required(self) -> "Step":
        if self.action not in LOCATORLESS_ACTIONS and self.locator is None:
            raise ValueError(
                f"step {self.id!r}: action {self.action.value!r} requires a locator"
            )
        if self.action is ActionType.NAVIGATE and not self.value:
            raise ValueError(f"step {self.id!r}: NAVIGATE requires value (a url)")
        return self


class WorkflowSpec(BaseModel):
    """The artifact teaching produces and call mode consumes.

    Usage:
        WorkflowSpec(id="gh-issue", title="Create a labelled issue", description="...",
                     target_domain="github.com", entry_url="https://github.com/",
                     steps=[], taught_at=datetime.now(timezone.utc),
                     rehearsal_passed=True, source_utterance="...")
    """

    id: str
    version: int = 1
    title: str
    description: str = ""
    target_domain: str
    entry_url: str
    steps: list[Step] = []
    taught_at: datetime
    rehearsal_passed: bool = False
    source_utterance: str = ""

    def step_by_id(self, step_id: str) -> Step | None:
        """Look up a step by its slug.

        Usage:
            spec.step_by_id("open-settings")
        """
        return next((s for s in self.steps if s.id == step_id), None)


class WorkflowSummary(BaseModel):
    """Listing row for the UI.

    Usage:
        WorkflowSummary(id="gh-issue", version=1, title="Create issue",
                        target_domain="github.com", n_steps=5, rehearsal_passed=True)
    """

    id: str
    version: int
    title: str
    target_domain: str
    n_steps: int
    rehearsal_passed: bool


# --------------------------------------------------------------------------
# 5. Runtime state
# --------------------------------------------------------------------------


class StepRecord(BaseModel):
    """One row of what actually happened.

    Usage:
        StepRecord(step_id="open-settings", status=StepStatus.DONE)
    """

    step_id: str
    status: StepStatus
    started_at: datetime | None = None
    ended_at: datetime | None = None
    error: str | None = None
    healed: bool = False


class ChatTurn(BaseModel):
    """One line of the conversation, for the transcript.

    Usage:
        ChatTurn(role="agent", text="opening settings now", ts=datetime.now(timezone.utc))
    """

    role: Literal["agent", "customer"]
    text: str
    ts: datetime
    during_step: str | None = None


class SessionState(BaseModel):
    """Live state of one run. The cursor invariant lives here.

    Usage:
        SessionState(session_id="s1", workflow_id="gh-issue", cursor=0,
                     state=RunState.IDLE, records=[])
    """

    session_id: str
    workflow_id: str
    cursor: int = 0
    state: RunState = RunState.IDLE
    records: list[StepRecord] = []
    interrupt_count: int = 0
    transcript: list[ChatTurn] = []


# --------------------------------------------------------------------------
# 6. Events
# --------------------------------------------------------------------------

EventType = Literal[
    "narration",
    "step_start",
    "step_done",
    "step_failed",
    "heal",
    "interrupt_received",
    "answer",
    "paused",
    "resumed",
    "plan_changed",
    "blocked",
    "run_started",
    "run_completed",
    "run_aborted",
    "teach_progress",
    "teach_question",
    "teach_complete",
    "error",
    "frame",
]


class Event(BaseModel):
    """Everything the user sees is one of these.

    Usage:
        Event(type="step_start", session_id="s1", ts=datetime.now(timezone.utc),
              step_id="open-settings", cursor=0)
    """

    type: EventType
    session_id: str
    ts: datetime
    text: str | None = None
    step_id: str | None = None
    cursor: int | None = None
    data: dict[str, Any] = {}


EmitFn = Callable[[Event], Awaitable[None]]
"""Signature every module uses to speak to the UI: `await emit(Event(...))`."""


# --------------------------------------------------------------------------
# 7. Teaching drafts
# --------------------------------------------------------------------------


class DraftStep(BaseModel):
    """Compiler output: an action with no locator yet.

    Usage:
        DraftStep(action=ActionType.CLICK, intent="click New issue",
                  narration_hint="opening the new issue form")
    """

    action: ActionType
    intent: str
    value: str | None = None
    narration_hint: str = ""
    risk: RiskLevel = RiskLevel.SAFE


class DraftWorkflow(BaseModel):
    """Compiler output before rehearsal.

    Usage:
        DraftWorkflow(title="Create issue", target_domain="github.com",
                      entry_url="https://github.com/o/r", steps=[], ambiguities=[])
    """

    title: str
    target_domain: str
    entry_url: str
    steps: list[DraftStep] = []
    ambiguities: list[str] = []


# --------------------------------------------------------------------------
# 8. Interrupt / QA / Guard payloads
# --------------------------------------------------------------------------


class InterruptDecision(BaseModel):
    """Classifier output.

    Usage:
        InterruptDecision(kind=InterruptKind.QUESTION, rationale="asks what a control does")
    """

    kind: InterruptKind
    target_step_index: int | None = None
    rationale: str


class Answer(BaseModel):
    """Grounded QA output. `grounded=False` means we declined, on purpose.

    Usage:
        Answer(text="That toggle turns on the signature.", grounded=True,
               sources=["add-signature"])
    """

    text: str
    grounded: bool
    sources: list[str] = []


class GuardVerdict(BaseModel):
    """Safety decision for exactly one step.

    Usage:
        GuardVerdict(allowed=False, risk=RiskLevel.DESTRUCTIVE,
                     reason="'Send' is irreversible, so I won't click it")
    """

    allowed: bool
    risk: RiskLevel
    reason: str


# --------------------------------------------------------------------------
# 9. Exceptions
# --------------------------------------------------------------------------


class BlockedAction(Exception):
    """Raised by Driver.act() when the guard refuses. Carries the verdict."""

    def __init__(self, verdict: GuardVerdict, step_id: str = "") -> None:
        self.verdict = verdict
        self.step_id = step_id
        super().__init__(f"blocked {step_id}: {verdict.reason}")


class CompileError(Exception):
    """Raised by M3 when the model output cannot be turned into a draft."""


class WorkflowNotFound(Exception):
    """Raised by M10 when an id/version does not exist on disk."""


# --------------------------------------------------------------------------
# 10. Structural protocols (so modules can type-hint each other without importing)
# --------------------------------------------------------------------------


class DriverLike(Protocol):
    async def start(self, entry_url: str, storage_state: str | None = None,
                    cdp_endpoint: str | None = None) -> None: ...
    async def stop(self) -> None: ...
    async def snapshot(self) -> PageSnapshot: ...
    async def screenshot(self, path: str) -> str: ...
    async def act(self, step: Step, resolver: Any, guard: Any) -> ActResult: ...


class NarratorLike(Protocol):
    async def narrate(self, step: Step, snap: PageSnapshot) -> str: ...
    async def resume_line(self, step: Step, cursor: int, total: int) -> str: ...


class GuardLike(Protocol):
    def check(self, step: Step, snap: PageSnapshot) -> GuardVerdict: ...
