"""tests/test_schemas.py — M0. Enum strings are asserted literally on purpose:
a typo here silently breaks every delegate."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas import (
    ActionType,
    Event,
    InterruptKind,
    Locator,
    LocatorBundle,
    RiskLevel,
    RunState,
    SessionState,
    Step,
    StepKnowledge,
    StepStatus,
    WorkflowSpec,
)
from tests.fakes import make_spec


def _now():
    return datetime.now(timezone.utc)


def test_enum_values_match_contracts():
    assert [e.value for e in ActionType] == [
        "navigate", "click", "type", "select", "scroll", "wait_for", "assert"]
    assert [e.value for e in RiskLevel] == ["safe", "caution", "destructive"]
    assert [e.value for e in StepStatus] == [
        "pending", "running", "done", "failed", "skipped", "blocked"]
    assert [e.value for e in RunState] == [
        "idle", "running", "interrupted", "completed", "aborted"]
    assert [e.value for e in InterruptKind] == [
        "question", "skip", "jump", "repeat", "stop", "pause", "unclear"]


@pytest.mark.parametrize("model", ["spec", "session", "event", "knowledge"])
def test_round_trip(model):
    made = {
        "spec": make_spec(3),
        "session": SessionState(session_id="s", workflow_id="w", cursor=0,
                                state=RunState.IDLE, records=[]),
        "event": Event(type="narration", session_id="s", ts=_now(), text="hi"),
        "knowledge": StepKnowledge(page_url="u", page_title="t", observed_effect="e",
                                   captured_at=_now()),
    }[model]
    assert type(made).model_validate(json.loads(made.model_dump_json())) == made


def test_empty_steps_is_valid():
    spec = WorkflowSpec(id="x", title="t", target_domain="github.com",
                        entry_url="https://github.com/", steps=[], taught_at=_now())
    assert spec.steps == []


def test_non_navigate_step_without_locator_raises():
    with pytest.raises(ValidationError):
        Step(id="s", index=0, action=ActionType.CLICK, intent="click it")


def test_navigate_step_needs_a_url():
    with pytest.raises(ValidationError):
        Step(id="s", index=0, action=ActionType.NAVIGATE, intent="go")
    assert Step(id="s", index=0, action=ActionType.NAVIGATE, intent="go",
                value="https://github.com/").locator is None


def test_empty_candidates_raises():
    with pytest.raises(ValidationError):
        LocatorBundle(intent="the gear", candidates=[])


def test_digests_truncate_to_contract_limits():
    k = StepKnowledge(page_url="u", page_title="t", a11y_digest="a" * 9000,
                      visible_text="b" * 9000, observed_effect="e", captured_at=_now())
    assert len(k.a11y_digest) == 4000
    assert len(k.visible_text) == 3000


def test_preference_rank_orders_strategies():
    b = LocatorBundle(intent="i", candidates=[
        Locator(strategy="css", value="div"),
        Locator(strategy="test_id", value="x")])
    assert b.preference_rank() == [5, 0]
