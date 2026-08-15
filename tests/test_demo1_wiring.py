"""tests/test_demo1_wiring.py — proves the real modules wire together.

Real Guard, real Narrator (no API key -> local fallback), real Store, real
Orchestrator, FakeDriver in place of Chrome.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.store as store
from app.runtime.narrator import Narrator
from app.runtime.orchestrator import Orchestrator
from app.safety.guard import Guard
from app.schemas import ActionType, Locator, LocatorBundle, RiskLevel, RunState, Step, StepStatus
from tests.fakes import EventCollector, FakeDriver, FakeResolver, make_snapshot, make_spec


def _step(sid: str, intent: str, action=ActionType.CLICK, value=None) -> Step:
    return Step(id=sid, index=0, action=action, intent=intent, value=value,
                narration_hint=intent,
                locator=None if action is ActionType.NAVIGATE else LocatorBundle(
                    intent=intent, candidates=[Locator(strategy="text", value=intent)]))


# -- Guard -----------------------------------------------------------------


def test_guard_allows_the_demo_ending_button():
    g = Guard(["github.com"])
    v = g.check(_step("submit", "submit new issue"), make_snapshot(url="https://github.com/x"))
    assert v.allowed and v.risk is RiskLevel.CAUTION and v.reason


@pytest.mark.parametrize("intent", [
    "delete repository", "empty trash", "transfer ownership"])
def test_guard_blocks_destructive(intent):
    v = Guard(["github.com"]).check(_step("x", intent), make_snapshot(url="https://github.com/x"))
    assert not v.allowed and v.reason


@pytest.mark.parametrize("intent", [
    "merge pull request", "close issue", "send the email", "buy now"])
def test_guard_allows_trimmed_generic_phrases(intent):
    """These were removed from DESTRUCTIVE_PHRASES: too broad, false-positived on normal steps."""
    v = Guard(["github.com"]).check(_step("x", intent), make_snapshot(url="https://github.com/x"))
    assert v.allowed, f"{intent!r} should no longer be blocked"


@pytest.mark.parametrize("intent", ["resend the invite", "view closed issues", "undelete it"])
def test_guard_word_boundaries(intent):
    v = Guard(["github.com"]).check(_step("x", intent), make_snapshot(url="https://github.com/x"))
    assert v.allowed, f"{intent!r} tripped a rule it should not have"


def test_guard_blocks_off_allowlist_navigation():
    s = _step("nav", "go elsewhere", ActionType.NAVIGATE, "https://evil.io/x")
    assert not Guard(["github.com"]).check(s, make_snapshot()).allowed


def test_guard_allows_subdomains_not_lookalikes():
    g = Guard(["github.com"])
    ok = _step("n1", "go", ActionType.NAVIGATE, "https://www.github.com/a")
    bad = _step("n2", "go", ActionType.NAVIGATE, "https://github.com.evil.io/a")
    assert g.check(ok, make_snapshot()).allowed
    assert not g.check(bad, make_snapshot()).allowed


# -- Narrator --------------------------------------------------------------


async def test_narrator_is_grounded_and_not_canned():
    n = Narrator(llm=None)
    step = make_spec(1).steps[0]
    a = await n.narrate(step, make_snapshot("Issues tab open"))
    b = await n.narrate(step, make_snapshot("Labels dropdown showing bug"))
    assert a and b and a != b, "same step, different pages, must differ"
    assert len(a) <= 300


async def test_narrator_survives_a_broken_model():
    async def boom(*a, **k):
        raise RuntimeError("model down")
    line = await Narrator(llm=boom).narrate(make_spec(1).steps[0], make_snapshot("hi"))
    assert line


async def test_resume_line_numbers():
    line = await Narrator(llm=None).resume_line(make_spec(5).steps[2], 2, 5)
    assert "3 of 5" in line


# -- Store -----------------------------------------------------------------


async def test_store_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "WORKFLOWS_DIR", tmp_path)
    spec = make_spec(3)
    await store.save_workflow(spec)
    assert await store.load_workflow(spec.id) == spec
    assert not list(tmp_path.glob("*.tmp"))
    v2 = spec.model_copy(update={"version": 2})
    await store.save_workflow(v2)
    assert (await store.load_workflow(spec.id)).version == 2
    rows = await store.list_workflows()
    assert len(rows) == 1 and rows[0].version == 2


async def test_store_ignores_junk_and_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "WORKFLOWS_DIR", tmp_path)
    (tmp_path / "notes.md").write_text("hi")
    assert await store.list_workflows() == []
    with pytest.raises(store.WorkflowNotFound):
        await store.load_workflow("nope")


async def test_shipped_demo_spec_loads():
    """The hardcoded Demo 1 spec on disk must be valid."""
    spec = await store.load_workflow("gh-issue")
    assert spec.rehearsal_passed and len(spec.steps) == 10


async def test_issue_demo_opens_the_issues_list_before_clicking_new_issue():
    """New issue is a control on /issues, not on the repository landing page."""
    spec = await store.load_workflow("gh-issue")
    assert spec.entry_url.endswith("/issues")
    assert spec.steps[0].value == spec.entry_url
    assert spec.steps[1].id == "new-issue"


async def test_issue_demo_submits_and_confirms_the_created_issue():
    spec = await store.load_workflow("gh-issue")
    ids = [step.id for step in spec.steps]
    assert ids[-2:] == ["submit", "assert-created"]
    submit = spec.steps[-2]
    assert submit.action is ActionType.CLICK
    assert submit.risk is RiskLevel.CAUTION
    assert "submit" in submit.intent.lower() or "create" in submit.intent.lower()


# -- Full stack on fakes ---------------------------------------------------


async def test_real_guard_and_narrator_drive_a_clean_run():
    spec = make_spec(4)
    c = EventCollector()
    orch = Orchestrator(spec=spec, driver=FakeDriver([make_snapshot(f"p{i}") for i in range(9)]),
                        resolver=FakeResolver(), narrator=Narrator(llm=None),
                        guard=Guard(["github.com"]), emit=c.emit, session_id="s")
    state = await orch.run()
    assert state.state is RunState.COMPLETED
    assert all(r.status is StepStatus.DONE for r in state.records)
    assert len(c.of("narration")) == 4


async def test_narration_events_are_text_only_for_browser_native_voice():
    spec = make_spec(2)
    c = EventCollector()
    orch = Orchestrator(spec=spec, driver=FakeDriver([make_snapshot("p")]),
                        resolver=FakeResolver(), narrator=Narrator(llm=None),
                        guard=Guard(["github.com"]), emit=c.emit, session_id="s")
    await orch.run()
    narrations = c.of("narration")
    assert narrations and all("audio" not in e.data for e in narrations)


async def test_real_guard_blocks_and_the_run_continues():
    spec = make_spec(3)
    spec.steps[1].intent = "delete the repository"
    c = EventCollector()
    orch = Orchestrator(spec=spec, driver=FakeDriver([make_snapshot("p")]),
                        resolver=FakeResolver(), narrator=Narrator(llm=None),
                        guard=Guard(["github.com"]), emit=c.emit, session_id="s")
    state = await orch.run()
    assert state.state is RunState.COMPLETED
    assert len(c.of("blocked")) == 1
    assert [r.status for r in state.records] == [
        StepStatus.DONE, StepStatus.BLOCKED, StepStatus.DONE]


# -- Server ----------------------------------------------------------------


def test_server_serves_ui_and_workflow_list():
    from app.server import app as fastapi_app
    client = TestClient(fastapi_app)
    assert client.get("/").status_code == 200
    rows = client.get("/api/workflows").json()
    assert any(r["id"] == "gh-issue" for r in rows)


def test_ws_rejects_junk_but_stays_open(monkeypatch):
    monkeypatch.delenv("WA_DEMO_TOKEN", raising=False)
    from app.server import app as fastapi_app
    with TestClient(fastapi_app).websocket_connect("/ws/test") as sock:
        sock.send_text("not json")
        assert sock.receive_json()["type"] == "error"
        sock.send_text('{"kind": "nonsense"}')
        assert sock.receive_json()["type"] == "error"
        sock.send_text('{"kind": "user_message", "text": "hi"}')
        assert sock.receive_json()["type"] == "error"  # nothing running yet


def test_ws_requires_demo_token_when_configured(monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    from app.server import app as fastapi_app

    monkeypatch.setenv("WA_DEMO_TOKEN", "private-demo-token")
    with TestClient(fastapi_app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/test"):
                pass
        with client.websocket_connect("/ws/test?token=private-demo-token") as sock:
            sock.send_text("not json")
            assert sock.receive_json()["type"] == "error"
