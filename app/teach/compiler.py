"""Compile a plain-language teaching request into a locator-free draft."""

from __future__ import annotations

from urllib.parse import urlparse

from app.llm import complete
from app.schemas import ActionType, CompileError, DraftWorkflow, RiskLevel

# A signing-in workflow spends four steps before it does anything, so the
# ceiling has to leave room for the actual work.
MIN_STEPS, MAX_STEPS = 3, 10


def _host(netloc: str) -> str:
    """A network location reduced to its hostname, so a port can never divide it."""
    return netloc.lower().removeprefix("www.").partition(":")[0]

_DESTRUCTIVE_WORDS = ("send", "delete", "purchase", "buy", "publish", "transfer", "merge", "close issue")
_CAUTION_WORDS = ("submit", "create", "save", "type", "fill")


async def compile_workflow(utterance: str, site_hint: str) -> DraftWorkflow:
    """Create a complete draft or raise ``CompileError``; never return a partial draft."""
    if not utterance.strip() or not site_hint.strip():
        raise CompileError("Please provide both a workflow description and its starting URL.")
    parsed = urlparse(site_hint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CompileError("The starting URL must be a complete http(s) URL.")
    system = """You convert a browser walkthrough request into a safe workflow draft.

Rules:
- Produce between 3 and 10 steps. Never produce an empty steps array.
- One step per human action, in the order a person performs them.
- target_domain is the host only, e.g. "localhost:8000". entry_url is the full starting URL.
- action is one of navigate, click, type, select, scroll, wait_for, assert.
- A "type" or "select" step puts the text to enter in value; "navigate" puts the URL there.
- Every step needs a narration_hint: a few words describing the step out loud.
- Never include a send, publish, delete, buy, purchase, merge or transfer action.
  A final submit step is allowed only when the request explicitly says this is a
  dummy or test account.
- If a detail is missing, add a plain-language question to ambiguities instead of
  inventing it.

Example for "go to example.com/login, sign in as sam@x.io with password hunter2, then open reports":
{"title":"Sign in and open reports","target_domain":"example.com",
 "entry_url":"https://example.com/login",
 "steps":[
  {"action":"navigate","intent":"open the sign-in page","value":"https://example.com/login","narration_hint":"opening the login page"},
  {"action":"type","intent":"type the email address","value":"sam@x.io","narration_hint":"entering the email"},
  {"action":"type","intent":"type the password","value":"hunter2","narration_hint":"entering the password"},
  {"action":"click","intent":"click Sign in","value":null,"narration_hint":"signing in"},
  {"action":"click","intent":"open the reports page","value":null,"narration_hint":"opening reports"}],
 "ambiguities":[]}"""
    schema = DraftWorkflow.model_json_schema()
    raw = await complete(system, f"Starting URL: {site_hint}\n\nTeaching request: {utterance}",
                         json_schema=schema, max_tokens=900)
    if not isinstance(raw, dict):
        raise CompileError("The teaching model did not return a usable workflow.")
    try:
        draft = DraftWorkflow.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 - schema errors are a teaching failure, not a partial draft
        raise CompileError(f"The teaching model returned an invalid workflow: {exc}") from exc
    if _host(urlparse(draft.entry_url).netloc) != _host(parsed.netloc) or \
            _host(draft.target_domain) != _host(parsed.netloc):
        raise CompileError("The draft did not stay on the requested website.")
    # The human gave the address; it outranks whatever the model wrote down. This
    # is what restores a port the model dropped from "localhost:8000".
    draft = draft.model_copy(update={
        "target_domain": parsed.netloc,
        "entry_url": urlparse(draft.entry_url)._replace(netloc=parsed.netloc).geturl(),
    })
    if not MIN_STEPS <= len(draft.steps) <= MAX_STEPS:
        raise CompileError(
            f"Teaching must produce a workflow of {MIN_STEPS} to {MAX_STEPS} steps, "
            f"and this one had {len(draft.steps)}.")
    explicit_demo_submission = (
        parsed.netloc.lower().endswith("github.com")
        and "submit" in utterance.lower()
        and any(word in utterance.lower() for word in ("dummy", "test", "demo"))
    )
    safe_steps = []
    for position, step in enumerate(draft.steps):
        lowered = step.intent.lower()
        if any(word in lowered for word in _DESTRUCTIVE_WORDS):
            raise CompileError("The requested workflow includes an irreversible action; remove it and teach again.")
        if "submit" in lowered and not explicit_demo_submission:
            raise CompileError("Submitting is allowed only when you explicitly confirm this is your dummy/test GitHub repo.")
        if not step.intent.strip() or not step.narration_hint.strip():
            raise CompileError("Every taught step needs an intent and narration hint.")
        risk = RiskLevel.CAUTION if any(word in lowered for word in _CAUTION_WORDS) else RiskLevel.SAFE
        update: dict = {"risk": risk}
        if step.action is ActionType.NAVIGATE and not (step.value or "").strip():
            # Only the opening step can be repaired from entry_url. Filling it in
            # later would send the walkthrough back to the start page mid-run,
            # which looks like it worked and quietly breaks every following step.
            if position == 0:
                update["value"] = draft.entry_url
            else:
                raise CompileError(
                    f"Step {position + 1} ('{step.intent}') navigates somewhere but gives no "
                    "address. Name the page it should open, then teach it again.")
        safe_steps.append(step.model_copy(update=update))
    return draft.model_copy(update={"steps": safe_steps})
