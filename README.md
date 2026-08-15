# Live Walkthrough Agent

An agent that drives a real, visible Chrome window through a saved browser
workflow (e.g. "create a labelled GitHub issue"), narrating each step aloud
as it goes, while a human watches and can interrupt it live — ask questions,
skip a step, jump ahead, repeat one, pause it, or stop it outright.

It also refuses irreversible actions ("delete the repository") out loud
instead of doing them, and can learn new workflows from a plain-English
description plus one live rehearsal run.

## How it works, in one paragraph

A `WorkflowSpec` (a saved JSON file — see `workflows/`) is a sequence of
`Step`s, each an action (navigate / click / type / select / scroll /
wait_for / assert) plus a ranked list of candidate locators. The
`Orchestrator` (`app/runtime/orchestrator.py`) walks the steps one at a
time against a real Chrome tab via Playwright (`app/browser/driver.py`),
narrates each one through an LLM (`app/runtime/narrator.py`), checks every
action against a safety `Guard` before it touches the page
(`app/safety/guard.py`), and streams everything — narration, screenshots,
step status — to a browser UI over one WebSocket
(`app/server.py`, `ui/index.html`). A human can type into that UI at any
point; an `Interrupter` classifies the message and the orchestrator reacts
without losing its place.

## Where things live

| Concern | File |
|---|---|
| Web server, WebSocket protocol | `app/server.py` |
| Run loop, cursor, interrupts, pause/resume | `app/runtime/orchestrator.py` |
| Narration text | `app/runtime/narrator.py` |
| Live Q&A during a run | `app/runtime/qa.py` |
| Classifying chat interruptions | `app/runtime/interrupt.py` |
| Browser control (Playwright) | `app/browser/driver.py` |
| Turning intent into a page locator | `app/browser/resolver.py` |
| Refusing irreversible/off-domain actions | `app/safety/guard.py` |
| Natural-language → workflow (teaching) | `app/teach/compiler.py`, `app/teach/rehearser.py` |
| Workflow persistence | `app/store.py`, `workflows/*.json` |
| Data shapes (single source of truth) | `app/schemas.py` |
| LLM provider routing (Groq → Gemini → Claude) | `app/llm.py` |
| Frontend | `ui/index.html` |

## Requirements

- Python 3.11+, a virtualenv (`.venv/` is already set up in this repo)
- Google Chrome
- At least one LLM provider API key (Groq, Gemini, or Anthropic — see
  `.env.example`). Without one, the agent still runs: narration falls back
  to a page-grounded local template and chat interrupts default to
  "unclear," but nothing crashes.

## Setup and running

See **[INSTRUCTIONS.md](INSTRUCTIONS.md)** for the full walkthrough of
installing, configuring, and using the app. See
**[QUICKSTART.md](QUICKSTART.md)** for the condensed checklist used to
record a demo.

## Testing

```bash
source .venv/bin/activate
pytest
```

Tests use only local fakes (`tests/fakes.py`) — no real browser, no real
model, no network — except the two `tests/test_llm_*.py` files, which stub
out `urlopen` directly rather than hitting a real provider.

## Configuration

All configuration is environment variables, loaded from `.env` at repo
root (see `.env.example` for the full list with defaults). The two that
change behavior most:

- `WA_DEMO_TOKEN` — required as `?token=` on the WebSocket if set; leave
  unset to disable the check locally.
- `WA_STEP_PACE_S` — seconds paused before each step's action, so a human
  can actually follow along.

## Status

This is a live build with three roughly-scoped stages: Demo 1 is a
hardcoded workflow driven end to end with narration and safety refusals;
Demo 2 adds live interruption handling (question/skip/jump/repeat/pause/
stop); Demo 3 is the teach-a-new-workflow pipeline. All three exist in this
tree today — see `HERMES_demo2_interruptions.md` and `docs/` for the
original design notes.
