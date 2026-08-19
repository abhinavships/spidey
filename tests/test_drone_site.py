"""SkyLoop: sign-in, role gating, and the three role workspaces."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.server import app

PAGES = {"vp": "/drone/vp/readiness", "tech": "/drone/tech/missions",
         "marketing": "/drone/marketing/campaigns"}
EMAILS = {"vp": "vp@skyloop.io", "tech": "tech@skyloop.io", "marketing": "mkt@skyloop.io"}


def signed_in(role: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/drone/login", data={"email": EMAILS[role], "password": "flightdemo"})
    assert response.status_code == 200 and response.url.path == PAGES[role]
    return client


@pytest.mark.parametrize("role", list(PAGES))
def test_each_role_lands_on_its_own_workspace(role):
    assert signed_in(role).get("/drone/api/me").json()["role"] == role


@pytest.mark.parametrize("role", list(PAGES))
def test_a_role_cannot_open_another_roles_page(role):
    client = signed_in(role)
    for other, page in PAGES.items():
        if other == role:
            continue
        assert client.get(page).url.path == PAGES[role], f"{role} reached {other}'s page"


def test_signed_out_visitors_are_sent_to_the_login():
    client = TestClient(app)
    assert client.get("/drone/tech/missions").url.path == "/drone/login"
    assert client.get("/drone/api/me").status_code == 401


def test_a_wrong_password_starts_no_session():
    client = TestClient(app)
    response = client.post("/drone/login", data={"email": EMAILS["vp"], "password": "nope"})
    assert "error=1" in str(response.url)
    assert client.get("/drone/api/me").status_code == 401


def test_a_forged_session_cookie_is_refused():
    """The cookie is signed, so editing it to another user proves nothing."""
    import base64

    from app.drone import COOKIE

    client = TestClient(app)
    forged = base64.urlsafe_b64encode(b"vp@skyloop.io").decode() + ".deadbeef" * 4
    client.cookies.set(COOKIE, forged)
    assert client.get("/drone/api/me").status_code == 401


def test_preflight_only_clears_a_mission_inside_every_safety_envelope():
    from app.drone import _readiness

    good = {"altitude_m": 90, "speed_ms": 6, "rth_altitude_m": 55, "battery_floor_pct": 30,
            "checklist": ["battery", "props", "compass", "airspace"]}
    assert _readiness(good)["cleared"]
    for bad in ({"altitude_m": 150}, {"speed_ms": 20}, {"rth_altitude_m": 10},
                {"battery_floor_pct": 5}, {"checklist": ["battery"]}):
        assert not _readiness({**good, **bad})["cleared"], bad


def test_the_technical_api_is_closed_to_the_other_roles():
    for role in ("vp", "marketing"):
        response = signed_in(role).post("/drone/api/missions", json={"name": "sneaky"})
        assert response.url.path == PAGES[role]
