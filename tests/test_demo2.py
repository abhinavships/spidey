"""Demo 2 interruption behavior."""

from __future__ import annotations

from app.runtime.interrupt import Interrupter, classify
from app.runtime.orchestrator import Orchestrator
from app.runtime.qa import QA
from app.schemas import InterruptDecision, InterruptKind, RunState, StepStatus
from tests.fakes import (
    EventCollector,
    FakeDriver,
    FakeGuard,
    FakeLLM,
    FakeNarrator,
    FakeQA,
    FakeResolver,
    make_snapshot,
    make_spec,
)


class ScriptedInterrupter:
    def __init__(self, decisions: list[InterruptDecision]) -> None:
        self.decisions = list(decisions)

    async def classify(self, text, spec, cursor):
        return self.decisions.pop(0)


def decision(kind: InterruptKind, target: int | None = None) -> InterruptDecision:
    return InterruptDecision(kind=kind, target_step_index=target, rationale="scripted")


def build(*, interrupter=None, qa=None):
    driver = FakeDriver([make_snapshot("thing 0")])
    collector = EventCollector()
    orch = Orchestrator(
        make_spec(5), driver, FakeResolver(), FakeNarrator(), FakeGuard(), collector.emit,
        interrupter=interrupter, qa=qa, session_id="demo2",
    )
    return orch, driver, collector


async def test_one_question_preserves_cursor_and_runs_every_step_once():
    orch, driver, collector = build(interrupter=ScriptedInterrupter([decision(InterruptKind.QUESTION)]), qa=FakeQA())
    orch.submit_user_message("what does that button do?")
    state = await orch.run()

    assert state.cursor == 5
    assert driver.calls == [f"step-{i}" for i in range(5)]
    assert [event.type for event in collector.events[1:4]] == ["interrupt_received", "answer", "resumed"]


async def test_three_questions_are_all_answered_before_execution_resumes():
    orch, driver, collector = build(
        interrupter=ScriptedInterrupter([decision(InterruptKind.QUESTION)] * 3), qa=FakeQA()
    )
    for _ in range(3):
        orch.submit_user_message("what is thing 0?")
    state = await orch.run()

    assert state.state is RunState.COMPLETED
    assert state.cursor == 5
    assert len(collector.of("answer")) == 3
    assert driver.calls == [f"step-{i}" for i in range(5)]


async def test_question_arriving_during_an_action_resumes_the_same_step():
    class InterruptingDriver(FakeDriver):
        orch = None

        async def act(self, step, resolver=None, guard=None):
            result = await super().act(step, resolver, guard)
            if step.id == "step-0":
                self.orch.submit_user_message("what is thing 0?")
            return result

    driver = InterruptingDriver([make_snapshot("thing 0")])
    collector = EventCollector()
    orch = Orchestrator(make_spec(2), driver, FakeResolver(), FakeNarrator(), FakeGuard(), collector.emit,
                        interrupter=ScriptedInterrupter([decision(InterruptKind.QUESTION)]), qa=FakeQA())
    driver.orch = orch
    await orch.run()

    assert collector.of("resumed")[0].cursor == 0


async def test_out_of_scope_qa_declines_to_guess():
    answer = await QA(llm=FakeLLM([{"text": "unknown", "grounded": False, "sources": []}]).complete).answer(
        "what's your pricing?", make_spec(5), 0, make_snapshot("thing 0")
    )
    assert not answer.grounded
    assert "out of scope" in answer.text.lower()
    assert "can't say" in answer.text.lower() or "don't know" in answer.text.lower()


async def test_skip_marks_current_step_skipped_then_executes_remaining_steps():
    orch, driver, _ = build(interrupter=ScriptedInterrupter([decision(InterruptKind.SKIP)]), qa=FakeQA())
    orch._state.cursor = 2
    orch.submit_user_message("skip this part")
    state = await orch.run()

    statuses = {record.step_id: record.status for record in state.records}
    assert statuses["step-2"] is StepStatus.SKIPPED
    assert driver.calls == ["step-3", "step-4"]
    assert state.cursor == 5


async def test_jump_marks_intermediate_steps_skipped_not_done():
    orch, driver, _ = build(interrupter=ScriptedInterrupter([decision(InterruptKind.JUMP, 4)]), qa=FakeQA())
    orch.submit_user_message("jump to the end")
    state = await orch.run()

    statuses = {record.step_id: record.status for record in state.records}
    assert statuses["step-0"] is StepStatus.SKIPPED
    assert statuses["step-1"] is StepStatus.SKIPPED
    assert statuses["step-2"] is StepStatus.SKIPPED
    assert statuses["step-3"] is StepStatus.SKIPPED
    assert driver.calls == ["step-4"]


async def test_invalid_jump_is_unclear_and_does_not_move_cursor():
    orch, driver, collector = build(interrupter=ScriptedInterrupter([decision(InterruptKind.JUMP, 99)]), qa=FakeQA())
    orch.submit_user_message("jump way ahead")
    state = await orch.run()

    assert state.cursor == 5
    assert driver.calls == [f"step-{i}" for i in range(5)]
    assert "clar" in (collector.of("answer")[0].text or "").lower()


async def test_stop_aborts_before_any_step_runs():
    orch, driver, _ = build(interrupter=ScriptedInterrupter([decision(InterruptKind.STOP)]), qa=FakeQA())
    orch.submit_user_message("ok stop")
    state = await orch.run()

    assert state.state is RunState.ABORTED
    assert driver.calls == []


async def test_pause_during_action_finishes_current_step_then_waits_for_resume():
    class InterruptingDriver(FakeDriver):
        orch = None

        async def act(self, step, resolver=None, guard=None):
            result = await super().act(step, resolver, guard)
            if step.id == "step-0":
                self.orch.submit_user_message("wait")
                self.orch.submit_user_message("resume")
            return result

    driver = InterruptingDriver([make_snapshot("thing 0"), make_snapshot("thing 1")])
    collector = EventCollector()
    orch = Orchestrator(make_spec(2), driver, FakeResolver(), FakeNarrator(), FakeGuard(), collector.emit,
                        interrupter=ScriptedInterrupter([decision(InterruptKind.PAUSE)]), qa=FakeQA())
    driver.orch = orch
    state = await orch.run()

    # The current step's action already finished when "wait" arrived, so it
    # still completes and the cursor still advances -- pause only blocks the *next* step.
    assert driver.calls == ["step-0", "step-1"]
    assert {r.step_id: r.status for r in state.records}["step-0"] is StepStatus.DONE
    assert state.state is RunState.COMPLETED
    assert collector.of("paused")
    assert collector.of("resumed")


async def test_pause_then_no_aborts_the_run():
    orch, driver, _ = build(interrupter=ScriptedInterrupter([decision(InterruptKind.PAUSE)]), qa=FakeQA())
    orch.submit_user_message("wait")
    orch.submit_user_message("no")
    state = await orch.run()

    assert state.state is RunState.ABORTED
    assert driver.calls == []


async def test_request_pause_and_resume_bypass_the_classifier():
    """Deterministic UI-button controls must never go through the LLM classifier."""
    orch, driver, collector = build(interrupter=ScriptedInterrupter([]), qa=FakeQA())
    orch.request_pause()
    orch.request_resume()
    state = await orch.run()

    assert state.state is RunState.COMPLETED
    assert state.cursor == 5
    assert collector.of("paused")
    assert collector.of("resumed")


async def test_request_pause_then_request_stop_aborts():
    orch, driver, _ = build(interrupter=ScriptedInterrupter([]), qa=FakeQA())
    orch.request_pause()
    orch.request_stop()
    state = await orch.run()

    assert state.state is RunState.ABORTED


async def test_classifier_table_and_empty_input_avoids_model_call():
    assert (await classify("", make_spec(5), 0)).kind is InterruptKind.UNCLEAR
    llm = FakeLLM([
        {"kind": "question", "rationale": "asks about the page"},
        {"kind": "skip", "rationale": "changes the plan"},
        {"kind": "stop", "rationale": "ends the run"},
        {"kind": "repeat", "rationale": "repeats the action"},
        {"kind": "pause", "rationale": "asks to hold on"},
    ])
    interrupter = Interrupter(llm=llm.complete)
    assert (await interrupter.classify("what does that button do?", make_spec(5), 0)).kind is InterruptKind.QUESTION
    assert (await interrupter.classify("skip this part", make_spec(5), 0)).kind is InterruptKind.SKIP
    assert (await interrupter.classify("ok stop", make_spec(5), 0)).kind is InterruptKind.STOP
    assert (await interrupter.classify("can you do that again", make_spec(5), 0)).kind is InterruptKind.REPEAT
    assert (await interrupter.classify("wait", make_spec(5), 0)).kind is InterruptKind.PAUSE
    assert "out of scope for this demo" in " ".join(llm.prompts[0][0].split())


async def test_malformed_model_output_falls_back_without_raising():
    interruption = await Interrupter(llm=FakeLLM(["not json"]).complete).classify("hello", make_spec(5), 0)
    answer = await QA(llm=FakeLLM(["not json"]).complete).answer("hello", make_spec(5), 0, make_snapshot())
    assert interruption.kind is InterruptKind.UNCLEAR
    assert not answer.grounded


async def test_qa_sends_only_the_evidence_the_question_is_about():
    """Retrieval, not the whole walkthrough, is what reaches the model."""
    import json as _json

    from app.schemas import StepKnowledge
    from datetime import datetime, timezone

    spec = make_spec(2)
    pages = {
        spec.steps[0].id: "Issues list. New issue button. Milestones.",
        spec.steps[1].id: "Apply labels to this issue: bug, enhancement, question.",
    }
    spec = spec.model_copy(update={"steps": [
        step.model_copy(update={"knowledge": StepKnowledge(
            page_url="https://github.com/acme/demo", page_title="demo",
            a11y_digest="", visible_text=pages[step.id],
            observed_effect="done", captured_at=datetime.now(timezone.utc))})
        for step in spec.steps
    ]})

    seen = {}

    async def spy(system, user, json_schema=None, max_tokens=0):
        seen["user"] = _json.loads(user)
        return {"text": "Labels categorise the issue.", "grounded": True,
                "sources": [spec.steps[1].id]}

    answer = await QA(llm=spy).answer("which labels can I apply", spec, 1, make_snapshot())

    sources = {chunk["source"] for chunk in seen["user"]["evidence"]}
    assert spec.steps[1].id in sources
    assert spec.steps[0].id not in sources, sources
    assert answer.grounded


async def test_qa_declines_when_nothing_retrieved_matches_the_question():
    """No overlapping evidence means no model call and no invented answer."""
    called = False

    async def never(*args, **kwargs):
        nonlocal called
        called = True
        return {"text": "sure", "grounded": True, "sources": ["live_page"]}

    answer = await QA(llm=never).answer("what are your enterprise pricing tiers", make_spec(2), 0, make_snapshot())

    assert not called
    assert not answer.grounded
