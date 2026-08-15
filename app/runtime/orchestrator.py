"""app/runtime/orchestrator.py — M5 v0 (Demo 1).

Linear run loop. NO event queue: `submit_user_message` accepts and parks
messages so the WebSocket layer can be written against the final signature,
but nothing drains them yet. Demo 2 replaces `_drain_events` with the real
implementation and adds Interrupter/QA to `__init__`. Everything else in this
file is meant to survive that change unedited.

The cursor invariant is enforced here structurally: `cursor` is mutated in
exactly one place, `_advance()`.

Usage:
    orch = Orchestrator(spec, driver, resolver, narrator, guard, emit, session_id="s1")
    state = await orch.run()
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import structlog

from app.runtime.interrupt import Interrupter
from app.runtime.qa import QA
from app.schemas import (
    ActResult,
    BlockedAction,
    Event,
    EventType,
    InterruptKind,
    PageSnapshot,
    RunState,
    SessionState,
    Step,
    StepRecord,
    StepStatus,
    WorkflowSpec,
)

log = structlog.get_logger(__name__)

STEP_HARD_TIMEOUT_S = 30.0
NARRATION_TIMEOUT_S = 6.0
STEP_PACE_S = float(os.getenv("WA_STEP_PACE_S", "1.25"))

_PAUSE_SENTINEL = "__control_pause__"
_RESUME_SENTINEL = "__control_resume__"
_STOP_SENTINEL = "__control_stop__"
_RESUME_WORDS = {"yes", "y", "resume", "continue", "go", "go ahead", "keep going"}
_STOP_WORDS = {"no", "n", "stop", "cancel", "abort", "end"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Orchestrator:
    """Runs one WorkflowSpec against one browser session.

    Usage:
        Orchestrator(spec, driver, resolver, narrator, guard, emit).run()
    """

    def __init__(
        self,
        spec: WorkflowSpec,
        driver: Any,
        resolver: Any,
        narrator: Any,
        guard: Any,
        emit: Any,
        session_id: str = "session",
        interrupter: Any | None = None,
        qa: Any | None = None,
    ) -> None:
        self.spec = spec
        self.driver = driver
        self.resolver = resolver
        self.narrator = narrator
        self.guard = guard
        self.emit = emit
        self.interrupter = interrupter or Interrupter()
        self.qa = qa or QA()
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._state = SessionState(
            session_id=session_id,
            workflow_id=spec.id,
            cursor=0,
            state=RunState.IDLE,
            records=[],
        )

    # -- public -----------------------------------------------------------

    @property
    def state(self) -> SessionState:
        """The live session state. Read-only by convention.

        Usage:
            orch.state.cursor
        """
        return self._state

    def submit_user_message(self, text: str) -> None:
        """Enqueue a user message. Never blocks, never awaits.

        Drained in `_drain_events` while running, or answered as a yes/no
        resume prompt in `_wait_for_resume` while paused.

        Usage:
            orch.submit_user_message("wait, what does that do?")
        """
        self._queue.put_nowait(text)
        log.info("user_message_parked", text=text, cursor=self._state.cursor)

    def request_pause(self) -> None:
        """Deterministic pause for a UI control, bypassing the LLM classifier.

        Takes effect after the current step finishes, same as a typed "wait".

        Usage:
            orch.request_pause()
        """
        self._queue.put_nowait(_PAUSE_SENTINEL)

    def request_resume(self) -> None:
        """Deterministic resume for a UI control, bypassing the LLM classifier.

        Usage:
            orch.request_resume()
        """
        self._queue.put_nowait(_RESUME_SENTINEL)

    def request_stop(self) -> None:
        """Deterministic stop for a UI control, bypassing the LLM classifier.

        Usage:
            orch.request_stop()
        """
        self._queue.put_nowait(_STOP_SENTINEL)

    async def run(self) -> SessionState:
        """Execute the workflow start to finish and return the final state.

        Usage:
            state = await orch.run()
        """
        s = self._state
        s.state = RunState.RUNNING
        await self._emit("run_started", text=self.spec.title, cursor=s.cursor)

        while s.cursor < len(self.spec.steps) and s.state in (RunState.RUNNING, RunState.INTERRUPTED):
            if s.state is RunState.INTERRUPTED:
                await self._wait_for_resume()
                continue

            await self._drain_events()
            if s.cursor >= len(self.spec.steps) or s.state is not RunState.RUNNING:
                continue

            step = self.spec.steps[s.cursor]
            await self._run_step(step)
            await self._drain_events()

        if s.state is RunState.RUNNING:
            s.state = RunState.COMPLETED
            await self._emit("run_completed", cursor=s.cursor)
        elif s.state is RunState.ABORTED:
            await self._emit("run_aborted", cursor=s.cursor)
        return s

    # -- internals --------------------------------------------------------

    async def _run_step(self, step: Step) -> None:
        record = StepRecord(step_id=step.id, status=StepStatus.RUNNING, started_at=_now())
        self._state.records.append(record)
        await self._emit("step_start", text=step.intent, step_id=step.id,
                         cursor=self._state.cursor)

        snap = await self._safe_snapshot()
        await self._narrate(step, snap)
        if self.driver.__class__.__module__ == "app.browser.driver" and STEP_PACE_S > 0:
            await asyncio.sleep(STEP_PACE_S)

        try:
            result = await asyncio.wait_for(
                self.driver.act(step, self.resolver, self.guard),
                timeout=STEP_HARD_TIMEOUT_S,
            )
        except BlockedAction as exc:
            record.status = StepStatus.BLOCKED
            record.ended_at = _now()
            record.error = exc.verdict.reason
            await self._emit("blocked", text=exc.verdict.reason, step_id=step.id,
                             cursor=self._state.cursor,
                             data={"risk": exc.verdict.risk.value})
            self._advance()
            return
        except asyncio.TimeoutError:
            result = ActResult(ok=False, error=f"step timed out after {STEP_HARD_TIMEOUT_S}s")
        except Exception as exc:  # noqa: BLE001 - boundary: one step must not kill a demo
            log.exception("step_crashed", step_id=step.id)
            result = ActResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        record.healed = result.healed
        record.ended_at = _now()
        if result.healed:
            await self._emit("heal", text=f"the page moved, I re-found it: {step.intent}",
                             step_id=step.id, cursor=self._state.cursor)

        if result.ok:
            record.status = StepStatus.DONE
            await self._emit("step_done", text=result.effect, step_id=step.id,
                             cursor=self._state.cursor)
            # Drain after the browser action but before advancing: an interruption
            # received during this action must resume the step the user was watching.
            await self._drain_events()
            # A pause leaves state INTERRUPTED but the action above already completed,
            # so the cursor still advances; only an abort or an explicit jump skips it.
            if self._state.state is RunState.ABORTED or self._state.cursor != step.index:
                return
            self._advance()
        else:
            record.status = StepStatus.FAILED
            record.error = result.error
            await self._emit("step_failed", text=result.error, step_id=step.id,
                             cursor=self._state.cursor)
            self._handle_failure(step, result)

    def _handle_failure(self, step: Step, result: ActResult) -> None:
        """Demo 1 policy: a failed step aborts the run and never moves the cursor."""
        self._state.state = RunState.ABORTED

    def _advance(self) -> None:
        """The ONLY place the cursor moves during normal execution."""
        self._state.cursor += 1

    async def _drain_events(self) -> None:
        """Handle every queued interruption without letting it kill the run loop."""
        s = self._state
        while not self._queue.empty() and s.state is RunState.RUNNING:
            text = self._queue.get_nowait()
            if text == _PAUSE_SENTINEL:
                await self._pause()
                return
            s.interrupt_count += 1
            await self._emit("interrupt_received", text=text, cursor=s.cursor)
            decision = await self.interrupter.classify(text, self.spec, s.cursor)

            # Injected classifiers are also untrusted: validate JUMP targets here.
            if decision.kind.value == "jump" and (
                not isinstance(decision.target_step_index, int)
                or isinstance(decision.target_step_index, bool)
                or not 0 <= decision.target_step_index < len(self.spec.steps)
            ):
                decision = decision.model_copy(update={"kind": InterruptKind.UNCLEAR, "target_step_index": None,
                                                        "rationale": "invalid jump target"})

            if decision.kind.value == "question":
                answer = await self.qa.answer(text, self.spec, s.cursor, await self._safe_snapshot())
                await self._emit("answer", text=answer.text, cursor=s.cursor,
                                 data={"grounded": answer.grounded, "sources": answer.sources})
                if s.cursor < len(self.spec.steps):
                    try:
                        resume = await self.narrator.resume_line(
                            self.spec.steps[s.cursor], s.cursor, len(self.spec.steps)
                        )
                    except Exception as exc:  # noqa: BLE001 - narration cannot break resumption
                        log.warning("resume_line_failed", error=str(exc), cursor=s.cursor)
                        resume = f"Back to step {s.cursor + 1} of {len(self.spec.steps)}."
                    await self._emit("resumed", text=resume, cursor=s.cursor)
                continue  # A QUESTION deliberately never changes the cursor.

            if decision.kind.value == "skip":
                if s.cursor < len(self.spec.steps):
                    step = self.spec.steps[s.cursor]
                    s.records.append(StepRecord(step_id=step.id, status=StepStatus.SKIPPED,
                                                started_at=_now(), ended_at=_now()))
                    await self._emit("plan_changed", text=f"Skipped: {step.intent}",
                                     step_id=step.id, cursor=s.cursor, data={"kind": "skip"})
                    self._advance()
                continue

            if decision.kind.value == "jump":
                target = decision.target_step_index
                assert target is not None  # validated above
                if target > s.cursor:
                    for index in range(s.cursor, target):
                        step = self.spec.steps[index]
                        s.records.append(StepRecord(step_id=step.id, status=StepStatus.SKIPPED,
                                                    started_at=_now(), ended_at=_now()))
                # JUMP_CURSOR_REPOSITION: sanctioned direct cursor mutation for a requested destination.
                s.cursor = target
                await self._emit("plan_changed", text=f"Jumping to step {target + 1}.",
                                 cursor=s.cursor, data={"kind": "jump", "target_step_index": target})
                continue

            if decision.kind.value == "repeat":
                self._repeat_current = True
                await self._emit("plan_changed", text="Repeating the current step.", cursor=s.cursor,
                                 data={"kind": "repeat"})
                continue

            if decision.kind.value == "stop":
                s.state = RunState.ABORTED
                await self._emit("run_aborted", text="Walkthrough stopped.", cursor=s.cursor)
                break

            if decision.kind.value == "pause":
                await self._pause()
                return

            await self._emit("answer", text="That request is outside the scope of this demo. Could you clarify whether you want an answer about this workflow, a skip, a jump within it, a repeat, to pause, or to stop?",
                             cursor=s.cursor, data={"grounded": False, "sources": []})

    async def _pause(self) -> None:
        """Pause after the current step: sets state so `run()` blocks in `_wait_for_resume`."""
        self._state.state = RunState.INTERRUPTED
        await self._emit("paused", text='Paused. Say "resume" or click Resume to continue, '
                         'or "stop" to end the walkthrough.', cursor=self._state.cursor)

    async def _wait_for_resume(self) -> None:
        """Block until the paused run is told to resume or stop.

        Deterministic on purpose: control-flow while paused must not depend
        on an LLM classification succeeding.
        """
        text = await self._queue.get()
        normalized = text.strip().lower()
        if text == _RESUME_SENTINEL or normalized in _RESUME_WORDS:
            self._state.state = RunState.RUNNING
            await self._emit("resumed", text="Resuming.", cursor=self._state.cursor)
        elif text == _STOP_SENTINEL or normalized in _STOP_WORDS:
            self._state.state = RunState.ABORTED
            await self._emit("run_aborted", text="Walkthrough stopped.", cursor=self._state.cursor)
        else:
            await self._emit("paused", text='Still paused — say "resume" to continue or "stop" to end.',
                             cursor=self._state.cursor)

    async def _narrate(self, step: Step, snap: PageSnapshot) -> None:
        try:
            text = await asyncio.wait_for(
                self.narrator.narrate(step, snap), timeout=NARRATION_TIMEOUT_S
            )
        except Exception as exc:  # noqa: BLE001 - narration must never stall a demo
            log.warning("narration_fallback", step_id=step.id, error=str(exc))
            text = step.narration_hint or step.intent
        if text:
            # Voice is intentionally client-side speechSynthesis only; no server
            # TTS call here, so narration never waits on an audio API.
            await self._emit("narration", text=text, step_id=step.id,
                             cursor=self._state.cursor)

    async def _safe_snapshot(self) -> PageSnapshot:
        try:
            return await self.driver.snapshot()
        except Exception as exc:  # noqa: BLE001 - snapshot is advisory, not fatal
            log.warning("snapshot_failed", error=str(exc))
            return PageSnapshot(url="", title="")

    async def _emit(self, type_: EventType, *, text: str | None = None,
                    step_id: str | None = None, cursor: int | None = None,
                    data: dict[str, Any] | None = None) -> None:
        await self.emit(Event(type=type_, session_id=self._state.session_id, ts=_now(),
                              text=text, step_id=step_id, cursor=cursor, data=data or {}))
