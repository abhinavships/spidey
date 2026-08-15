# Instructions — how to use the Live Walkthrough Agent

For what this project is and how it's structured, see [README.md](README.md).
This document is the practical "how do I actually run and use it" guide.

## 1. One-time setup

```bash
cd ~/walkthrough-agent
source .venv/bin/activate
pip install -r requirements.txt      # already installed if .venv exists
```

Copy `.env.example` to `.env` and fill in at least one LLM provider key
(Groq, Gemini, or Anthropic — checked in that priority order by
`app/llm.py`). Every variable is documented inline in `.env.example`.

If you're pointing this at a real GitHub repo for the shipped `gh-issue`
workflow, open `workflows/gh-issue.v1.json` and replace the repo URL in
`entry_url` and the `open-repo` step's `value` with your own
`username/reponame`.

## 2. Start the two things this app needs

**A real, visible Chrome window** that the agent drives (separate from
whatever browser you're reading this in):

```bash
./launch_browser.sh
```

This opens Chrome with its DevTools debug port open on `9222`, navigated to
github.com. Log into GitHub in that window if asked, and leave it open —
the agent drives this exact window, not a hidden one.

**The app server:**

```bash
uvicorn app.server:app --port 8000
```

## 3. Open the UI

```
http://localhost:8000/?token=<WA_DEMO_TOKEN from your .env>
```

If `WA_DEMO_TOKEN` is empty/unset in your `.env`, the token check is
disabled and you can drop the `?token=` entirely.

Put this window and the Chrome window where you can see both at once —
the UI narrates and tracks progress, the Chrome window is where the actual
clicking happens.

## 4. Run a workflow

1. Pick a workflow from the dropdown (workflows on disk are listed via
   `GET /api/workflows`; `gh-issue` ships by default).
2. Click **Start walkthrough**. The agent drives Chrome step by step,
   narrating each one in the left pane (spoken aloud too, if the **Voice**
   checkbox is on) and tracking progress in the right-hand step list.

## 5. Interrupt it while it's running

Type into the chat box at the bottom at any time. Your message is
classified and handled without losing the run's place:

| You type something like | What happens |
|---|---|
| "what does that button do?" | Answered from what the agent has actually seen (grounded — it won't guess), then resumes where it was. |
| "skip this part" | Marks the current step skipped, moves on. |
| "jump to the label step" | Skips ahead to a matching step (only if it maps to a real step in this workflow). |
| "do that again" | Repeats the current step. |
| "wait" / "hold on" / "pause" | Pauses **after** the current step finishes (never mid-action) and asks whether to resume. Reply "yes"/"resume" to continue, "no"/"stop" to end. |
| "stop" | Ends the run immediately. |

There's also a **Pause** button in the header — same pause-after-current
behavior, but deterministic (it doesn't go through the LLM classifier at
all, so it works even if the model is slow or unavailable). Click it again
(now labeled **Resume**) to continue.

## 6. Safety refusals

Before any click/type/select actually touches the page, `Guard`
(`app/safety/guard.py`) checks it. It refuses and narrates the refusal in
red, then continues the run, if the step:

- Navigates off the workflow's allowed domain.
- Is explicitly marked `"risk": "destructive"` in the workflow file.
- Matches a hard-coded irreversible phrase in its own intent text — things
  like "delete", "delete repository", "transfer ownership", "empty trash",
  "confirm payment", "unsubscribe all".

This is not something you configure per run — it's a property of the
workflow file and the Guard's phrase list. To see it fire, edit a step's
`intent` in the workflow JSON to include one of those phrases and rerun.

## 7. Teach a new workflow

Use the bar above the chat: describe a short (3–6 step) workflow in plain
English plus the URL it starts from, and click **Teach live**. The agent
compiles a draft, then actually rehearses it once against the live Chrome
window (this really clicks around) to resolve real locators and capture
page knowledge. If any step doesn't resolve, it asks a clarifying question
instead of saving a broken workflow. A successful rehearsal is saved to
`WA_WORKFLOWS_DIR` (default `workflows/`) and immediately appears in the
workflow dropdown.

Only short, reversible workflows are accepted — anything that reads as
destructive (delete/buy/publish/merge/etc.) is rejected before rehearsal
ever starts, and a "submit" step is only allowed if your description
explicitly says this is a dummy/test repo.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Could not attach to Chrome on..." | `./launch_browser.sh` isn't running, or you closed that window. Check `curl http://localhost:9222/json` — an empty `[]` means the process is alive but has no open tab; kill it and relaunch. |
| Nothing in the workflow dropdown | You're not running `uvicorn` from the repo root — `workflows/` is resolved relative to the current working directory. |
| Every question gets a canned "out of scope" answer | No working LLM key, or the configured model/key has hit a quota or rate limit. Check `/tmp` server logs for `llm_call_failed` / `*_gave_up` warnings. |
| Narration sounds flat / templated | Same cause as above — no reachable LLM, so narration falls back to a local, page-grounded template. Still varies per page, just plainer. |
| WebSocket closes immediately with "invalid demo token" | Your URL's `?token=` doesn't match `WA_DEMO_TOKEN` in `.env` (or you dropped it while a token is still configured). |
| It logs you out of GitHub / hits 2FA | The Chrome profile at `~/wa-profile` got reset. Log in again in the launched window. |

For the condensed, demo-recording-specific checklist (including the
"record the safety refusal on camera" step), see
[QUICKSTART.md](QUICKSTART.md).
