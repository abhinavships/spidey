# plan.md — Live Walkthrough Agent

## What we are building, in one paragraph

A web app with two modes. In **teach mode** (done days before the demo) you describe a short workflow in plain language; the agent opens a real browser on your dummy account, walks the workflow live, resolves each step against the actual page, asks you when it's unsure, and freezes what worked into a saved workflow file. In **call mode** a judge opens a chat page with a live browser view beside it, asks for that workflow, and the agent drives the real browser step by step, narrating from what it sees. When the judge interrupts, the agent answers from what it actually captured during teaching plus the live page, then resumes at exactly the step it was on. Two modes, one artifact between them, one cursor that questions never move.

**Target site:** Gmail on a dummy account. Primary workflow: *create an email signature* (Settings gear → See all settings → General → Signature → Create → name it → save). 5 steps, reversible, zero real-world side effects. The Compose screen is available as a bonus step specifically to show the safety guard refusing to click Send.
**Backup site:** YouTube — create a playlist. Simpler DOM, fully reversible. Taught and frozen as insurance before demo day.

## Locked decisions

| Decision | Choice | Why |
|---|---|---|
| Browser | Playwright, persistent context | Real driven session (hard requirement), a11y snapshots for free, CDP screencast for the live view |
| Firecrawl | Not used | Stateless read tool. No session, no cursor, no live drive. Fails the core requirement |
| Execution | Saved locator first, LLM self-heal on miss | Deterministic happy path; survives UI drift; the heal is a good live moment |
| Teaching | NL description → compile → **live rehearsal** → freeze | Handles ambiguity natively (bonus), proves the path once before demo day, far less code than a click recorder |
| Interruptions | Event queue drained in a loop, cursor untouched by questions | One design yields single, consecutive, and plan-changing interruptions |
| QA grounding | Captured StepKnowledge + live snapshot only, may decline | "Using what it actually knows" is judged. A confident *I don't know* beats a hallucination |
| Stack | Python 3.11, Playwright, FastAPI + WebSocket, vanilla JS UI | No build step, nothing to debug at 3am |

## The four demos

We do not build modules and integrate at the end. We build four **runnable, screen-recordable** demos, each strictly containing the last. Miss Demo 4 entirely and we still ship a complete must-have.

### Demo 1 — Walking skeleton *(proves it's real)*
Hardcoded `workflows/gmail-signature.v1.json`. Real Playwright drives Gmail on the dummy account. Chat UI streams narration, live browser visible beside it. Safety guard in from the first commit.
- Modules: M0, M1, M2 (stub heal), M5 (no event queue yet), M6, M9, M10, M11
- **Done when:** you can record a clean run end to end with nothing hardcoded except the workflow file.
- Risk retired: "does it actually drive a browser live" — the thing everything else assumes.

### Demo 2 — Interruption *(the must-have, minus teaching)*
Event queue + cursor + classifier + grounded QA. Judge interrupts any number of times; agent answers, says where it was, resumes. Workflow still hardcoded — but knowledge blocks are hand-filled so QA has something real to be grounded in.
- Modules: M5 (full loop), M7, M8
- **Done when:** three consecutive unscripted questions mid-run, all answered, run completes correctly, and one deliberately out-of-scope question gets a clean "that wasn't part of what I was shown."
- Risk retired: the single hardest-to-fake scoring criterion.

### Demo 3 — Teaching *(closes the must-have, unlocks reuse)*
NL → compiler → rehearser → frozen spec. The Demo 1 hardcoded JSON is **deleted** and replaced by a taught one. Reuse (bonus) comes free: the artifact is already on disk and `list_workflows` already exists.
- Modules: M3, M4
- **Done when:** a workflow taught only from one paragraph of plain English, including one deliberately vague sentence, runs cleanly in call mode. Backup YouTube workflow taught the same day.

### Demo 4 — Bonuses
Plan-changing interruptions (SKIP / JUMP / REPEAT wired to the cursor — the classifier already returns them, this is just handling), then voice narration (M12, TTS on the narration channel).
- **Done when:** judge says "skip ahead to the part where you save it" and the agent does, narrating the jump.

Post-demo backlog, explicitly out of this build: human-in-the-loop approval gates, auth hardening, multi-site workflow libraries, richer security model.

## Module ownership

Hermes gets everything prompt-shaped — that's its strength and the organizers want it used. Codex/GLM get plumbing. The orchestrator stays in-house because it's where integration bugs live.

| # | Module | Owner | Demo | Blocks |
|---|---|---|---|---|
| M0 | `schemas.py` | **Claude master, alone, first** | 1 | everything |
| M1 | `browser/driver.py` | Codex | 1 | M4, M5 |
| M2 | `browser/resolver.py` | **Hermes** | 1 (stub) / 3 (full) | M4 |
| M3 | `teach/compiler.py` | **Hermes** | 3 | M4 |
| M4 | `teach/rehearser.py` | **Hermes** | 3 | — |
| M5 | `runtime/orchestrator.py` | **Claude master + 1 Claude acct** | 1, 2 | — |
| M6 | `runtime/narrator.py` | GLM | 1 | — |
| M7 | `runtime/interrupt.py` | **Hermes** | 2 | — |
| M8 | `runtime/qa.py` | **Hermes** | 2 | — |
| M9 | `safety/guard.py` | GLM | 1 | M1 |
| M10 | `store.py` | Codex | 1 | — |
| M11 | `server.py` + UI | Antigravity | 1 | — |
| M12 | voice | anyone | 4 | — |

**M0 is written by us and frozen before a single delegation prompt goes out.** It is in `contracts.md` already; transcribing it to `schemas.py` is mechanical.

## Delegation protocol

Every delegated task is one message containing exactly four things:

1. Full text of `contracts.md`
2. The module's section from `plan_unittests.md` (the tests are the spec)
3. `"Implement ONLY app/<path>.py. Import only from app.schemas and your declared dependencies. Do not create, modify, or stub any other file. Do not change any signature in contracts.md — if a signature seems wrong, say so and stop."`
4. Site-specific notes if any (Gmail DOM quirks, etc.)

Acceptance: paste back the file, we run its tests. Green or regenerate. **We do not read delegated code closely — we read test output.** That is what makes the no-agentic-coding approach viable.

Anti-drift rules, learned the hard way:
- Never let two delegates touch one file.
- Never let a delegate invent a schema. If it needs a new type, it comes back to us and goes into `contracts.md` v2.
- Integration is always done by the master chat, never by a delegate.
- A module without passing tests does not get merged, no matter how good it looks.

## Parallelization

**Wave A (nothing blocks these):** M9 guard, M10 store, M11 server+UI shell, M6 narrator. Four delegates at once, hour zero.
**Wave B (needs M0 only, which exists):** M1 driver, M7 classifier.
**Wave C (needs M1):** M2 resolver, then M4 rehearser.
**In-house throughout:** M5 orchestrator, integration, demo recording.

## Demo-day risk register

| Risk | Mitigation |
|---|---|
| Gmail DOM shifts between teach and demo | Self-heal path; re-run rehearsal the morning of |
| Google flags the dummy account / 2FA challenge | Persistent `storage_state.json` saved days early; YouTube workflow as backup; account warmed by normal use |
| Live model latency stalls narration | Narration streams; 6s timeout falls back to `narration_hint`; classifier + QA share one call where possible |
| Judge asks something genuinely unknowable | QA declines cleanly. This is a designed outcome, not a failure |
| Network dies during the demo | Screen recording of a full clean run kept ready to play |
| Wi-Fi/venue latency on screencast | Frame rate throttled to 5fps, quality tunable |

## Definition of done for the submission

- Must-have: taught workflow, live driven browser, live narration, ≥1 interruption answered and resumed, dummy account only, no destructive actions. **All four demos above satisfy this by Demo 3.**
- Bonuses claimed in order of certainty: consecutive interruptions (free, Demo 2), reuse (free, Demo 3), messy teaching input (Demo 3), plan-changing interruptions (Demo 4), voice (Demo 4).
- Recording of each demo kept as it lands. Never rely on live-only.
