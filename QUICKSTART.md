# QUICKSTART — get a working demo, in order

Follow top to bottom. Nothing here is optional.

---

## 1. Files in place (2 min)

Copy everything into `~/walkthrough-agent`, then:

```bash
cd ~/walkthrough-agent
source .venv/bin/activate
pip install pydantic pytest pytest-asyncio structlog fastapi uvicorn playwright anthropic httpx
chmod +x launch_browser.sh
pytest
```

**Expect: `42 passed`.** If not, stop and fix that first — nothing downstream works otherwise.

---

## 2. Your demo repo (3 min)

You need one throwaway GitHub repo on the account you'll be logged into. Nothing
special about it. If you don't have one: new repo, name it `wa-demo`, public,
tick "Add a README". Check Settings → Features → Issues is on.

Then open `workflows/gh-issue.v1.json` and find-and-replace **`OWNER/DEMO-REPO`**
with your real `username/reponame`. It appears in exactly two places, both near
the top. Change nothing else in that file.

Sanity check:
```bash
python -c "
import asyncio; from app.store import load_workflow
s = asyncio.run(load_workflow('gh-issue')); print(s.entry_url)"
```
It should print your real repo URL.

---

## 3. Chrome with the debug port open (2 min)

```bash
./launch_browser.sh
```

A Chrome window opens on github.com using a dedicated profile at `~/wa-profile`.
**Log into your GitHub account in that window and navigate to your demo repo.**
Leave the window open — the agent drives this exact window.

Verify the attach works:
```bash
python -c "
import asyncio
from playwright.async_api import async_playwright
async def m():
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp('http://localhost:9222')
        pg = b.contexts[0].pages[0]
        print('OK:', pg.url, '|', await pg.title())
asyncio.run(m())"
```
If that prints your URL and title, the risky part is done.

---

## 4. Optional but worth it: an API key (1 min)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

With a key, narration is written live from what's on screen. Without one, it
still narrates and still varies per page, but more plainly. **The demo works
either way** — don't let a missing key block you.

---

## 5. Run it

```bash
uvicorn app.server:app --port 8000
```

Open http://localhost:8000. Put that window on one side of your screen and the
Chrome window on the other so both are visible.

Pick the workflow in the dropdown, hit **Start walkthrough**. Chrome drives
itself through ten steps while the left pane narrates each one and the right
sidebar tracks the cursor.

---

## 6. Record it

- Both windows visible, everything else closed, no notifications.
- Start recording *before* you click Start. The judge needs to see it is live.
- Say nothing over the top — the narration is the point.
- Save as `recordings/demo1.mp4` the moment it's done.

**Then record the safety clip.** Open `workflows/gh-issue.v1.json`, change the
`submit` step's `intent` to `"delete the repository"`, save, restart the server,
run again. The agent refuses that step out loud, in red, and carries on. That
clip is worth more to a judge than the clean run. **Change it back afterwards.**

---

## If something breaks

| Symptom | Fix |
|---|---|
| "Could not attach to Chrome on..." | `./launch_browser.sh` isn't running, or you closed the window |
| Agent can't find a button | GitHub's DOM shifted. Open the step in the JSON, add a candidate matching the visible button text |
| Nothing in the dropdown | You're not running uvicorn from `~/walkthrough-agent`; `workflows/` is resolved relative to cwd |
| Narration sounds flat | No `ANTHROPIC_API_KEY` set. Fine for a demo |
| It logs you out / 2FA | The profile at `~/wa-profile` got reset. Log in again in the launched window |

---

## What this is *not* yet

- **Interruptions don't work.** You can type in the box; the message is parked and ignored. That's Demo 2 — `_drain_events()` in `orchestrator.py` is a deliberate no-op stub.
- **The workflow is hardcoded**, not taught. That's Demo 3.
- **No browser view inside the app.** Demo 1's browser view is the real Chrome window next to it, which is honestly more convincing anyway.

If you only ship what's here: a live-driven real browser, live narration off the
real page, and a safety refusal on camera. That's most of the must-have.
