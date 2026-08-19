<div align="center">

# 🚁 Live Walkthrough Agent

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-99%20passing-brightgreen)](#testing)
[![Runs locally](https://img.shields.io/badge/LLM-local%20%7C%20no%20API%20key-8b5cf6)](#it-runs-on-your-laptop)
[![Status](https://img.shields.io/badge/status-demo%20build-orange)](#status)

**Teach a browser workflow once, in plain English. Then watch it run itself — narrating every step out loud, answering questions mid-run, and refusing what it shouldn't touch.**

</div>

---

Somebody on your team walks a new hire through the same five-minute process every week. Sign in, open the planner, set six fields, clear the checklist, save the draft. You could record a video — it goes stale the moment a button moves. You could write a Selenium script — it can't explain itself, and it can't answer "wait, why that field?"

This drives a real browser through the workflow, narrates what it's doing from what it can actually see on the page, and stops to answer you when you interrupt — then resumes at the exact step it was on. It learns the workflow from one sentence of English and one live rehearsal, so there is no script to maintain.

Nothing opens on your desktop. The browser runs headless and you watch it inside the app.

## How to use it

### 1. Install

```bash
uv venv --python 3.12 .venv                     # or: python3.12 -m venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m playwright install chromium
```

### 2. Point it at a local model

```bash
ollama pull llama3.2:3b
cp .env.example .env                            # then set WA_LOCAL_MODEL=llama3.2:3b
```

No API key needed. See [It runs on your laptop](#it-runs-on-your-laptop) for the fallback order.

### 3. Start it

```bash
.venv/bin/python -m uvicorn app.server:app --port 8000
```

Open **http://localhost:8000**. The demo site it drives — SkyLoop Flight Academy — is served by the same process at `/drone`.

### 4. Run a workflow

Pick one from the dropdown, press **Start**. The plan fills in on the right, the browser preview goes live, and narration streams into the chat:

```
[run_started]  Pull the quarterly readiness report
[step_start]   open the SkyLoop sign-in page
[narration]    opening SkyLoop
[step_done]    open the SkyLoop sign-in page went through
...
[step_start]   confirm the quarterly summary appeared
[narration]    I'm currently viewing the quarterly report for the 2026-Q3 period,
               which shows a fleet readiness of 60% with 29 pilots certified
               against a target of 50.
[run_completed]
```

That last line is generated from what the agent read on the page, not from a script.

### 5. Interrupt it

Type into the chat at any time. Your message is classified and handled without losing the run's place:

| You type | What happens |
|---|---|
| "what does that button do?" | Answered from what it actually saw. It cites its sources, and declines when it wasn't shown the answer. |
| "skip this part" | Current step marked skipped, moves on. |
| "jump to the part where it saves" | Skips ahead — only if that maps to a real step. |
| "do that again" | Repeats the current step. |
| "wait" / "hold on" | Pauses **after** the current step finishes, never mid-action. |
| "stop" | Ends the run. |

A **question never moves the cursor.** Ask three in a row; the run resumes exactly where it paused.

## It runs on your laptop

Providers are tried in order, and the first one configured wins:

| Order | Provider | Set |
|---|---|---|
| 1 | Local OpenAI-compatible server (Ollama, llama.cpp, LM Studio) | `WA_LOCAL_MODEL` |
| 2 | Groq | `GROQ_API_KEY` |
| 3 | Gemini | `GEMINI_API_KEY` |
| 4 | Anthropic | `ANTHROPIC_API_KEY` |

Two things make a small local model good enough for this:

- **Grammar-constrained decoding.** Every structured call sends its JSON schema, so the decoder is held to it. A 3B model *cannot* return an invalid action or a missing field.
- **Thinking off by default.** Local builds are often reasoning models that spend the whole token budget on hidden thought and return empty content. `WA_LOCAL_REASONING=none` (the default) turns that off — worth 90s → 12s on the same prompt.

With no provider at all, nothing crashes: narration falls back to page-grounded templates, locators fall back to deterministic guesses, and chat interrupts classify as "unclear".

## What it refuses

Safety is checked before every single action touches the page, not after.

| Guard | Example |
|---|---|
| Irreversible phrases | "delete repository", "confirm payment", "transfer ownership" |
| Off-allowlist navigation | A workflow taught on `localhost:8000` cannot wander to `example.com` |
| Ungrounded answers | "What's your pricing?" → *"that wasn't part of what I was shown"* — never a guess |
| Teaching-time compilation | A draft containing send / publish / delete / buy is rejected before it ever runs |

The refusal is spoken out loud, in the run, as a first-class outcome — not an exception.

## The site it drives

**SkyLoop Flight Academy** (`/drone`) is a drone-pilot training platform with three signed-in roles. It exists so the agent has a site we control: stable `data-testid` hooks, no CAPTCHAs, nothing destructive.

| Role | Sign in as | Workspace | Shaped for |
|---|---|---|---|
| Vice President | `vp@skyloop.io` | Fleet readiness | Outcomes — KPI tiles, quarter selector, one-click board summary |
| Technical | `tech@skyloop.io` | Mission planner | Depth — 8 flight parameters, preflight checklist, safety envelopes |
| Marketing | `mkt@skyloop.io` | Course campaigns | Reach — course, segment, channels, schedule; drafts only |

Password for all three: `flightdemo`. Each role is hard-gated out of the other two workspaces — sessions are HMAC-signed, passwords PBKDF2-hashed.

The same agent adapts its depth to whichever workspace it's in, because the narration comes from the page.

## Teaching a new workflow

Press **Teach**, describe it in a sentence, give the starting URL. The agent compiles a draft, then rehearses it live in the browser — and **asks you when a step won't resolve** instead of throwing the lesson away:

```
[teach_progress]  Learning step 2 of 7: type the email address
[teach_question]  I could not learn 'type the email address'. Describe what I
                  should click or type instead.
   -> the Work email field
[teach_progress]  Retrying step 2 as: the Work email field
[teach_progress]  Learning step 5 of 7: open the reports page
[teach_question]  I could not learn 'open the reports page'...
   -> skip that, we're already there
[teach_progress]  Dropping step 5: open the reports page
[teach_complete]  Learned 6 steps and saved the reusable workflow.
```

Answers replace that step's description and it tries again (twice, then it gives up on that step). Answering **"skip that"** drops a step the site doesn't need — a one-sentence description often plans a navigation that isn't there. What's learned is kept; the workflow lands in `workflows/` as JSON and appears in the picker immediately.

## How it works

A `WorkflowSpec` is a sequence of `Step`s — an action (navigate / click / type / select / scroll / wait_for / assert) plus a ranked list of candidate locators. The `Orchestrator` walks them one at a time against a real Chromium page, narrating through the model, checking every action against the `Guard` before it lands, and streaming narration, screenshots and step status to the browser over one WebSocket.

| Concern | File |
|---|---|
| Web server, WebSocket protocol | `app/server.py` |
| Run loop, cursor, interrupts, pause/resume | `app/runtime/orchestrator.py` |
| Narration | `app/runtime/narrator.py` |
| Grounded Q&A during a run | `app/runtime/qa.py` |
| Retrieval that keeps answers grounded | `app/retrieve.py` |
| Classifying chat interruptions | `app/runtime/interrupt.py` |
| Browser control (Playwright) | `app/browser/driver.py` |
| Intent → page locator, with fallbacks | `app/browser/resolver.py` |
| Refusing irreversible / off-domain actions | `app/safety/guard.py` |
| Plain English → workflow | `app/teach/compiler.py`, `app/teach/rehearser.py` |
| The SkyLoop site being driven | `app/drone.py`, `ui/drone/` |
| Provider routing (local → Groq → Gemini → Claude) | `app/llm.py` |
| Data shapes (single source of truth) | `app/schemas.py` |
| Frontend | `ui/index.html` |

### Grounded answers, in two stages

Questions aren't answered from the whole walkthrough. Each page capture is chunked, scored against the question by IDF word overlap, and only the top few chunks reach the model — which is what keeps a small model's prompt short enough to answer well. **Zero overlap means no model call at all**, and a clean "I wasn't shown that."

No embeddings, no vector store: the corpus is six short page captures, and stdlib scoring beats a dependency at that size.

## Testing

```bash
.venv/bin/python -m pytest
```

**99 tests**, all local fakes — no real browser, no real model, no network. The two `test_llm_*.py` files stub `urlopen` directly.

## Configuration

Everything is environment variables, loaded from `.env`. Full list with defaults in `.env.example`. The ones that change behaviour most:

| Variable | Default | Does |
|---|---|---|
| `WA_LOCAL_MODEL` | *(unset)* | Local model name. Set it and no API key is used. |
| `WA_HEADLESS` | `1` | `0` shows the browser window instead of the in-app preview. |
| `WA_CDP_ATTACH` | `0` | `1` drives your real Chrome over CDP — needed only for a workflow that depends on a profile you're logged into. |
| `WA_STEP_PACE_S` | `1.25` | Seconds paused before each action, so a human can follow. |
| `WA_DEMO_TOKEN` | *(unset)* | Required as `?token=` on the WebSocket when set. |

## Status

Demo build. Three shipped workflows (one per role), plus anything you teach it. Live interruption, grounded Q&A, safety refusals, and teaching all work end to end on a local 3B model.

Not built: human-in-the-loop approval gates, auth hardening beyond the demo accounts, multi-site workflow libraries.

See [INSTRUCTIONS.md](INSTRUCTIONS.md) for the full run-it-yourself guide.
