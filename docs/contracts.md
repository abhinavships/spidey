# contracts.md — FROZEN INTERFACES — **v2**

**Status: FROZEN as of Demo 1 handoff. Do not change without a version bump and a changelog note at the bottom.**

This file is pasted into EVERY delegation prompt (Hermes / GLM / Codex / Antigravity). No delegate ever sees another delegate's implementation — only this file and their own module spec. If two modules disagree at integration time, this file is right and the code is wrong.

Language: Python 3.11+. Types are Pydantic v2 models. Async throughout.
Browser: **Playwright 1.62.0, CDP attach to a real Chrome on `localhost:9222`.** No Firecrawl.

---

## 0. Project layout

```
walkthrough-agent/
  app/
    schemas.py          # M0 — everything in this document. ALREADY WRITTEN. Do not edit.
    browser/
      driver.py         # M1
      resolver.py       # M2
    teach/
      compiler.py       # M3
      rehearser.py      # M4
    runtime/
      orchestrator.py   # M5
      narrator.py       # M6
      interrupt.py      # M7
      qa.py             # M8
    safety/
      guard.py          # M9
    store.py            # M10
    server.py           # M11 (FastAPI + WebSocket)
    llm.py              # shared model client
  workflows/            # frozen WorkflowSpec JSON lands here
  docs/                 # contracts.md, plan.md, plan_unittests.md
  tests/                # incl. tests/fakes.py, tests/pages/
  ui/                   # M11 static frontend
  launch_browser.sh     # starts Chrome with --remote-debugging-port=9222
```

Rules that apply to every module:
- A module imports ONLY from `app.schemas`, its declared dependencies, and stdlib/third-party. Never sideways into a sibling's internals.
- Every public function is `async def` unless it is pure computation.
- No module prints. Logging via `structlog` only. User-visible text goes through the event bus (§5).
- No module calls an LLM directly. All model calls go through `app.llm.complete()` (§8).
- **No module defines a shared type.** Every model in this document already exists in `app/schemas.py`. Import it; do not redeclare it. (v2 change — see changelog.)

---

## 1. Core enums

```python
class ActionType(str, Enum):
    NAVIGATE = "navigate"     # go to a URL
    CLICK    = "click"
    TYPE     = "type"         # fill a field
    SELECT   = "select"       # dropdown option
    SCROLL   = "scroll"
    WAIT_FOR = "wait_for"     # wait for an element/state, no interaction
    ASSERT   = "assert"       # verify we landed where we expected

class RiskLevel(str, Enum):
    SAFE        = "safe"
    CAUTION     = "caution"      # mutates dummy account state, reversible
    DESTRUCTIVE = "destructive"  # irreversible / real-world side effect — ALWAYS BLOCKED

class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE    = "done"
    FAILED  = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"       # v2: guard refused. Distinct from SKIPPED (user chose)
                              # and FAILED (we tried and couldn't).

class RunState(str, Enum):
    IDLE = "idle"; RUNNING = "running"; INTERRUPTED = "interrupted"
    COMPLETED = "completed"; ABORTED = "aborted"

class InterruptKind(str, Enum):
    QUESTION = "question"   # answer, cursor unchanged
    SKIP     = "skip"       # advance cursor past current step
    JUMP     = "jump"       # move cursor to a named/indexed step
    REPEAT   = "repeat"     # re-run current step
    STOP     = "stop"       # abort run
    UNCLEAR  = "unclear"    # ask the human to clarify
```

`LOCATORLESS_ACTIONS = {NAVIGATE, SCROLL}` — these two, and only these two, may carry `locator=None`. Every other action without a locator is a validation error at construction time.

---

## 2. Locators — the self-heal contract

**Execution policy (locked): saved locator first, LLM self-heal on miss.**

At call time the driver tries each candidate in `LocatorBundle.candidates` in order. If all miss within `timeout_ms`, it hands the current a11y snapshot plus `intent` to the resolver, which returns a fresh candidate. A successful heal is written back to the bundle and emitted as a visible `heal` event.

```python
class Locator(BaseModel):
    strategy: Literal["role", "test_id", "label", "placeholder", "text", "css"]
    value: str
    name: str | None = None      # for strategy == "role": the accessible name
    exact: bool = False

class LocatorBundle(BaseModel):
    intent: str                  # "the settings gear icon, top right"
    candidates: list[Locator]    # ordered, most-preferred first. len >= 1 (enforced)
    healed_at: datetime | None = None
    heal_count: int = 0
    def preference_rank(self) -> list[int]: ...   # helper, index into STRATEGY_PREFERENCE
```

Ordering preference: `test_id` > `role` > `label` > `placeholder` > `text` > `css`. CSS is last-resort and must never be a bare tag or nth-child chain.

**v2 note for GitHub:** GitHub's DOM is `data-testid`-rich and its buttons have good accessible names. Prefer `test_id` then `role`. Never rely on class names — they are hashed and rotate.

---

## 3. Page observation (v2: moved into schemas, shared by M1/M2/M4/M6/M8/M9)

```python
class PageSnapshot(BaseModel):
    url: str
    title: str
    a11y_digest: str = ""     # <= 4000 chars
    visible_text: str = ""    # <= 3000 chars

class ActResult(BaseModel):
    ok: bool
    healed: bool = False
    error: str | None = None
    effect: str = ""          # observed diff summary, one sentence
```

**How `a11y_digest` is produced (v2, LOCKED):** `page.accessibility` is REMOVED in Playwright 1.62. Use:

```python
digest = await page.locator("body").aria_snapshot()   # -> str, YAML-ish a11y tree
```

Truncate to 4000 chars. Never call `page.accessibility.snapshot()` — it does not exist and will `AttributeError` at demo time.

---

## 4. Workflow artifact

```python
class StepKnowledge(BaseModel):
    """Captured during rehearsal. This is what QA is grounded in."""
    page_url: str
    page_title: str
    a11y_digest: str = ""       # auto-truncated to 4000
    visible_text: str = ""      # auto-truncated to 3000
    screenshot_path: str | None = None
    observed_effect: str = ""   # what changed after the action, one sentence
    captured_at: datetime

class Step(BaseModel):
    id: str                   # stable slug, e.g. "open-new-issue"
    index: int                # 0-based position at freeze time
    action: ActionType
    intent: str               # human-readable purpose
    locator: LocatorBundle | None = None    # None only for NAVIGATE / SCROLL
    value: str | None = None                # text for TYPE, option for SELECT, url for NAVIGATE
    narration_hint: str = ""                # seed for the narrator, NOT the final text
    risk: RiskLevel = RiskLevel.SAFE
    knowledge: StepKnowledge | None = None  # populated by rehearsal
    timeout_ms: int = 8000

class WorkflowSpec(BaseModel):
    id: str; version: int = 1
    title: str; description: str = ""
    target_domain: str        # e.g. "github.com" — enforced by the guard
    entry_url: str
    steps: list[Step] = []
    taught_at: datetime
    rehearsal_passed: bool = False   # MUST be True before a spec may run at call time
    source_utterance: str = ""
    def step_by_id(self, step_id: str) -> Step | None: ...

class WorkflowSummary(BaseModel):
    id: str; version: int; title: str
    target_domain: str; n_steps: int; rehearsal_passed: bool
```

Persistence: `workflows/{id}.v{version}.json`, plain `model_dump_json(indent=2)`.

---

## 5. Runtime state and the cursor invariant

```python
class StepRecord(BaseModel):
    step_id: str
    status: StepStatus
    started_at: datetime | None = None
    ended_at: datetime | None = None
    error: str | None = None
    healed: bool = False

class ChatTurn(BaseModel):
    role: Literal["agent", "customer"]
    text: str
    ts: datetime
    during_step: str | None = None

class SessionState(BaseModel):
    session_id: str
    workflow_id: str
    cursor: int = 0
    state: RunState = RunState.IDLE
    records: list[StepRecord] = []
    interrupt_count: int = 0
    transcript: list[ChatTurn] = []
```

> **The cursor invariant — the single most important rule in this project.**
> Handling an interruption of kind `QUESTION` MUST NOT modify `cursor`. Only `SKIP`, `JUMP`, and step completion may modify `cursor`. Any code path that mutates the cursor while answering a question is a bug, regardless of how the demo looks.

Implementation rule (v2): in `orchestrator.py` the cursor is mutated in exactly one private method, `_advance()`. If you need a second one, you are wrong.

---

## 6. Events — the bus between runtime and UI

```python
class Event(BaseModel):
    type: Literal[
        "narration", "step_start", "step_done", "step_failed",
        "heal", "interrupt_received", "answer", "resumed",
        "plan_changed", "blocked",
        "run_started", "run_completed", "run_aborted",
        "teach_progress", "teach_question", "teach_complete",
        "error", "frame",          # v2: "frame" is now in the enum, not an exemption
    ]
    session_id: str
    ts: datetime
    text: str | None = None
    step_id: str | None = None
    cursor: int | None = None
    data: dict = {}

EmitFn = Callable[[Event], Awaitable[None]]     # v2: named type, use it in signatures
```

Inbound from UI is exactly two message shapes:
```json
{"kind": "start_run", "workflow_id": "gh-issue"}
{"kind": "user_message", "text": "wait, what does that toggle do?"}
```

---

## 7. Module interfaces

Each delegate implements exactly these signatures. Nothing more is public.

### M1 `browser/driver.py`
```python
class Driver:
    async def start(self, entry_url: str, storage_state: str | None = None,
                    cdp_endpoint: str | None = None) -> None
    async def stop(self) -> None
    async def snapshot(self) -> PageSnapshot
    async def screenshot(self, path: str) -> str
    async def act(self, step: Step, resolver: "Resolver", guard: "Guard") -> ActResult
    async def find(self, bundle: LocatorBundle, timeout_ms: int) -> Any | None
```

**v2 attach semantics (VERIFIED WORKING — do not redesign):**
```python
browser = await playwright.chromium.connect_over_cdp(cdp_endpoint)  # "http://localhost:9222"
context = browser.contexts[0]
page = context.pages[0]
```
- When `cdp_endpoint` is not None, attach as above and **reuse the existing tab**. Do not create a context, do not `launch()`, do not pass `storage_state` — the real Chrome profile at `~/wa-profile` already holds the GitHub and Gmail sessions.
- When `cdp_endpoint` is None, fall back to `launch()` + `storage_state` (test/CI path only).
- `stop()` must **detach, not close**, when attached over CDP. Killing the judge's browser mid-demo is unrecoverable.
- Rationale: Google blocks Playwright-launched Chromium at login. CDP + manual login is the only workaround.

`act()` is the ONLY place actions happen. It calls `guard.check()` **before touching the page** and raises `BlockedAction` if refused. On locator miss it calls `resolver.heal()` exactly once, retries, then fails with `ok=False` — no exception escapes except `BlockedAction`.

### M2 `browser/resolver.py`
```python
class Resolver:
    async def resolve(self, intent: str, snap: PageSnapshot) -> LocatorBundle
    async def heal(self, bundle: LocatorBundle, snap: PageSnapshot) -> LocatorBundle | None
```

### M3 `teach/compiler.py`
```python
async def compile_workflow(utterance: str, site_hint: str) -> DraftWorkflow

class DraftStep(BaseModel):
    action: ActionType; intent: str; value: str | None = None
    narration_hint: str = ""; risk: RiskLevel = RiskLevel.SAFE

class DraftWorkflow(BaseModel):
    title: str; target_domain: str; entry_url: str
    steps: list[DraftStep] = []
    ambiguities: list[str] = []     # questions for the human. May be empty.
```
Raises `CompileError` on unusable model output. Never returns a half-built draft.

### M4 `teach/rehearser.py`
```python
async def rehearse(draft: DraftWorkflow, driver: Driver, resolver: Resolver,
                   guard: Guard, emit: EmitFn) -> WorkflowSpec
```

### M5 `runtime/orchestrator.py`
```python
class Orchestrator:
    def __init__(self, spec: WorkflowSpec, driver, resolver, narrator,
                 guard, emit: EmitFn, session_id: str = "session",
                 interrupter=None, qa=None)      # v2: last two are Demo 2, default None
    async def run(self) -> SessionState
    def submit_user_message(self, text: str) -> None    # non-blocking, enqueues
    @property
    def state(self) -> SessionState
```
Normative loop:
```
state = RUNNING
while cursor < len(steps) and state == RUNNING:
    await drain_events()          # may answer questions, may move cursor, may abort
    if cursor >= len(steps) or state != RUNNING: break
    step = steps[cursor]
    emit(step_start); narrate(step, snapshot()); result = await driver.act(step, resolver, guard)
    await drain_events()          # mid-step interruptions handled BEFORE advancing
    record(result)
    if result.ok: _advance()
    else: handle_failure(step, result)
```
`drain_events()` loops until the queue is empty — that is what makes consecutive interruptions free. **In v0 (Demo 1) it is a no-op stub; the loop shape is already final.**

Failure policy (v0): a failed step aborts the run and does not move the cursor. A blocked step records `BLOCKED`, emits `blocked`, and **the run continues** — the refusal is a feature we show the judge.

### M6 `runtime/narrator.py`
```python
class Narrator:
    async def narrate(self, step: Step, snap: PageSnapshot) -> str
    async def resume_line(self, step: Step, cursor: int, total: int) -> str
```
Must use the live snapshot, not only `narration_hint`. Canned output is a scoring failure. On model timeout the caller falls back to `narration_hint`; `narrate` itself must never raise.

### M7 `runtime/interrupt.py`
```python
async def classify(text: str, spec: WorkflowSpec, cursor: int) -> InterruptDecision

class InterruptDecision(BaseModel):
    kind: InterruptKind
    target_step_index: int | None = None    # for JUMP/SKIP
    rationale: str
```

### M8 `runtime/qa.py`
```python
class QA:
    async def answer(self, question: str, spec: WorkflowSpec, cursor: int,
                     live: PageSnapshot) -> Answer

class Answer(BaseModel):
    text: str
    grounded: bool          # False => the answer said "I don't know"
    sources: list[str]      # step ids / "live_page"
```
Grounding rule: answer from `spec.steps[*].knowledge` + `live` ONLY. If unsupported, return `grounded=False` and say so plainly. Guessing is worse than declining.

### M9 `safety/guard.py`
```python
class Guard:
    def __init__(self, allowed_domains: list[str])
    def check(self, step: Step, snap: PageSnapshot) -> GuardVerdict

class GuardVerdict(BaseModel):
    allowed: bool; risk: RiskLevel; reason: str

class BlockedAction(Exception):     # in app.schemas, carries .verdict and .step_id
```
Blocks: off-allowlist navigation; any step whose intent or resolved element name matches the destructive vocabulary — **word-boundary matched, case-insensitive**: send, delete, delete permanently, empty trash, buy, purchase, publish, deactivate, unsubscribe all, confirm payment, **merge pull request, close issue, transfer ownership, delete repository** (v2, GitHub). "Resend" and "Sender" must NOT trip "send". Blocked actions emit a `blocked` event and are narrated aloud.

### M10 `store.py`
```python
async def save_workflow(spec: WorkflowSpec) -> str
async def load_workflow(workflow_id: str, version: int | None = None) -> WorkflowSpec
async def list_workflows() -> list[WorkflowSummary]
```
Raises `WorkflowNotFound`. `load()` with no version returns the highest.

### M11 `server.py`
FastAPI. `GET /` serves the UI. `WS /ws/{session_id}` speaks §6. `GET /api/workflows`. Browser view: CDP screencast frames relayed as base64 on the same socket, event type `frame`, throttled to 5fps.

### shared `llm.py`
```python
async def complete(system: str, user: str, json_schema: dict | None = None,
                   max_tokens: int = 1024) -> str | dict
```
One retry on transient error. On JSON mode, strip fences before parsing.

---

## 8. Conventions delegates must follow

- Pydantic v2 (`model_validate`, `model_dump_json`), not v1.
- No bare `except:`. Catch specific, re-raise with context. The one licensed exception is a documented boundary catch that keeps a demo alive — comment it.
- Every public function gets a docstring with one usage example.
- Timeouts everywhere; nothing may hang a demo.
- Never `time.sleep`. `await asyncio.sleep`.
- No secrets in code. `.env` via `pydantic-settings`.
- Tests use the fakes in `tests/fakes.py` (FakeDriver, FakeLLM, FakeGuard, FakeNarrator, FakeResolver, EventCollector) — never a real browser or real model. **Do not edit `tests/fakes.py`; if you need a new fake, ask the master chat.**

---

## Changelog

- **v1** — initial freeze. Execution policy: saved locator first, LLM self-heal on miss.
- **v2** — Demo 1 handoff. Changes:
  1. **CDP attach is the primary browser path.** `Driver.start()` gains `cdp_endpoint: str | None = None`. Attach via `connect_over_cdp` → `browser.contexts[0]` → `contexts[0].pages[0]`. Verified working against real Chrome. `stop()` detaches rather than closes when attached.
  2. **`page.accessibility` is removed in Playwright 1.62.** `a11y_digest` now comes from `await page.locator("body").aria_snapshot()`.
  3. **Target site changed to GitHub** (create an issue with a label and assignee). Gmail is a stretch, Trello the backup. Guard vocabulary extended with GitHub-destructive verbs.
  4. **Shared types relocated into `app/schemas.py`**: `PageSnapshot`, `ActResult`, `DraftStep`, `DraftWorkflow`, `InterruptDecision`, `Answer`, `GuardVerdict`, `WorkflowSummary`, `BlockedAction`, `CompileError`, `WorkflowNotFound`, `EmitFn`. Rationale: "import only from `app.schemas`" was impossible while M1 owned `PageSnapshot` and M2/M4/M6/M8/M9 all needed it. No shape changed — only the file they live in.
  5. **`StepStatus.BLOCKED` added.** A guard refusal is not a user skip and not a failure; the UI colours them differently and the integration test asserts on it.
  6. **`SCROLL` joins `NAVIGATE` as locator-optional**, and `NAVIGATE` now requires a `value`. Both enforced by a validator on `Step`.
  7. **`Narrator.resume_line` gains `total: int`** — it could not say "step 3 of 5" without it.
  8. **`Orchestrator.__init__` gains `session_id`, and `interrupter`/`qa` default to `None`** so Demo 1 can construct it without Demo 2's modules.
  9. **`"frame"` is a real member of the event enum** rather than a documented exemption.
