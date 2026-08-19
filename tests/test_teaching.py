"""Teaching pipeline: compile plain language, rehearse live, freeze a spec."""

from __future__ import annotations

import pytest

from app.schemas import ActionType, Locator, LocatorBundle, RiskLevel
from tests.fakes import EventCollector, FakeDriver, FakeGuard, make_snapshot


class StaticResolver:
    async def resolve(self, intent, snap):
        return LocatorBundle(intent=intent, candidates=[Locator(strategy="text", value="New issue")])


async def test_compile_turns_a_github_description_into_a_safe_draft(monkeypatch):
    from app.teach import compiler

    async def fake_complete(*args, **kwargs):
        return {
            "title": "Open a GitHub issue draft",
            "target_domain": "github.com",
            "entry_url": "https://github.com/acme/demo/issues",
            "steps": [
                {"action": "navigate", "intent": "open the issues list", "value": "https://github.com/acme/demo/issues", "narration_hint": "opening Issues"},
                {"action": "click", "intent": "click New issue", "narration_hint": "opening a draft"},
                {"action": "type", "intent": "type a draft title", "value": "Demo issue draft", "narration_hint": "adding a title"},
            ],
            "ambiguities": [],
        }

    monkeypatch.setattr(compiler, "complete", fake_complete)
    draft = await compiler.compile_workflow("Open Issues and start a new issue, but do not submit it.", "https://github.com/acme/demo/issues")

    assert draft.target_domain == "github.com"
    assert len(draft.steps) == 3
    assert all(step.narration_hint for step in draft.steps)
    assert all(step.risk is not RiskLevel.DESTRUCTIVE for step in draft.steps)


async def test_compile_marks_an_explicit_demo_issue_submission_as_caution(monkeypatch):
    from app.teach import compiler

    async def fake_complete(*args, **kwargs):
        return {
            "title": "Create a demo issue", "target_domain": "github.com",
            "entry_url": "https://github.com/acme/demo/issues", "ambiguities": [],
            "steps": [
                {"action": "navigate", "intent": "open Issues", "value": "https://github.com/acme/demo/issues", "narration_hint": "opening Issues"},
                {"action": "click", "intent": "click New issue", "narration_hint": "opening a new issue"},
                {"action": "type", "intent": "type the demo title", "value": "Demo issue", "narration_hint": "adding the title"},
                {"action": "click", "intent": "submit the demo issue", "narration_hint": "creating the issue"},
            ],
        }

    monkeypatch.setattr(compiler, "complete", fake_complete)
    draft = await compiler.compile_workflow(
        "On my dummy GitHub demo repo, create and submit one test issue.",
        "https://github.com/acme/demo/issues",
    )
    assert draft.steps[-1].risk is RiskLevel.CAUTION


async def test_rehearse_freezes_live_knowledge_for_each_completed_step():
    from app.schemas import DraftStep, DraftWorkflow
    from app.teach.rehearser import rehearse

    draft = DraftWorkflow(
        title="Open issue draft", target_domain="github.com", entry_url="https://github.com/acme/demo/issues",
        steps=[
            DraftStep(action=ActionType.NAVIGATE, intent="open Issues", value="https://github.com/acme/demo/issues", narration_hint="opening Issues"),
            DraftStep(action=ActionType.CLICK, intent="click New issue", narration_hint="opening a draft"),
        ],
    )
    events = EventCollector()
    spec = await rehearse(
        draft, FakeDriver([make_snapshot("Issues"), make_snapshot("New issue")]),
        StaticResolver(), FakeGuard(), events.emit,
    )

    assert spec.rehearsal_passed
    assert len(spec.steps) == 2
    assert all(step.knowledge and step.knowledge.observed_effect for step in spec.steps)
    assert len(events.of("teach_progress")) == 2


def _two_step_draft():
    from app.schemas import DraftStep, DraftWorkflow

    return DraftWorkflow(
        title="Open issue draft", target_domain="github.com",
        entry_url="https://github.com/acme/demo/issues",
        steps=[
            DraftStep(action=ActionType.NAVIGATE, intent="open Issues",
                      value="https://github.com/acme/demo/issues", narration_hint="opening Issues"),
            DraftStep(action=ActionType.CLICK, intent="click New issue", narration_hint="opening a draft"),
        ],
    )


async def test_rehearse_retries_a_failed_step_with_the_humans_clarification():
    from app.teach.rehearser import rehearse

    asked: list[str] = []

    async def ask(question: str) -> str:
        asked.append(question)
        return "press the green New issue button"

    events = EventCollector()
    spec = await rehearse(
        _two_step_draft(),
        FakeDriver([make_snapshot("Issues")], fail_on={"2-click-new-issue"}),
        StaticResolver(), FakeGuard(), events.emit, ask,
    )

    assert len(asked) == 1
    assert spec.rehearsal_passed
    assert [step.intent for step in spec.steps] == ["open Issues", "press the green New issue button"]
    assert len(events.of("teach_question")) == 0


async def test_rehearse_keeps_learned_steps_when_a_clarification_never_comes():
    from app.teach.rehearser import rehearse

    async def ask(question: str) -> None:
        return None

    events = EventCollector()
    spec = await rehearse(
        _two_step_draft(),
        FakeDriver([make_snapshot("Issues")], fail_on={"2-click-new-issue"}),
        StaticResolver(), FakeGuard(), events.emit, ask,
    )

    assert not spec.rehearsal_passed
    assert [step.intent for step in spec.steps] == ["open Issues"]
    assert len(events.of("teach_question")) == 1


async def test_rehearse_gives_up_after_the_clarification_budget():
    from app.teach.rehearser import rehearse
    from app.teach import rehearser

    asked: list[str] = []

    async def ask(question: str) -> str:
        asked.append(question)
        return "click New issue"  # same wording every time, so it keeps failing

    spec = await rehearse(
        _two_step_draft(),
        FakeDriver([make_snapshot("Issues")], fail_on={"2-click-new-issue"}),
        StaticResolver(), FakeGuard(), EventCollector().emit, ask,
    )

    assert not spec.rehearsal_passed
    assert len(asked) == rehearser.MAX_CLARIFICATIONS


async def test_compile_fills_in_a_navigate_step_the_model_left_without_a_url(monkeypatch):
    """A navigate with no value would fail rehearsal; the entry URL is the answer."""
    from app.teach import compiler

    async def fake_complete(*args, **kwargs):
        return {
            "title": "Sign in and read the dashboard",
            "target_domain": "localhost:8000",
            "entry_url": "http://localhost:8000/drone/login",
            "steps": [
                {"action": "navigate", "intent": "open the sign-in page", "value": None,
                 "narration_hint": "opening the login page"},
                {"action": "type", "intent": "type the email address", "value": "vp@skyloop.io",
                 "narration_hint": "entering the email"},
                {"action": "click", "intent": "click Sign in", "narration_hint": "signing in"},
            ],
            "ambiguities": [],
        }

    monkeypatch.setattr(compiler, "complete", fake_complete)
    draft = await compiler.compile_workflow("sign in and read the dashboard",
                                            "http://localhost:8000/drone/login")

    assert draft.steps[0].value == "http://localhost:8000/drone/login"


def test_resolver_guesses_a_label_from_how_a_person_describes_a_field():
    """"the Work email field" is a person describing the label "Work email"."""
    from app.browser.resolver import _guesses

    strategies = {(loc.strategy, loc.value) for loc in _guesses("the Work email field")}
    assert ("label", "Work email") in strategies
    assert ("placeholder", "Work email") in strategies
    assert ("test_id", "work-email") in strategies


async def test_resolver_still_offers_candidates_with_no_model_available(monkeypatch):
    """No API key and no local model must not mean no locator at all."""
    from app.browser import resolver as resolver_module
    from tests.fakes import make_snapshot

    async def no_model(*args, **kwargs):
        return None

    monkeypatch.setattr(resolver_module, "complete", no_model)
    bundle = await resolver_module.Resolver().resolve("the Sign in button", make_snapshot())

    assert bundle.candidates, "a missing model must not produce an empty bundle"
    assert any(c.strategy == "role" and c.name == "Sign in" for c in bundle.candidates)


async def test_a_human_can_drop_a_step_the_page_does_not_have():
    """Compiling from one sentence invents steps; "skip it" is the right answer."""
    from app.teach.rehearser import rehearse

    async def ask(question: str) -> str:
        return "skip that, we're already there"

    events = EventCollector()
    spec = await rehearse(
        _two_step_draft(),
        FakeDriver([make_snapshot("Issues")], fail_on={"2-click-new-issue"}),
        StaticResolver(), FakeGuard(), events.emit, ask,
    )

    assert spec.rehearsal_passed
    assert [step.intent for step in spec.steps] == ["open Issues"]
    assert any("Dropping step 2" in e.text for e in events.of("teach_progress"))


async def test_a_later_navigate_without_a_url_is_refused_not_sent_home(monkeypatch):
    """Filling entry_url into a mid-workflow navigate silently breaks every later step."""
    from app.schemas import CompileError
    from app.teach import compiler

    async def fake_complete(*args, **kwargs):
        return {
            "title": "Sign in then wander off",
            "target_domain": "localhost:8000",
            "entry_url": "http://localhost:8000/drone/login",
            "steps": [
                {"action": "navigate", "intent": "open the sign-in page", "value": None,
                 "narration_hint": "opening the login page"},
                {"action": "click", "intent": "click Sign in", "narration_hint": "signing in"},
                {"action": "navigate", "intent": "open the readiness page", "value": None,
                 "narration_hint": "opening readiness"},
            ],
            "ambiguities": [],
        }

    monkeypatch.setattr(compiler, "complete", fake_complete)
    with pytest.raises(CompileError, match="gives no address"):
        await compiler.compile_workflow("sign in then open readiness",
                                        "http://localhost:8000/drone/login")


async def test_compile_keeps_the_port_the_human_gave_in_the_starting_url(monkeypatch):
    """Models drop ":8000"; the guard and the browser both need it back."""
    from app.teach import compiler

    async def fake_complete(*args, **kwargs):
        return {
            "title": "Sign in and read the dashboard",
            "target_domain": "localhost",
            "entry_url": "http://localhost/drone/login",
            "steps": [
                {"action": "navigate", "intent": "open the sign-in page",
                 "value": "http://localhost:8000/drone/login", "narration_hint": "opening the login page"},
                {"action": "type", "intent": "type the email address", "value": "vp@skyloop.io",
                 "narration_hint": "entering the email"},
                {"action": "click", "intent": "click Sign in", "narration_hint": "signing in"},
            ],
            "ambiguities": [],
        }

    monkeypatch.setattr(compiler, "complete", fake_complete)
    draft = await compiler.compile_workflow("sign in and read the dashboard",
                                            "http://localhost:8000/drone/login")

    assert draft.target_domain == "localhost:8000"
    assert draft.entry_url == "http://localhost:8000/drone/login"


async def test_a_taught_workflow_gets_a_readable_id():
    """The id shows up in the picker, so it should read like a name, not a step."""
    from app.teach.rehearser import rehearse

    spec = await rehearse(_two_step_draft(), FakeDriver([make_snapshot("Issues")]),
                          StaticResolver(), FakeGuard(), EventCollector().emit)

    assert spec.id == "open-issue-draft"
