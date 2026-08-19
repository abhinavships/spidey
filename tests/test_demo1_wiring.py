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


SHIPPED = ("tech-mission-plan", "vp-quarterly-report", "marketing-campaign-draft")


@pytest.mark.parametrize("workflow_id", SHIPPED)
async def test_shipped_specs_load_and_start_at_the_sign_in_page(workflow_id):
    """Every shipped spec is valid and signs in before it touches a workspace."""
    spec = await store.load_workflow(workflow_id)
    assert spec.rehearsal_passed and spec.steps
    assert spec.entry_url.endswith("/drone/login")
    assert spec.steps[0].value == spec.entry_url
    assert [step.id for step in spec.steps[:4]] == [
        "open-login", "type-email", "type-password", "click-signin"]


@pytest.mark.parametrize("workflow_id", SHIPPED)
async def test_no_shipped_step_is_destructive(workflow_id):
    """A demo must not contain an irreversible action in the first place."""
    spec = await store.load_workflow(workflow_id)
    assert all(step.risk is not RiskLevel.DESTRUCTIVE for step in spec.steps)


async def test_the_technical_workflow_clears_preflight_before_saving():
    """Order matters: the engineer checks the aircraft, then commits the draft."""
    spec = await store.load_workflow("tech-mission-plan")
    ids = [step.id for step in spec.steps]
    assert ids.index("run-preflight") < ids.index("save-mission")
    save = spec.steps[ids.index("save-mission")]
    assert save.action is ActionType.CLICK and save.risk is RiskLevel.CAUTION


def test_server_serves_ui_and_workflow_list():
    from app.server import app as fastapi_app
    client = TestClient(fastapi_app)
    assert client.get("/").status_code == 200
    rows = client.get("/api/workflows").json()
    assert {r["id"] for r in rows} >= set(SHIPPED)


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


def test_guard_allows_a_local_site_whose_domain_carries_a_port():
    """A spec names a local site as localhost:8000; a parsed URL host never has the port."""
    from app.safety.guard import Guard
    from app.schemas import Step

    step = Step(id="open", index=0, action=ActionType.NAVIGATE,
                intent="open the sign-in page", value="http://localhost:8000/drone/login",
                narration_hint="opening the login page")
    snap = make_snapshot()
    assert Guard(["localhost:8000"]).check(step, snap).allowed
    assert Guard(["localhost"]).check(step, snap).allowed
    assert not Guard(["example.com"]).check(step, snap).allowed
