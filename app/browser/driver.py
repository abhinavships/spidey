"""app/browser/driver.py — M1. The only place actions touch a page.

Attaches to a real Chrome over CDP (verified path) or launches its own for tests.

Usage:
    driver = Driver()
    await driver.start("https://github.com/", cdp_endpoint="http://localhost:9222")
    result = await driver.act(step, resolver, guard)
    await driver.stop()
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog

from app.schemas import (
    A11Y_DIGEST_LIMIT,
    VISIBLE_TEXT_LIMIT,
    ActionType,
    ActResult,
    BlockedAction,
    LocatorBundle,
    PageSnapshot,
    Step,
)

log = structlog.get_logger(__name__)


class Driver:
    """Drives one page. CDP-attached by default; detaches rather than closes."""

    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._attached = False

    # -- lifecycle --------------------------------------------------------

    async def start(self, entry_url: str, storage_state: str | None = None,
                    cdp_endpoint: str | None = None) -> None:
        """Attach to Chrome on cdp_endpoint, or launch a fresh browser.

        Usage:
            await driver.start("https://github.com/", cdp_endpoint="http://localhost:9222")
        """
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        if cdp_endpoint:
            try:
                self._browser = await self._pw.chromium.connect_over_cdp(cdp_endpoint)
            except Exception as exc:  # noqa: BLE001 - boundary: readable message for the UI
                await self._pw.stop()
                self._pw = None
                raise RuntimeError(
                    f"Could not attach to Chrome on {cdp_endpoint}. "
                    "Run ./launch_browser.sh (Chrome must be on port 9222) and retry."
                ) from exc
            self._context = self._browser.contexts[0]
            self._page = self._context.pages[0] if self._context.pages else \
                await self._context.new_page()
            self._attached = True
            log.info("cdp_attached", url=self._page.url)
        else:
            self._browser = await self._pw.chromium.launch(headless=False)
            self._context = await self._browser.new_context(storage_state=storage_state)
            self._page = await self._context.new_page()
            self._attached = False
            if entry_url:
                await self._page.goto(entry_url)

    async def stop(self) -> None:
        """Detach when attached over CDP; close fully when we launched it.

        Usage:
            await driver.stop()
        """
        try:
            if not self._attached and self._browser:
                await self._browser.close()
        except Exception as exc:  # noqa: BLE001 - shutdown must not raise
            log.warning("browser_close_failed", error=str(exc))
        try:
            if self._pw:
                await self._pw.stop()
        except Exception as exc:  # noqa: BLE001 - shutdown must not raise
            log.warning("playwright_stop_failed", error=str(exc))
        self._pw = self._browser = self._context = self._page = None

    # -- observation ------------------------------------------------------

    def _live_page(self) -> Any:
        """Re-read the active tab so a mid-demo tab switch doesn't break us."""
        if self._attached and self._context and self._context.pages:
            self._page = self._context.pages[-1]
        if self._page is None:
            raise RuntimeError("driver not started")
        return self._page

    async def snapshot(self) -> PageSnapshot:
        """Current url, title, a11y digest and visible text.

        Usage:
            snap = await driver.snapshot()
        """
        page = self._live_page()
        try:
            digest = await page.locator("body").aria_snapshot()
        except Exception as exc:  # noqa: BLE001 - a11y is advisory
            log.warning("aria_snapshot_failed", error=str(exc))
            digest = ""
        try:
            text = await page.locator("body").inner_text(timeout=3000)
        except Exception as exc:  # noqa: BLE001 - text is advisory
            log.warning("inner_text_failed", error=str(exc))
            text = ""
        return PageSnapshot(url=page.url, title=await page.title(),
                            a11y_digest=digest[:A11Y_DIGEST_LIMIT],
                            visible_text=text[:VISIBLE_TEXT_LIMIT])

    async def screenshot(self, path: str) -> str:
        """Save a PNG and return its path.

        Usage:
            await driver.screenshot("shots/step-3.png")
        """
        await self._live_page().screenshot(path=path)
        return path

    # -- finding ----------------------------------------------------------

    def _to_locator(self, page: Any, loc: Any) -> Any:
        s, v = loc.strategy, loc.value
        if s == "test_id":
            return page.get_by_test_id(v)
        if s == "role":
            return page.get_by_role(v, name=loc.name, exact=loc.exact) if loc.name \
                else page.get_by_role(v)
        if s == "label":
            return page.get_by_label(v, exact=loc.exact)
        if s == "placeholder":
            return page.get_by_placeholder(v, exact=loc.exact)
        if s == "text":
            return page.get_by_text(v, exact=loc.exact)
        return page.locator(v)

    async def find(self, bundle: LocatorBundle, timeout_ms: int) -> Any | None:
        """First candidate that resolves within timeout_ms, else None. Never raises.

        Usage:
            handle = await driver.find(step.locator, 8000)
        """
        page = self._live_page()
        per = max(800, timeout_ms // max(1, len(bundle.candidates)))
        for cand in bundle.candidates:
            try:
                target = self._to_locator(page, cand).first
                await target.wait_for(state="visible", timeout=per)
                return target
            except Exception:  # noqa: BLE001 - a miss is normal, try the next candidate
                continue
        return None

    # -- acting -----------------------------------------------------------

    async def act(self, step: Step, resolver: Any = None, guard: Any = None) -> ActResult:
        """Perform exactly one step. Only BlockedAction escapes.

        Usage:
            result = await driver.act(step, resolver, guard)
        """
        snap = await self.snapshot()
        if guard is not None:
            verdict = guard.check(step, snap)
            if not verdict.allowed:
                raise BlockedAction(verdict, step.id)

        before_url, before_title = snap.url, snap.title
        before_len = len(snap.visible_text)
        healed = False

        try:
            handle = None
            if step.locator is not None:
                handle = await self.find(step.locator, step.timeout_ms)
                if handle is None and resolver is not None:
                    fresh = await resolver.heal(step.locator, await self.snapshot())
                    if fresh is not None:
                        fresh.heal_count = step.locator.heal_count + 1
                        fresh.healed_at = datetime.now(timezone.utc)
                        step.locator = fresh
                        handle = await self.find(fresh, step.timeout_ms)
                        healed = handle is not None
                if handle is None:
                    return ActResult(ok=False, healed=False,
                                     error=f"couldn't find {step.locator.intent!r} on the page")

            await self._perform(step, handle)
            await asyncio.sleep(0.4)
            after = await self.snapshot()
            return ActResult(ok=True, healed=healed,
                             effect=self._describe(before_url, before_title,
                                                   before_len, after, step))
        except Exception as exc:  # noqa: BLE001 - boundary: a step failure is data, not a crash
            log.warning("act_failed", step_id=step.id, error=str(exc))
            return ActResult(ok=False, healed=healed, error=f"{type(exc).__name__}: {exc}")

    async def _perform(self, step: Step, handle: Any) -> None:
        page = self._live_page()
        a = step.action
        if a is ActionType.NAVIGATE:
            await page.goto(step.value, wait_until="domcontentloaded",
                            timeout=step.timeout_ms * 2)
        elif a is ActionType.CLICK:
            await handle.click(timeout=step.timeout_ms)
        elif a is ActionType.TYPE:
            await handle.fill(step.value or "", timeout=step.timeout_ms)
        elif a is ActionType.SELECT:
            await handle.select_option(step.value, timeout=step.timeout_ms)
        elif a is ActionType.SCROLL:
            await page.mouse.wheel(0, 600)
        elif a in (ActionType.WAIT_FOR, ActionType.ASSERT):
            if handle is not None:
                await handle.wait_for(state="visible", timeout=step.timeout_ms)

    @staticmethod
    def _describe(before_url: str, before_title: str, before_len: int,
                  after: PageSnapshot, step: Step) -> str:
        if after.url != before_url:
            return f"the page moved to {after.title or after.url}"
        if after.title != before_title:
            return f"the page is now '{after.title}'"
        delta = len(after.visible_text) - before_len
        if abs(delta) > 40:
            return "new content appeared on the page"
        return f"{step.intent} went through"
