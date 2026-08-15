"""app/store.py — M10. Workflow persistence on plain JSON files.

Usage:
    path = await save_workflow(spec)
    spec = await load_workflow("gh-issue")
    rows = await list_workflows()
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import structlog

from app.schemas import WorkflowNotFound, WorkflowSpec, WorkflowSummary

log = structlog.get_logger(__name__)

WORKFLOWS_DIR = Path(os.getenv("WA_WORKFLOWS_DIR", "workflows"))
_NAME = re.compile(r"^(?P<id>.+)\.v(?P<version>\d+)\.json$")


def _versions(workflow_id: str) -> dict[int, Path]:
    out: dict[int, Path] = {}
    if not WORKFLOWS_DIR.exists():
        return out
    for p in WORKFLOWS_DIR.iterdir():
        m = _NAME.match(p.name)
        if m and m.group("id") == workflow_id:
            out[int(m.group("version"))] = p
    return out


def _save_sync(spec: WorkflowSpec) -> str:
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    final = WORKFLOWS_DIR / f"{spec.id}.v{spec.version}.json"
    tmp = final.with_suffix(".json.tmp")
    tmp.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, final)
    return str(final)


async def save_workflow(spec: WorkflowSpec) -> str:
    """Write a spec atomically and return the path.

    Usage:
        await save_workflow(spec)
    """
    path = await asyncio.to_thread(_save_sync, spec)
    log.info("workflow_saved", path=path)
    return path


async def load_workflow(workflow_id: str, version: int | None = None) -> WorkflowSpec:
    """Load a spec. With no version, returns the highest on disk.

    Usage:
        spec = await load_workflow("gh-issue")
    """
    found = await asyncio.to_thread(_versions, workflow_id)
    if not found:
        raise WorkflowNotFound(f"no workflow {workflow_id!r} in {WORKFLOWS_DIR}")
    pick = max(found) if version is None else version
    if pick not in found:
        raise WorkflowNotFound(f"workflow {workflow_id!r} has no version {pick}")
    raw = await asyncio.to_thread(found[pick].read_text, "utf-8")
    try:
        return WorkflowSpec.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001 - boundary: corrupt file is "not found"
        raise WorkflowNotFound(f"{found[pick]} is not a valid WorkflowSpec: {exc}") from exc


async def list_workflows() -> list[WorkflowSummary]:
    """One row per workflow id, at its highest version. Never raises.

    Usage:
        rows = await list_workflows()
    """
    if not WORKFLOWS_DIR.exists():
        return []
    best: dict[str, int] = {}
    for p in await asyncio.to_thread(lambda: list(WORKFLOWS_DIR.iterdir())):
        m = _NAME.match(p.name)
        if m:
            wid, v = m.group("id"), int(m.group("version"))
            best[wid] = max(v, best.get(wid, 0))

    rows: list[WorkflowSummary] = []
    for wid in sorted(best):
        try:
            spec = await load_workflow(wid, best[wid])
        except WorkflowNotFound:
            log.warning("skipping_unreadable_workflow", id=wid)
            continue
        rows.append(WorkflowSummary(id=spec.id, version=spec.version, title=spec.title,
                                    target_domain=spec.target_domain,
                                    n_steps=len(spec.steps),
                                    rehearsal_passed=spec.rehearsal_passed))
    return rows
