# HERMES PACKET — Demo 2: interruptions. Ship in 30 minutes.

## Context you need

We have a working live-walkthrough agent. It attaches to a real Chrome over CDP,
drives GitHub step by step, and narrates each step from the live page. That part
is **done and recorded**.

What is missing is the highest-scoring criterion: **the customer can interrupt
mid-walkthrough, get a real answer, and the agent resumes at exactly the step it
was on.**

Everything below already exists on disk and is imported, not written by you:

- `app/schemas.py` — all shared Pydantic models. Frozen. Do not edit.
- `app/runtime/orchestrator.py` — the run loop. You edit ONE method in it.
- `app/llm.py` — `async def complete(system, user, json_schema=None, max_tokens=1024)`.
  Returns `str`, or `dict` in JSON mode, or `None` when there is no API key.
- `tests/fakes.py` — FakeDriver, FakeLLM, FakeGuard, FakeNarrator, EventCollector,
  `make_spec(n)`, `make_snapshot(text)`. Do not edit.

## The schemas you will use (verbatim from app/schemas.py)

```python
class InterruptKind(str, Enum):
    QUESTION = "question"; SKIP = "skip"; JUMP = "jump"
    REPEAT = "repeat"; STOP = "stop"; UNCLEAR = "unclear"

class InterruptDecision(BaseModel):
    kind: InterruptKind
    target_step_index: int | None = None
    rationale: str

class Answer(BaseModel):
    text: str
    grounded: bool          # False => we declined to guess
    sources: list[str]      # step ids, and/or the literal "live_page"

class StepKnowledge(BaseModel):     # attached to each Step as step.knowledge
    page_url: str; page_title: str
    a11y_digest: str; visible_text: str
    observed_effect: str            # one sentence: what changed after the action
    captured_at: datetime

class PageSnapshot(BaseModel):
    url: str; title: str; a11y_digest: str; visible_text: str

class Event(BaseModel):
    type: Literal["narration","step_start","step_done","step_failed","heal",
                  "interrupt_received","answer","resumed","plan_changed","blocked",
                  "run_started","run_completed","run_aborted","teach_progress",
                  "teach_question","teach_complete","error","frame"]
    session_id: str; ts: datetime
    text: str | None = None
    step_id: str | None = None
    cursor: int | None = None
    data: dict = {}

class StepStatus(str, Enum):
    PENDING="pending"; RUNNING="running"; DONE="done"
    FAILED="failed"; SKIPPED="skipped"; BLOCKED="blocked"
```

## The current orchestrator, so you know exactly what you are changing

```python
class Orchestrator:
    def __init__(self, spec, driver, resolver, narrator, guard, emit,
                 session_id="session"):
        ...
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._state = SessionState(session_id=..., workflow_id=spec.id,
                                   cursor=0, state=RunState.IDLE, records=[])

    def submit_user_message(self, text: str) -> None:
        self._queue.put_nowait(text)          # already works, non-blocking

    async def run(self) -> SessionState:
        s.state = RunState.RUNNING
        await self._emit("run_started", ...)
        while s.cursor < len(self.spec.steps) and s.state is RunState.RUNNING:
            await self._drain_events()
            if s.cursor >= len(self.spec.steps) or s.state is not RunState.RUNNING:
                break
            step = self.spec.steps[s.cursor]
            await self._run_step(step)        # emits step_start, narration, step_done
            await self._drain_events()
        ...

    def _advance(self) -> None:
        self._state.cursor += 1               # THE ONLY PLACE THE CURSOR MOVES

    async def _drain_events(self) -> None:
        return None                           # <-- YOUR JOB IS THIS METHOD

    async def _emit(self, type_, *, text=None, step_id=None, cursor=None, data=None)
    async def _safe_snapshot(self) -> PageSnapshot
```

## Your task — three files

### 1. `app/runtime/interrupt.py` (new)

```python
async def classify(text: str, spec: WorkflowSpec, cursor: int) -> InterruptDecision
```

- Prompt-based, not keyword-based. The judge speaks naturally.
- Send the model the step list (`index`, `id`, `intent` for each) plus the current
  cursor, so JUMP can map "the bit where you save it" to a real index. Do NOT send
  `knowledge` blobs — too big, this is on the latency path.
- Empty/whitespace input → UNCLEAR with **zero** model calls.
- Validate `target_step_index` against `len(spec.steps)` YOURSELF. Out of range or
  negative → UNCLEAR. Never clamp.
- Bad JSON, unknown kind, timeout, `complete()` returning None → UNCLEAR.
  **Never raise.** This runs inside the live run loop.
- `rationale` always non-empty.
- 4 second timeout via `asyncio.wait_for`.
- Disambiguation rule to encode in the prompt: questions about what the agent just
  did or is looking at are QUESTION even when phrased as complaints ("wait, why did
  you click there"). Requests to change what happens next are SKIP/JUMP/REPEAT.

### 2. `app/runtime/qa.py` (new)

```python
class QA:
    async def answer(self, question: str, spec: WorkflowSpec, cursor: int,
                     live: PageSnapshot) -> Answer
```

- Ground **only** in `spec.steps[*].knowledge` (page_title, visible_text,
  observed_effect) and the `live` snapshot. Nothing else. No world knowledge.
- If the answer isn't in there, return `grounded=False` and text that plainly says
  so — e.g. "That wasn't part of what I was shown, so I can't say." **A confident
  'I don't know' scores better than a hallucination. This is judged explicitly.**
- `sources` gets step ids used, and/or the literal string `"live_page"`.
- Answer ≤ 500 chars, spoken register, no markdown.
- Model failure → a grounded=False answer, never an exception.

### 3. Edit `_drain_events()` in `app/runtime/orchestrator.py`

Change ONLY that method plus adding `interrupter=None, qa=None` params to
`__init__` (defaulting to real instances if None). Touch nothing else in the file.

```
while the queue is not empty:
    text = queue.get_nowait()
    state.interrupt_count += 1
    emit("interrupt_received", text=text, cursor=cursor)
    decision = await classify(text, spec, cursor)

    QUESTION:  ans = await qa.answer(text, spec, cursor, await self._safe_snapshot())
               emit("answer", text=ans.text, data={"grounded":..., "sources":...})
               emit("resumed", text=await narrator.resume_line(
                        spec.steps[cursor], cursor, len(spec.steps)), cursor=cursor)
               # CURSOR MUST NOT MOVE

    SKIP:      record current step SKIPPED; emit("plan_changed"); self._advance()
    JUMP:      mark every step between cursor and target as SKIPPED (not DONE);
               set cursor = target; emit("plan_changed")
    REPEAT:    emit("plan_changed"); leave cursor; set a flag so the loop re-runs
               the current step (the same step id must appear twice in driver.calls)
    STOP:      state.state = RunState.ABORTED; emit("run_aborted")
    UNCLEAR:   emit("answer", text=a short clarifying question); cursor unchanged
```

**THE ONE RULE THAT MATTERS: a QUESTION must never change `cursor`.** The loop
draining until the queue is empty is what makes three consecutive questions work
for free. Emit order for a question is always
`interrupt_received` → `answer` → `resumed`, and `resumed` must name the correct
step number.

For JUMP you will need to move the cursor outside `_advance()`. That is the only
sanctioned exception — do it in one clearly named place and comment it.

## Tests you must also write — `tests/test_demo2.py`

Use `FakeDriver`, `make_spec(5)`, `EventCollector` and a scripted fake classifier.

1. One QUESTION mid-run → cursor identical before and after; all 5 steps still run exactly once
2. Three QUESTIONs back to back → 3 `answer` events, cursor unchanged, run completes
3. Out-of-scope question ("what's your pricing?") → `grounded=False`, text says it doesn't know
4. SKIP at cursor 2 of 5 → step 2 is SKIPPED, cursor becomes 3, steps 3–4 run
5. JUMP to 4 → intermediate steps SKIPPED not DONE
6. JUMP to index 99 on a 5-step spec → UNCLEAR, cursor unchanged
7. STOP → RunState.ABORTED, no further steps in `driver.calls`
8. Event order for one interruption is exactly interrupt_received → answer → resumed
9. Classifier table: "what does that button do?"→QUESTION, "skip this part"→SKIP,
   "ok stop"→STOP, "can you do that again"→REPEAT, ""→UNCLEAR
10. Malformed model output anywhere → UNCLEAR / grounded=False, never an exception

## Constraints

- Python 3.11+, Pydantic v2, async throughout, `structlog` for logging, no prints.
- Import only from `app.schemas`, `app.llm`, stdlib.
- Do not create, edit, or stub any file other than the three named above.
- Do not change any signature shown here. If one looks wrong, say so and stop.
- Paste back complete files. We run the tests; we do not read the code closely.
