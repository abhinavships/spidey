# plan_unittests.md — tests are the spec

Written before implementation. Each delegate receives `contracts.md` plus their own section below. A module is accepted when its tests are green — we read test output, not code.

`pytest` + `pytest-asyncio`. No real browser, no real model, no network. Everything runs against the fakes.

## Shared fakes — `tests/fakes.py` (Codex writes this first, before any other module)

```python
class FakeDriver:
    """Scriptable Driver. Records every act() call."""
    def __init__(self, snapshots: list[PageSnapshot], fail_on: set[str] = frozenset())
    # act() returns ok=False for step ids in fail_on, ok=True otherwise
    # .calls -> list[str] of step ids acted on, in order

class FakeLLM:
    """Returns queued responses in order. Raises if drained."""
    def __init__(self, responses: list[str | dict])
    # .prompts -> list[tuple[system, user]] for assertion

def make_spec(n_steps: int = 5, with_knowledge: bool = True) -> WorkflowSpec
def make_snapshot(text: str = "", url: str = "https://mail.google.com/") -> PageSnapshot
```

---

## M0 `schemas.py`
- Every model round-trips: `model_validate(json.loads(m.model_dump_json())) == m`
- `WorkflowSpec` with `steps=[]` is valid; with a non-NAVIGATE step and `locator=None` it raises
- `LocatorBundle` with `candidates=[]` raises
- Enum values match `contracts.md` exactly (assert against literal strings — this catches typos that silently break delegates)

## M1 `browser/driver.py`
Runs against Playwright's own bundled test page or a local static HTML fixture in `tests/pages/`. No Gmail in tests.
- `snapshot()` returns non-empty `a11y_digest` and `visible_text`, correct url/title
- `find()` returns a handle for a valid bundle; `None` after `timeout_ms` for a bogus one, and takes ≥ timeout but < timeout+2s
- `act()` on a CLICK step with a good locator → `ok=True`, page state changed, `effect` non-empty
- `act()` on a miss calls `resolver.heal()` **exactly once**, retries, and reports `healed=True` when the heal succeeds
- `act()` on a miss where heal also fails → `ok=False`, `error` set, **no exception escapes**
- `act()` raises `BlockedAction` when the guard refuses, and **the page is not touched** (assert via a spy that no Playwright call was made)
- Candidate order is respected: with two matching candidates, the first is used

## M2 `browser/resolver.py`
- `resolve()` returns ≥1 candidate; strategies come from the allowed set only
- Candidates are ordered per the §2 preference (test_id > role > label > placeholder > text > css)
- `heal()` returns a bundle that differs from the input; increments `heal_count`; sets `healed_at`
- `heal()` returns `None` when the LLM output has no plausible element (FakeLLM returns garbage)
- Malformed LLM JSON → returns `None`, does not raise
- The snapshot is actually in the prompt (assert on `FakeLLM.prompts`)

## M3 `teach/compiler.py`
- Clear input ("open Gmail settings, go to General, add a signature, save") → 3–6 steps, `ambiguities == []`
- Vague input ("show them the signature thing") → `ambiguities` non-empty, still returns a draft
- Every draft step has non-empty `intent` and `narration_hint`
- No draft step carries a locator (locators are rehearsal's job)
- `target_domain` and `entry_url` are consistent with each other
- Any step whose intent implies sending/deleting/purchasing is tagged `RiskLevel.DESTRUCTIVE`
- LLM returns non-JSON → raises `CompileError`, never returns a half-built draft

## M4 `teach/rehearser.py`
- Happy path: every draft step becomes a `Step` with a populated `LocatorBundle` and `StepKnowledge`; `rehearsal_passed=True`
- Step indices are contiguous 0..n-1 and step ids are unique
- One unresolvable step → `rehearsal_passed=False`, emits `teach_question`, **does not raise**
- `StepKnowledge.observed_effect` is non-empty for every DONE step
- Digests are truncated to the contract limits (4000 / 3000)
- `emit` is called with `teach_progress` at least once per step

## M5 `runtime/orchestrator.py` — the critical module
**Cursor invariant:**
- Submit 1 QUESTION mid-run → cursor identical before and after handling; all steps still execute exactly once
- Submit 3 QUESTIONs back to back → 3 answers emitted, cursor unchanged, run completes normally
- Submit 5 QUESTIONs while a step is executing → all 5 drained before the cursor advances

**Cursor movement:**
- SKIP at cursor 2 of 5 → step 2 recorded `SKIPPED`, cursor becomes 3, steps 3–4 run
- JUMP to index 4 → cursor becomes 4, steps between are `SKIPPED`, not `DONE`
- JUMP to an out-of-range index → treated as UNCLEAR, cursor unchanged, clarification emitted
- REPEAT → the same step id appears twice in `driver.calls`, cursor unchanged after the repeat
- STOP → `RunState.ABORTED`, no further steps execute

**Ordering and events:**
- Event order for a clean run: `run_started`, then per step `step_start` → narration → `step_done`, then `run_completed`
- An interruption emits `interrupt_received` → `answer` → `resumed`, in that order, and `resumed` names the correct step index
- A failing step emits `step_failed` and does not advance the cursor
- `submit_user_message` never blocks (assert it returns while a step is mid-execution)
- Empty workflow → completes immediately, no crash

## M6 `runtime/narrator.py`
- `narrate()` output is non-empty, ≤ 300 chars, mentions something from the live snapshot (assert overlap with snapshot text)
- Output differs when the snapshot differs but the step is identical — **this is the anti-canned-script test and it is a scoring criterion**
- `resume_line()` contains the correct step number and total
- LLM timeout → falls back to `narration_hint`, never raises, never emits empty

## M7 `runtime/interrupt.py`
Table-driven, one case per row, all against FakeLLM:

| input | expected kind |
|---|---|
| "what does that button do?" | QUESTION |
| "wait, why did you click there" | QUESTION |
| "skip this part" | SKIP |
| "just show me the saving bit" | JUMP |
| "can you do that again" | REPEAT |
| "ok stop" | STOP |
| "hmm" | UNCLEAR |
| "" | UNCLEAR |

- JUMP returns a `target_step_index` within range, or UNCLEAR if it can't map to a step
- `rationale` is always non-empty
- Malformed LLM output → UNCLEAR, never raises

## M8 `runtime/qa.py`
- Question answerable from `StepKnowledge` → `grounded=True`, `sources` contains the step id
- Question answerable only from the live page → `grounded=True`, `sources` contains `"live_page"`
- Question about something never seen ("what's your pricing?") → `grounded=False` and the text plainly says it doesn't know. **This test is non-negotiable — it is what "using what it actually knows" means.**
- The answer never contains a URL or UI label absent from both knowledge and live snapshot (hallucination canary)
- Answer ≤ 500 chars
- Both current-step knowledge and the live snapshot appear in the prompt

## M9 `safety/guard.py`
- Off-allowlist navigation → `allowed=False`
- Step whose locator intent contains "Send" on a compose screen → `DESTRUCTIVE`, blocked
- "Delete forever" / "Empty trash" / "Buy now" / "Publish" → blocked
- "Save changes" → `CAUTION`, allowed
- "Open settings" → `SAFE`, allowed
- Case-insensitive and substring-safe: "Resend" must NOT trip the "send" rule *(word-boundary matching)*
- Every verdict has a non-empty human-readable `reason` — it gets narrated to the judge

## M10 `store.py`
- Save then load → identical spec
- Save v1 then v2 → both retrievable; `load(id)` with no version returns the highest
- `load()` of a missing id raises `WorkflowNotFound`
- `list_workflows()` on an empty dir returns `[]`
- Filenames match `{id}.v{n}.json`

## M11 `server.py`
- `GET /` returns 200 HTML
- `GET /api/workflows` returns the list from the store
- WS accepts `start_run`, emits `run_started` within 2s
- WS `user_message` mid-run reaches the orchestrator (assert via spy)
- Malformed WS JSON → an `error` event, socket stays open
- Client disconnect mid-run → the browser session is torn down, no orphan process

---

## Integration tests — `tests/test_integration.py` (master chat writes these, not delegates)

1. **Clean run:** FakeDriver, 5-step spec, no interruptions → all steps DONE in order, `run_completed`
2. **One interruption:** the literal must-have. Question at step 3 → answered, `resumed`, steps 3 and 4 still run, cursor never regressed
3. **Consecutive interruptions:** 3 questions during step 2 → 3 answers, run completes
4. **Plan change:** SKIP then a QUESTION then JUMP → final records match the expected status list exactly
5. **Blocked action:** a spec containing a Send step → `blocked` emitted, run continues past it, page never touched
6. **Teach → save → load → run:** full round trip on fakes, proving reuse works

## Smoke test — `tests/smoke_live.py` (manual, real browser, not in CI)
Run against the real dummy account before each demo. Loads the frozen spec, executes it, asserts completion. **Run this the morning of demo day** — it's the canary for overnight DOM drift.
