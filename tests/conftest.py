"""Test isolation: a developer's .env must never decide what the suite tests.

``app.server`` calls ``load_dotenv()`` at import, so provider keys from a real
.env would otherwise leak in and change which branch of ``app.llm`` runs.
"""

from __future__ import annotations

import pytest

PROVIDER_ENV = (
    "WA_LOCAL_MODEL", "WA_LOCAL_BASE", "WA_LOCAL_KEY", "WA_LOCAL_TIMEOUT",
    "WA_LOCAL_REASONING", "GROQ_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY",
)


@pytest.fixture(autouse=True)
def _no_ambient_providers(monkeypatch):
    """Every test starts with no model provider configured unless it sets one."""
    for name in PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
