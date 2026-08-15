"""app/browser/resolver.py — M2, Demo 1 scope.

`heal()` asks the model for a fresh locator when the saved ones miss. With no
API key it returns None and the step simply fails — the saved-locator path is
what Demo 1 is proving. Hermes replaces this file at Demo 3.

Usage:
    resolver = Resolver()
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
    "target element, reply with JSON: "
    '{"strategy": one of role|test_id|label|placeholder|text|css, '
    '"value": string, "name": string or null, "exact": bool}. '
    "Prefer test_id, then role. Never invent an element that is not in the tree."
)


class Resolver:
    """Turns natural-language intent into locators using the live a11y tree."""

    async def resolve(self, intent: str, snap: PageSnapshot) -> LocatorBundle:
        """Best-effort bundle for an intent. Falls back to a text match.

        Usage:
            bundle = await resolver.resolve("the New issue button", snap)
        """
        healed = await self._ask(intent, snap)
        candidates = [healed] if healed else [Locator(strategy="text", value=intent)]
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
        out = await complete(SYSTEM, user, json_schema={"type": "object"}, max_tokens=300)
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
