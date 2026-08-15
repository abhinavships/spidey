"""Compile a plain-language teaching request into a locator-free draft."""

from __future__ import annotations

from urllib.parse import urlparse

from app.llm import complete
from app.schemas import CompileError, DraftWorkflow, RiskLevel

_DESTRUCTIVE_WORDS = ("send", "delete", "purchase", "buy", "publish", "transfer", "merge", "close issue")
_CAUTION_WORDS = ("submit", "create", "save", "type", "fill")


async def compile_workflow(utterance: str, site_hint: str) -> DraftWorkflow:
    """Create a complete draft or raise ``CompileError``; never return a partial draft."""
    if not utterance.strip() or not site_hint.strip():
        raise CompileError("Please provide both a workflow description and its starting URL.")
    parsed = urlparse(site_hint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CompileError("The starting URL must be a complete http(s) URL.")
    system = """Turn a short browser walkthrough request into a safe workflow draft.
Return JSON only with title, target_domain, entry_url, steps, ambiguities. Each step has
action (navigate/click/type/select/scroll/wait_for/assert), intent, optional value, and
narration_hint. Produce 3 to 6 reversible steps. Never include a send, publish,
delete, buy, purchase, merge, or other irreversible action. A final 'submit new issue'
step is permitted only when the teaching request explicitly says it is for the user's
dummy/test GitHub repository; mark it as caution. If important details are
missing, add plain-language questions to ambiguities rather than inventing them."""
    schema = DraftWorkflow.model_json_schema()
    raw = await complete(system, f"Starting URL: {site_hint}\n\nTeaching request: {utterance}",
                         json_schema=schema, max_tokens=900)
    if not isinstance(raw, dict):
        raise CompileError("The teaching model did not return a usable workflow.")
    try:
        draft = DraftWorkflow.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 - schema errors are a teaching failure, not a partial draft
        raise CompileError(f"The teaching model returned an invalid workflow: {exc}") from exc
    if urlparse(draft.entry_url).netloc != parsed.netloc or draft.target_domain != parsed.netloc:
        raise CompileError("The draft did not stay on the requested website.")
    if not 3 <= len(draft.steps) <= 6:
        raise CompileError("Teaching must produce a short workflow of 3 to 6 steps.")
    explicit_demo_submission = (
        parsed.netloc.lower().endswith("github.com")
        and "submit" in utterance.lower()
        and any(word in utterance.lower() for word in ("dummy", "test", "demo"))
    )
    safe_steps = []
    for step in draft.steps:
        lowered = step.intent.lower()
        if any(word in lowered for word in _DESTRUCTIVE_WORDS):
            raise CompileError("The requested workflow includes an irreversible action; remove it and teach again.")
        if "submit" in lowered and not explicit_demo_submission:
            raise CompileError("Submitting is allowed only when you explicitly confirm this is your dummy/test GitHub repo.")
        if not step.intent.strip() or not step.narration_hint.strip():
            raise CompileError("Every taught step needs an intent and narration hint.")
        risk = RiskLevel.CAUTION if any(word in lowered for word in _CAUTION_WORDS) else RiskLevel.SAFE
        safe_steps.append(step.model_copy(update={"risk": risk}))
    return draft.model_copy(update={"steps": safe_steps})
