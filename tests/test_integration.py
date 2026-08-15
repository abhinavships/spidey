"""tests/test_integration.py — written by the master chat, never by a delegate.

Demo 1 covers case 1 (clean run) plus the Demo-1-reachable slices of case 5
(blocked action) and the cursor invariant's floor (no cursor movement without
step completion). Cases 2-4 and 6 land with Demo 2 / Demo 3.
"""

from __future__ import annotations

import asyncio

import pytest

from app.runtime.orchestrator import Orchestrator
from app.schemas import RunState, StepStatus
from tests.fakes import (
    EventCollector,
    FakeDriver,
    FakeGuard,
    FakeNarrator,
    FakeResolver,
    make_snapshot,
    make_spec,
)


def build(spec=None, driver=None, guard=None):
    spec = spec or make_spec(5)
    driver = driver or FakeDriver([make_snapshot(f"page {i}") for i in range(8)])
    collector = EventCollector()
    orch = Orchestrator(
        spec=spec,
        driver=driver,
        resolver=FakeResolver(),
        narrator=FakeNarrator(),
        guard=guard or FakeGuard(),
        emit=collector.emit,
        session_id="test-session",
    )
    return orch, driver, collector


# -- Case 1: clean run -----------------------------------------------------


async def test_clean_run_all_steps_done_in_order():
    orch, driver, c = build()
    state = await orch.run()

    assert state.state is RunState.COMPLETED
    assert driver.calls == [f"step-{i}" for i in range(5)]
    assert [r.status for r in state.records] == [StepStatus.DONE] * 5
    assert state.cursor == 5


async def test_clean_run_event_order():
    orch, _, c = build()
    await orch.run()
    types = c.types()

    assert types[0] == "run_started"
    assert types[-1] == "run_completed"
    # per step: step_start -> narration -> step_done
    body = types[1:-1]
    assert body == ["step_start", "narration", "step_done"] * 5


async def test_every_event_carries_session_and_cursor_context():
    orch, _, c = build()
    await orch.run()
    assert all(e.session_id == "test-session" for e in c.events)
    assert all(e.step_id is not None for e in c.events if e.type.startswith("step_"))


async def test_narration_is_not_canned():
    """Narration must reflect the live snapshot, not just narration_hint."""
    orch, _, c = build()
    await orch.run()
    lines = [e.text for e in c.of("narration")]
    assert len(set(lines)) == len(lines), "narration repeated verbatim across steps"
    assert all("page" in (line or "") for line in lines)


# -- Cursor floor ----------------------------------------------------------


async def test_failed_step_does_not_advance_cursor():
    spec = make_spec(5)
    driver = FakeDriver([make_snapshot("x")], fail_on={"step-2"})
    orch, driver, c = build(spec=spec, driver=driver)
    state = await orch.run()

    assert state.cursor == 2, "cursor must not move past a failed step"
    assert state.state is RunState.ABORTED
    assert driver.calls == ["step-0", "step-1", "step-2"]
    assert c.of("step_failed")[0].step_id == "step-2"


async def test_empty_workflow_completes_immediately():
    orch, driver, c = build(spec=make_spec(0))
    state = await orch.run()
    assert state.state is RunState.COMPLETED
    assert driver.calls == []
    assert c.types() == ["run_started", "run_completed"]


# -- Case 5 (Demo 1 slice): blocked action ---------------------------------


async def test_blocked_step_emits_blocked_and_run_continues():
    spec = make_spec(4)
    orch, driver, c = build(spec=spec, guard=FakeGuard(refuse={"step-1"}))
    state = await orch.run()

    assert state.state is RunState.COMPLETED
    blocked = c.of("blocked")
    assert len(blocked) == 1 and blocked[0].step_id == "step-1"
    assert blocked[0].text, "a blocked event must carry a narratable reason"
    statuses = {r.step_id: r.status for r in state.records}
    assert statuses["step-1"] is StepStatus.BLOCKED
    assert statuses["step-2"] is StepStatus.DONE


# -- Demo 2 forward compatibility -----------------------------------------


async def test_submit_user_message_never_blocks():
    orch, _, _ = build()
    task = asyncio.create_task(orch.run())
    orch.submit_user_message("wait, what does that do?")  # must not raise or await
    await task
    assert orch.state.cursor == 5, "v0 parks messages; the cursor is untouched"
