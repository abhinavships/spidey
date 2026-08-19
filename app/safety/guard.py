"""app/safety/guard.py — M9. Pure, synchronous, never raises.

Usage:
    guard = Guard(["github.com"])
    verdict = guard.check(step, snapshot)
    if not verdict.allowed: ...
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import structlog

from app.schemas import ActionType, GuardVerdict, PageSnapshot, RiskLevel, Step

log = structlog.get_logger(__name__)

DESTRUCTIVE_PHRASES = (
    "delete permanently", "delete forever", "delete repository", "empty trash",
    "unsubscribe all", "confirm payment", "transfer ownership", "delete",
)

CAUTION_PHRASES = (
    "save", "submit", "create", "add", "update", "apply", "rename", "type", "fill",
)


def _compile(phrases: tuple[str, ...]) -> list[tuple[str, re.Pattern[str]]]:
    return [
        (p, re.compile(r"\b" + r"\s+".join(map(re.escape, p.split())) + r"\b", re.I))
        for p in phrases
    ]


_DESTRUCTIVE = _compile(DESTRUCTIVE_PHRASES)
_CAUTION = _compile(CAUTION_PHRASES)


def _host_allowed(host: str, allowed: list[str]) -> bool:
    """True when a URL's host is on the allowlist, port and www. notwithstanding.

    An allowed domain may be written with a port ("localhost:8000") because that
    is how a spec names a local site, while the host parsed off a URL never has
    one — so both sides are normalised before comparing.
    """
    host = (host or "").lower().removeprefix("www.").partition(":")[0]
    names = [a.lower().removeprefix("www.").partition(":")[0] for a in allowed]
    return any(host == name or host.endswith("." + name) for name in names if name)


class Guard:
    """Refuses irreversible actions and off-allowlist navigation.

    Usage:
        Guard(["github.com"]).check(step, snap)
    """

    def __init__(self, allowed_domains: list[str]) -> None:
        self.allowed_domains = list(allowed_domains)

    def _surface_text(self, step: Step) -> str:
        parts = [step.intent]
        if step.locator:
            parts.append(step.locator.intent)
            for c in step.locator.candidates:
                parts.extend(x for x in (c.name, c.value) if x)
        return " | ".join(parts)

    def check(self, step: Step, snap: PageSnapshot) -> GuardVerdict:
        """Decide whether this step may touch the page. Never raises.

        Usage:
            verdict = guard.check(step, snap)
        """
        try:
            return self._check(step, snap)
        except Exception as exc:  # noqa: BLE001 - boundary: refuse rather than crash
            log.exception("guard_crashed", step_id=step.id)
            return GuardVerdict(
                allowed=False, risk=RiskLevel.DESTRUCTIVE,
                reason=f"I couldn't safely evaluate this step, so I'm not doing it ({exc}).",
            )

    def _check(self, step: Step, snap: PageSnapshot) -> GuardVerdict:
        target_url = step.value if step.action is ActionType.NAVIGATE else snap.url
        host = urlsplit(target_url or "").hostname or ""
        if host and not _host_allowed(host, self.allowed_domains):
            return GuardVerdict(
                allowed=False, risk=RiskLevel.DESTRUCTIVE,
                reason=f"I won't go to {host} — I'm only allowed on "
                       f"{', '.join(self.allowed_domains)}.",
            )

        if step.risk is RiskLevel.DESTRUCTIVE:
            return GuardVerdict(
                allowed=False, risk=RiskLevel.DESTRUCTIVE,
                reason=f"'{step.intent}' is marked irreversible, so I'm not doing it.",
            )

        surface = self._surface_text(step)
        for phrase, pattern in _DESTRUCTIVE:
            if pattern.search(surface):
                return GuardVerdict(
                    allowed=False, risk=RiskLevel.DESTRUCTIVE,
                    reason=f"I won't do that — '{phrase}' can't be undone, "
                           f"and this is someone's real account.",
                )

        for phrase, pattern in _CAUTION:
            if pattern.search(surface):
                return GuardVerdict(
                    allowed=True, risk=RiskLevel.CAUTION,
                    reason=f"'{step.intent}' changes something, but it's reversible.",
                )

        return GuardVerdict(allowed=True, risk=RiskLevel.SAFE,
                            reason=f"'{step.intent}' just looks around — nothing changes.")
