"""tests/fakes.py — the only test doubles in the project.

No real browser, no real model, no network. Written in-house for Demo 1 so the
integration test exists before delegates return; delegates may ADD to this file
via the master chat only, never edit existing behaviour.

Usage:
    driver = FakeDriver([make_snapshot("New issue")])
    orch = Orchestrator(make_spec(5), driver, FakeResolver(), FakeNarrator(),
                        FakeGuard(), collector.emit)
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas import (
    ActionType,
    ActResult,
    Answer,
    BlockedAction,
    Event,
    GuardVerdict,
    InterruptDecision,
    InterruptKind,
    Locator,
    LocatorBundle,
    PageSnapshot,
    RiskLevel,
    Step,
    StepKnowledge,
    WorkflowSpec,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def make_snapshot(text: str = "", url: str = "https://github.com/") -> PageSnapshot:
    """Build a PageSnapshot for tests.

    Usage:
        make_snapshot("New issue", url="https://github.com/o/r/issues/new")
    """
    return PageSnapshot(
        url=url,
        title="GitHub",
        a11y_digest=f'- button "{text or "Something"}"',
        visible_text=text,
    )


def make_spec(n_steps: int = 5, with_knowledge: bool = True) -> WorkflowSpec:
    """Build a runnable WorkflowSpec with n_steps CLICK steps.

    Usage:
        spec = make_spec(3, with_knowledge=False)
    """
    steps: list[Step] = []
    for i in range(n_steps):
        steps.append(
            Step(
                id=f"step-{i}",
                index=i,
                action=ActionType.CLICK,
                intent=f"click thing {i}",
                locator=LocatorBundle(
                    intent=f"thing {i}",
                    candidates=[Locator(strategy="test_id", value=f"thing-{i}")],
                ),
                narration_hint=f"clicking thing {i}",
                knowledge=(
                    StepKnowledge(
                        page_url=f"https://github.com/step/{i}",
                        page_title="GitHub",
                        a11y_digest=f'- button "thing {i}"',
                        visible_text=f"thing {i}",
                        observed_effect=f"thing {i} was clicked",
                        captured_at=_now(),
                    )
                    if with_knowledge
                    else None
                ),
            )
        )
    return WorkflowSpec(
        id="fake-workflow",
        title="Fake workflow",
        description="for tests",
        target_domain="github.com",
        entry_url="https://github.com/",
        steps=steps,
        taught_at=_now(),
        rehearsal_passed=True,
        source_utterance="do the fake thing",
    )


class FakeDriver:
    """Scriptable Driver. Records every act() call.

    Usage:
        d = FakeDriver([make_snapshot("a")], fail_on={"step-2"})
        await d.act(step, resolver, guard)
    """

    def __init__(
        self,
        snapshots: list[PageSnapshot] | None = None,
        fail_on: set[str] = frozenset(),
        heal_on: set[str] = frozenset(),
        block_on: set[str] = frozenset(),
    ) -> None:
        self.snapshots = list(snapshots or [make_snapshot("default")])
        self.fail_on = set(fail_on)
        self.heal_on = set(heal_on)
        self.block_on = set(block_on)
        self.calls: list[str] = []
        self.started = False
        self.stopped = False
        self._snap_i = 0

    async def start(self, entry_url: str, storage_state: str | None = None,
                    cdp_endpoint: str | None = None) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def snapshot(self) -> PageSnapshot:
        snap = self.snapshots[min(self._snap_i, len(self.snapshots) - 1)]
        self._snap_i += 1
        return snap

    async def screenshot(self, path: str) -> str:
        return path

    async def act(self, step: Step, resolver=None, guard=None) -> ActResult:
        self.calls.append(step.id)
        if guard is not None:
            verdict = guard.check(step, await self.snapshot())
            if not verdict.allowed:
                raise BlockedAction(verdict, step.id)
        if step.id in self.block_on:
            verdict = GuardVerdict(allowed=False, risk=RiskLevel.DESTRUCTIVE,
                                   reason=f"{step.intent} is irreversible")
            raise BlockedAction(verdict, step.id)
        if step.id in self.fail_on:
            return ActResult(ok=False, error=f"could not find {step.intent}")
        return ActResult(ok=True, healed=step.id in self.heal_on,
                         effect=f"{step.intent} — done")


class FakeLLM:
    """Returns queued responses in order. Raises if drained.

    Usage:
        llm = FakeLLM(["{\\"kind\\": \\"question\\"}"])
        await llm.complete("sys", "user")
    """

    def __init__(self, responses: list[str | dict]) -> None:
        self._responses = list(responses)
        self.prompts: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str, json_schema: dict | None = None,
                       max_tokens: int = 1024) -> str | dict:
        self.prompts.append((system, user))
        if not self._responses:
            raise AssertionError("FakeLLM drained: an unexpected extra model call was made")
        return self._responses.pop(0)


class FakeNarrator:
    """Narrator that echoes the live snapshot, so anti-canned tests can assert overlap."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def narrate(self, step: Step, snap: PageSnapshot) -> str:
        self.calls.append(step.id)
        return f"{step.narration_hint} — I can see {snap.visible_text or snap.title}"

    async def resume_line(self, step: Step, cursor: int, total: int) -> str:
        return f"back to it — step {cursor + 1} of {total}: {step.intent}"


class FakeResolver:
    """Resolver that always returns the bundle it was given."""

    async def resolve(self, intent: str, snap: PageSnapshot) -> LocatorBundle:
        return LocatorBundle(intent=intent,
                             candidates=[Locator(strategy="text", value=intent)])

    async def heal(self, bundle: LocatorBundle, snap: PageSnapshot) -> LocatorBundle | None:
        return bundle.model_copy(update={"heal_count": bundle.heal_count + 1,
                                         "healed_at": _now()})


class FakeGuard:
    """Guard that allows everything unless the step id is in `refuse`."""

    def __init__(self, refuse: set[str] = frozenset()) -> None:
        self.refuse = set(refuse)
        self.checked: list[str] = []

    def check(self, step: Step, snap: PageSnapshot) -> GuardVerdict:
        self.checked.append(step.id)
        if step.id in self.refuse or step.risk is RiskLevel.DESTRUCTIVE:
            return GuardVerdict(allowed=False, risk=RiskLevel.DESTRUCTIVE,
                                reason=f"I won't do '{step.intent}' — it can't be undone")
        return GuardVerdict(allowed=True, risk=step.risk,
                            reason=f"'{step.intent}' is reversible")


class FakeQA:
    """QA that is grounded iff the question shares a word with captured knowledge."""

    async def answer(self, question: str, spec: WorkflowSpec, cursor: int,
                     live: PageSnapshot) -> Answer:
        corpus = " ".join(
            (s.knowledge.visible_text if s.knowledge else "") for s in spec.steps
        ).lower()
        hit = any(w for w in question.lower().split() if len(w) > 3 and w in corpus)
        if hit:
            return Answer(text=f"On this step: {question.strip('?')} — yes.",
                          grounded=True, sources=[spec.steps[cursor].id])
        return Answer(text="That wasn't part of what I was shown, so I can't say.",
                      grounded=False, sources=[])


class FakeTTS:
    """TTS that never hits the network. Returns `fixed_audio` (default: none)."""

    def __init__(self, fixed_audio: str | None = None) -> None:
        self.fixed_audio = fixed_audio
        self.calls: list[str] = []

    async def synthesize(self, text: str) -> str | None:
        self.calls.append(text)
        return self.fixed_audio


class FakeInterrupter:
    """Classifier driven by a scripted mapping of substrings to kinds."""

    def __init__(self, mapping: dict[str, InterruptKind] | None = None) -> None:
        self.mapping = mapping or {}

    async def classify(self, text: str, spec: WorkflowSpec, cursor: int) -> InterruptDecision:
        for needle, kind in self.mapping.items():
            if needle in text.lower():
                return InterruptDecision(kind=kind, rationale=f"matched {needle!r}")
        return InterruptDecision(kind=InterruptKind.QUESTION, rationale="default: a question")


class EventCollector:
    """Captures the event stream for assertions.

    Usage:
        c = EventCollector(); orch = Orchestrator(..., emit=c.emit)
        assert c.types() == ["run_started", ...]
    """

    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [e.type for e in self.events]

    def of(self, type_: str) -> list[Event]:
        return [e for e in self.events if e.type == type_]
