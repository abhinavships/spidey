"""app/server.py — M11. FastAPI + one WebSocket that speaks contracts.md §6.

Usage:
    uvicorn app.server:app --reload      # then open http://localhost:8000
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.browser.driver import Driver
from app.drone import router as drone_router
from app.browser.resolver import Resolver
from app.runtime.narrator import Narrator
from app.teach.compiler import compile_workflow
from app.teach.rehearser import rehearse
from app.runtime.orchestrator import Orchestrator
from app.safety.guard import Guard
from app.schemas import CompileError, Event, WorkflowNotFound, WorkflowSpec
from app.store import list_workflows, load_workflow, save_workflow

log = structlog.get_logger(__name__)

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
CDP_ENDPOINT = os.getenv("WA_CDP", "http://localhost:9222")
# Headless is the default: nothing opens on the desktop and the run is watched
# through the in-app preview. Set WA_CDP_ATTACH=1 to drive a real Chrome instead
# (needed only when a workflow depends on a browser profile you logged into).
ATTACH_TO_CHROME = os.getenv("WA_CDP_ATTACH", "0") == "1"
CDP = CDP_ENDPOINT if ATTACH_TO_CHROME else None
FRAME_INTERVAL_S = float(os.getenv("WA_FRAME_INTERVAL_S", "1.0"))
# How long teaching waits for a human to answer a clarifying question.
TEACH_ANSWER_TIMEOUT_S = float(os.getenv("WA_TEACH_ANSWER_TIMEOUT", "180"))
# How many rounds of clarification the compiler gets before teaching gives up.
MAX_COMPILE_ROUNDS = 3

app = FastAPI(title="Live Walkthrough Agent")
if UI_DIR.exists():
    app.mount("/static", StaticFiles(directory=UI_DIR), name="static")
    app.mount("/drone-static", StaticFiles(directory=UI_DIR / "drone"), name="drone-static")
# The site the agent drives: SkyLoop, served from this same process.
app.include_router(drone_router)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_orchestrator(spec: WorkflowSpec, session_id: str, emit, driver: Driver):
    """Assemble the real runtime. Monkeypatched in tests.

    Usage:
        orch = build_orchestrator(spec, "s1", emit, driver)
    """
    return Orchestrator(
        spec=spec, driver=driver, resolver=Resolver(), narrator=Narrator(),
        guard=Guard([spec.target_domain]), emit=emit, session_id=session_id,
    )


@app.get("/api/workflows/{workflow_id}")
async def api_workflow(workflow_id: str):
    """One workflow's steps, so the UI can show the plan before the run starts."""
    try:
        spec = await load_workflow(workflow_id)
    except WorkflowNotFound:
        return JSONResponse({"error": "no such workflow"}, status_code=404)
    return {"id": spec.id, "title": spec.title, "entry_url": spec.entry_url,
            "steps": [{"id": step.id, "intent": step.intent, "action": step.action.value,
                       "risk": step.risk.value} for step in spec.steps]}


@app.get("/")
async def index():
    """Serve the single-page UI."""
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/workflows")
async def api_workflows():
    """List saved workflows for the picker."""
    return JSONResponse([w.model_dump(mode="json") for w in await list_workflows()])


@app.websocket("/ws/{session_id}")
async def ws(sock: WebSocket, session_id: str) -> None:
    """One session: start_run and user_message in, Events out."""
    expected_token = os.getenv("WA_DEMO_TOKEN")
    supplied_token = sock.query_params.get("token", "")
    if expected_token and not secrets.compare_digest(supplied_token, expected_token):
        await sock.close(code=1008, reason="invalid demo token")
        return
    await sock.accept()
    lock = asyncio.Lock()

    async def emit(event: Event) -> None:
        async with lock:
            try:
                await sock.send_text(event.model_dump_json())
            except Exception as exc:  # noqa: BLE001 - client may have vanished
                log.warning("emit_failed", error=str(exc))

    async def error(text: str) -> None:
        await emit(Event(type="error", session_id=session_id, ts=_now(), text=text))

    async def stream_frames(active_driver: Driver) -> None:
        """Send lightweight screenshot frames so public links show the driven Chrome."""
        path = Path("/tmp") / f"walkthrough-{session_id}.png"
        interval = max(FRAME_INTERVAL_S, 0.25)
        while True:
            try:
                await active_driver.screenshot(str(path))
                src = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
                await emit(Event(type="frame", session_id=session_id, ts=_now(), data={"src": src}))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # frames are nice-to-have; never break a run
                log.warning("frame_stream_failed", error=str(exc))
            await asyncio.sleep(interval)

    driver: Driver | None = None
    orch: Orchestrator | None = None
    task: asyncio.Task | None = None
    # Set while teaching is blocked on a question; the next chat message answers it.
    pending: asyncio.Future[str] | None = None

    async def run(workflow_id: str) -> None:
        nonlocal driver, orch
        try:
            spec = await load_workflow(workflow_id)
        except WorkflowNotFound as exc:
            await error(str(exc))
            return
        driver = Driver()
        try:
            await driver.start(spec.entry_url, cdp_endpoint=CDP)
        except RuntimeError as exc:
            await error(str(exc))
            driver = None
            return
        frame_task = asyncio.create_task(stream_frames(driver))
        orch = build_orchestrator(spec, session_id, emit, driver)
        try:
            await orch.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - boundary: report, don't crash the socket
            log.exception("run_crashed")
            await error(f"the run stopped unexpectedly: {exc}")
        finally:
            frame_task.cancel()
            try:
                await frame_task
            except asyncio.CancelledError:
                pass
            if driver:
                await driver.stop()
                driver = None

    async def ask(question: str) -> str | None:
        """Put one teaching question to the human and wait for their chat reply."""
        nonlocal pending
        await emit(Event(type="teach_question", session_id=session_id, ts=_now(), text=question))
        pending = asyncio.get_running_loop().create_future()
        try:
            return await asyncio.wait_for(pending, TEACH_ANSWER_TIMEOUT_S)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return None
        finally:
            pending = None

    async def teach(utterance: str, site_hint: str) -> None:
        """Compile and rehearse a reversible workflow in the attached live Chrome."""
        draft = None
        for _ in range(MAX_COMPILE_ROUNDS):
            try:
                draft = await compile_workflow(utterance, site_hint)
            except CompileError as exc:
                await error(f"Teaching needs clarification: {exc}")
                return
            if not draft.ambiguities:
                break
            reply = await ask("Teaching needs clarification: " + " ".join(draft.ambiguities))
            if not reply or not reply.strip():
                await error("Teaching stopped: that question went unanswered.")
                return
            # Keep the original wording; the answer is added, never substituted,
            # so a clarification cannot silently drop half the request.
            utterance = f"{utterance}\n\nClarification: {reply.strip()}"
        if draft is None or draft.ambiguities:
            await error("Teaching stopped: the description is still ambiguous after clarifying.")
            return
        teach_driver = Driver()
        frame_task: asyncio.Task | None = None
        try:
            await teach_driver.start(draft.entry_url, cdp_endpoint=CDP)
            frame_task = asyncio.create_task(stream_frames(teach_driver))
            spec = await rehearse(draft, teach_driver, Resolver(), Guard([draft.target_domain]),
                                  emit, ask)
            if not spec.rehearsal_passed:
                return
            spec = spec.model_copy(update={"source_utterance": utterance})
            await save_workflow(spec)
        except Exception as exc:  # noqa: BLE001 - teaching errors are shown in the live chat
            log.exception("teach_crashed")
            await error(f"Teaching stopped unexpectedly: {exc}")
        finally:
            if frame_task:
                frame_task.cancel()
                try:
                    await frame_task
                except asyncio.CancelledError:
                    pass
            await teach_driver.stop()

    try:
        while True:
            raw = await sock.receive_text()
            try:
                msg = json.loads(raw)
                kind = msg["kind"]
            except (json.JSONDecodeError, KeyError, TypeError):
                await error("I couldn't read that message.")
                continue

            if kind == "start_run":
                if task and not task.done():
                    await error("a run is already in progress")
                    continue
                task = asyncio.create_task(run(msg.get("workflow_id", "")))
            elif kind == "teach_workflow":
                if task and not task.done():
                    await error("a run or teaching session is already in progress")
                    continue
                task = asyncio.create_task(teach(msg.get("utterance", ""), msg.get("site_hint", "")))
            elif kind == "user_message":
                if pending is not None and not pending.done():
                    pending.set_result(msg.get("text", ""))
                elif orch is None:
                    await error("nothing is running yet")
                else:
                    orch.submit_user_message(msg.get("text", ""))
            elif kind == "pause_control":
                if orch is None:
                    await error("nothing is running yet")
                    continue
                action = msg.get("action")
                if action == "pause":
                    orch.request_pause()
                elif action == "resume":
                    orch.request_resume()
                elif action == "stop":
                    orch.request_stop()
                else:
                    await error(f"unknown pause action {action!r}")
            else:
                await error(f"unknown message kind {kind!r}")
    except WebSocketDisconnect:
        log.info("client_disconnected", session_id=session_id)
    finally:
        if task and not task.done():
            task.cancel()
        if driver:
            await driver.stop()
