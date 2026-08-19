# Instructions — running the Live Walkthrough Agent

What this project is and how it's put together lives in [README.md](README.md).
This is the practical "get it running on my machine" guide.

---

## 1. One-time setup

### Python

Needs **3.11+**. macOS ships 3.9, so create the venv against a newer interpreter:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

Without `uv`:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### The browser

The agent drives a headless Chromium managed by Playwright. Download it once:

```bash
.venv/bin/python -m playwright install chromium
```

### The model

```bash
ollama pull llama3.2:3b
```

2 GB, already quantized (Q4_K_M). It handles narration, interrupt classification,
grounded answers and workflow teaching. A bigger model is not required — every
structured call is grammar-constrained to its JSON schema, so a small model
cannot return a malformed reply.

Make sure Ollama is serving:

```bash
curl -s http://localhost:11434/api/tags | head -c 80
```

### Configuration

```bash
cp .env.example .env
```

Then set one line:

```
WA_LOCAL_MODEL=llama3.2:3b
```

Every variable is documented inline in `.env.example`. If you'd rather use a
hosted provider, set `GROQ_API_KEY`, `GEMINI_API_KEY` or `ANTHROPIC_API_KEY`
instead and leave `WA_LOCAL_MODEL` empty.

### Check it

```bash
.venv/bin/python -m pytest
```

Expect **98 passed**. If not, stop and fix that first — nothing downstream works otherwise.

---

## 2. Start it

One process serves everything — the agent UI, the WebSocket, and the SkyLoop
site being driven:

```bash
.venv/bin/python -m uvicorn app.server:app --port 8000
```

Open **http://localhost:8000**.

If you set `WA_DEMO_TOKEN` in `.env`, open
`http://localhost:8000/?token=<that value>` instead. Leave it unset and the
check is disabled.

**No browser window will appear.** That's correct — the agent drives a headless
Chromium and you watch it in the preview panel. To see a real window instead,
set `WA_HEADLESS=0`.

---

## 3. Look at the site it drives

Worth opening once yourself so you know what the agent is doing:
**http://localhost:8000/drone**

| Sign in as | Password | Lands on |
|---|---|---|
| `vp@skyloop.io` | `flightdemo` | Fleet readiness — KPI tiles, quarter selector, report generator |
| `tech@skyloop.io` | `flightdemo` | Mission planner — flight parameters, preflight checklist |
| `mkt@skyloop.io` | `flightdemo` | Course campaigns — audience, channels, schedule |

Each role is locked out of the other two workspaces; try it and you'll be
redirected back to your own.

The store is in memory. Restarting the server resets missions and campaigns to
their seeded state, which is what you want between rehearsals.

---

## 4. Run a workflow

1. Pick one from the dropdown. Three ship:
   - **Plan and preflight a survey mission** (16 steps, technical)
   - **Pull the quarterly readiness report** (8 steps, VP)
   - **Draft a course intake campaign** (12 steps, marketing)
2. Press **Start**.
3. Watch the preview panel and the plan rail. Narration streams into the chat
   and is spoken aloud if **Voice** is ticked — pick the voice from the dropdown
   beside it.

Each step pauses for `WA_STEP_PACE_S` seconds (default 1.25) before acting, so a
human can follow along. Raise it if you're presenting.

---

## 5. Interrupt it mid-run

Type into the chat box at any point:

| You type | What happens |
|---|---|
| "what does that button do?" | Answered from what it has actually seen, then resumes where it was. |
| "why is fleet readiness only 60%?" | Same — grounded in the page it's looking at. |
| "what's your pricing?" | Declines: it wasn't shown that. **This is a designed outcome, not a failure.** |
| "skip this part" | Marks the step skipped and moves on. |
| "jump to the part where it saves" | Skips ahead, if that maps to a real step. |
| "do that again" | Repeats the current step. |
| "wait" / "hold on" | Pauses after the current step finishes, then asks whether to resume. |
| "stop" | Ends the run. |

There's also a **Pause** button in the header. Same pause-after-current
behaviour, but deterministic — it never goes through the model, so it works even
if the model is slow or down.

Questions never move the cursor. Ask three in a row and the run still resumes at
the right step.

---

## 6. Teach it a new workflow

1. Press **Teach**.
2. Describe the workflow in one or two sentences, e.g.
   *"Sign in as mkt@skyloop.io with password flightdemo, then draft a campaign
   for the thermal course."*
3. Leave the starting URL as `http://localhost:8000/drone/login`.
4. Press **Teach it live**.

It compiles a draft, then rehearses it step by step in the browser. When a step
won't resolve, it asks you in the chat. **Just answer in the chat box** — your
reply becomes that step's new description and it tries again.

Useful answers:

| Situation | Answer with |
|---|---|
| It can't find a field | Name it as it appears — *"the Work email field"* |
| It can't find a button | *"the Save campaign draft button"* |
| The step isn't needed — you're already there | *"skip that, we're already there"* |

Each step gets two retries before teaching gives up on it. Everything learned up
to that point is kept.

On success the workflow is written to `workflows/<name>.v1.json` and appears in
the dropdown immediately. Run it like any other.

> **Teaching is non-deterministic.** A small model plans a slightly different set
> of steps each time, and typically asks about two or three of them. If you're
> demoing this, teach the workflow once beforehand so a working one is already on
> disk, then teach a second one live.

---

## 7. Driving your real Chrome instead

Only needed for a workflow against a site where you're already logged in with a
profile — the headless browser has no session of yours.

```bash
./launch_browser.sh                       # Chrome with CDP open on port 9222
WA_CDP_ATTACH=1 .venv/bin/python -m uvicorn app.server:app --port 8000
```

Log in inside that Chrome window and leave it open. The agent drives that exact
window.

> Only one session may drive a browser at a time. Opening the UI in two tabs and
> starting a run in each will make them fight over the same page, and one will
> close it out from under the other.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `openai_call_failed ... timed out` | Model cold-loading, or too large | First call after a while is slow; raise `WA_LOCAL_TIMEOUT`, or use a smaller model |
| Narration is generic, not about the page | Model call exceeded `WA_LLM_TIMEOUT` and fell back to the template | Raise it, or use a faster model |
| Answers always "I wasn't shown that" | Nothing retrieved matched the question | Expected for off-topic questions. Otherwise raise `WA_EVIDENCE_CHUNKS` |
| `Could not attach to Chrome on ...` | `WA_CDP_ATTACH=1` with no Chrome on 9222 | Run `./launch_browser.sh`, or unset it and use headless |
| Teaching: *"the draft did not stay on the requested website"* | Model drifted to another host | Re-word the description; keep the URL in it |
| Teaching: *"navigates somewhere but gives no address"* | A mid-workflow step wanted a page it didn't name | Name the page in your description, or teach it as a click |
| Tests fail after editing `.env` | — | They shouldn't: `tests/conftest.py` clears provider variables. If they do, that's a real bug |
| A run repeats one step forever | Step numbering has a gap | Shouldn't happen — `WorkflowSpec` rejects it at load. File it if you see it |

---

## Recording a demo

1. Restart the server first — resets SkyLoop to its seeded state.
2. Teach the workflow you'll teach live, once, in advance. Keep it.
3. Raise `WA_STEP_PACE_S` to ~2.0 so steps are followable on video.
4. Record each run as it lands. Never rely on live-only.
