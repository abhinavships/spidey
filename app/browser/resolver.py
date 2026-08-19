"""app/browser/resolver.py — turning a description of an element into locators.

``resolve()`` is what teaching leans on: a human says "the Work email field" and
this has to become something Playwright can click. The model is asked first, but
its answer is only ever the *first* candidate — plain-language guesses derived
from the words themselves follow it, so a step can still resolve when the model
is small, slow, or absent.

Usage:
    resolver = Resolver()
    bundle = await resolver.resolve("the Work email field", snap)
    fresh = await resolver.heal(step.locator, snap)
"""

from __future__ import annotations

import structlog

from app.llm import complete
from app.schemas import Locator, LocatorBundle, PageSnapshot

log = structlog.get_logger(__name__)

_ALLOWED = {"role", "test_id", "label", "placeholder", "text", "css"}

SYSTEM = (
    "You find web elements. Given an accessibility tree and a description of a "
    "target element, reply with the locator that finds it. Prefer test_id, then "
    "role, then label. Never invent an element that is not in the tree."
)

# Sent to the model so a constrained decoder cannot return an unusable strategy.
LOCATOR_SCHEMA = {
    "type": "object",
    "properties": {
        "strategy": {"type": "string", "enum": sorted(_ALLOWED)},
        "value": {"type": "string"},
        "name": {"type": ["string", "null"]},
        "exact": {"type": "boolean"},
    },
    "required": ["strategy", "value"],
}

# Words people attach to an element when describing it, which are never part of
# its accessible name: "the Work email field" is looking for "Work email".
_NOISE = ("the ", "a ", "an ")
_TRAILING = (" field", " button", " box", " input", " link", " dropdown",
             " checkbox", " menu", " icon", " option", " tab")


def _bare(intent: str) -> str:
    """The element's likely visible name, stripped of describing words."""
    text = intent.strip().strip('"\'')
    lowered = text.lower()
    for prefix in _NOISE:
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            lowered = text.lower()
            break
    for suffix in _TRAILING:
        if lowered.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text.strip()


def _guesses(intent: str) -> list[Locator]:
    """Deterministic candidates from the wording alone, best first.

    Usage:
        _guesses("the Work email field")  # -> label/placeholder/text "Work email"
    """
    name = _bare(intent)
    if not name:
        return [Locator(strategy="text", value=intent)]
    slug = name.lower().replace(" ", "-")
    return [
        Locator(strategy="label", value=name),
        Locator(strategy="placeholder", value=name),
        Locator(strategy="test_id", value=slug),
        Locator(strategy="role", value="button", name=name),
        Locator(strategy="text", value=name),
    ]


class Resolver:
    """Turns natural-language intent into locators using the live a11y tree."""

    async def resolve(self, intent: str, snap: PageSnapshot) -> LocatorBundle:
        """Best-effort bundle for an intent. Falls back to a text match.

        Usage:
            bundle = await resolver.resolve("the New issue button", snap)
        """
        asked = await self._ask(intent, snap)
        guesses = _guesses(intent)
        candidates = [asked, *guesses] if asked else guesses
        return LocatorBundle(intent=intent, candidates=candidates)

    async def heal(self, bundle: LocatorBundle, snap: PageSnapshot) -> LocatorBundle | None:
        """Fresh candidate after a miss, or None. Never raises.

        Usage:
            fresh = await resolver.heal(bundle, snap)
        """
        loc = await self._ask(bundle.intent, snap)
        if loc is None:
            return None
        return LocatorBundle(intent=bundle.intent,
                             candidates=[loc, *bundle.candidates],
                             heal_count=bundle.heal_count, healed_at=bundle.healed_at)

    async def _ask(self, intent: str, snap: PageSnapshot) -> Locator | None:
        user = (f"Page: {snap.title} ({snap.url})\n\n"
                f"Accessibility tree:\n{snap.a11y_digest[:3000]}\n\n"
                f"Target element: {intent}")
        out = await complete(SYSTEM, user, json_schema=LOCATOR_SCHEMA, max_tokens=300)
        if not isinstance(out, dict):
            return None
        strategy, value = out.get("strategy"), out.get("value")
        if strategy not in _ALLOWED or not isinstance(value, str) or not value.strip():
            log.warning("heal_rejected", got=out)
            return None
        name = out.get("name")
        return Locator(strategy=strategy, value=value,
                       name=name if isinstance(name, str) and name else None,
                       exact=bool(out.get("exact", False)))
