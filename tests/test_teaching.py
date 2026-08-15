"""Teaching pipeline: compile plain language, rehearse live, freeze a spec."""

from __future__ import annotations

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
