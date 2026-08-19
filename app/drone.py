"""app/drone.py — SkyLoop, the drone-training site the walkthrough agent drives.

A small three-role web app: a technical flight engineer plans missions, a vice
president reads fleet readiness outcomes, and marketing drafts course campaigns.
It exists so the agent has a site we control — stable ``data-testid`` hooks, no
login CAPTCHAs, nothing destructive that a demo can trip over.

Usage:
    from app.drone import router
    app.include_router(router)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

UI_DIR = Path(__file__).resolve().parent.parent / "ui" / "drone"
COOKIE = "skyloop_session"
SECRET = os.getenv("WA_DRONE_SECRET", "skyloop-demo-secret").encode()
# Demo password for all three seeded accounts. Override for anything real.
DEMO_PASSWORD = os.getenv("WA_DRONE_PASSWORD", "flightdemo")

router = APIRouter(prefix="/drone")


def _hash(password: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), SECRET, 100_000).hex()


USERS: dict[str, dict[str, str]] = {
    "vp@skyloop.io": {"name": "Priya Raghavan", "role": "vp", "title": "Vice President, Flight Ops",
                      "password": _hash(DEMO_PASSWORD)},
    "tech@skyloop.io": {"name": "Arjun Mehta", "role": "tech", "title": "Lead Flight Engineer",
                        "password": _hash(DEMO_PASSWORD)},
    "mkt@skyloop.io": {"name": "Dana Okoye", "role": "marketing", "title": "Growth Marketing Manager",
                       "password": _hash(DEMO_PASSWORD)},
}

HOME = {"vp": "/drone/vp/readiness", "tech": "/drone/tech/missions", "marketing": "/drone/marketing/campaigns"}

# ponytail: in-memory store, seeded at import. A restart resets the demo, which
# is a feature during rehearsals. Swap for SQLite the day two people share it.
MISSIONS: list[dict[str, Any]] = [
    {"id": "MSN-1041", "name": "Ridgeline survey", "aircraft": "SkyLoop X4",
     "status": "flown", "altitude_m": 90, "speed_ms": 7, "created": "2026-07-02"},
    {"id": "MSN-1042", "name": "Solar farm inspection", "aircraft": "SkyLoop X4",
     "status": "draft", "altitude_m": 60, "speed_ms": 5, "created": "2026-08-04"},
]
CAMPAIGNS: list[dict[str, Any]] = [
    {"id": "CMP-204", "name": "Monsoon intake — Beginner Pilot", "course": "beginner",
     "segment": "hobbyist", "status": "draft", "created": "2026-08-01"},
]

FLEET = [
    {"tail": "X4-01", "model": "SkyLoop X4", "hours": 412, "battery_health": 0.91, "status": "ready"},
    {"tail": "X4-02", "model": "SkyLoop X4", "hours": 388, "battery_health": 0.74, "status": "ready"},
    {"tail": "X4-03", "model": "SkyLoop X4", "hours": 967, "battery_health": 0.52, "status": "maintenance"},
    {"tail": "H2-07", "model": "SkyLoop Heavy 2", "hours": 145, "battery_health": 0.96, "status": "ready"},
    {"tail": "H2-08", "model": "SkyLoop Heavy 2", "hours": 201, "battery_health": 0.88, "status": "grounded"},
]

QUARTERS = {
    "2026-Q1": {"pilots_certified": 34, "pilots_enrolled": 61, "incidents": 3,
                "flight_hours": 1180, "cost_per_hour": 148, "target_certified": 40},
    "2026-Q2": {"pilots_certified": 47, "pilots_enrolled": 72, "incidents": 2,
                "flight_hours": 1465, "cost_per_hour": 132, "target_certified": 45},
    "2026-Q3": {"pilots_certified": 29, "pilots_enrolled": 80, "incidents": 1,
                "flight_hours": 990, "cost_per_hour": 127, "target_certified": 50},
}

COURSES = {
    "beginner": "Beginner Pilot Certification",
    "commercial": "Commercial Operations (Part 107)",
    "thermal": "Thermal & Inspection Specialist",
}
SEGMENTS = {
    "hobbyist": "Hobbyist pilots",
    "enterprise": "Enterprise fleet teams",
    "government": "Public safety & government",
}


# -- session ---------------------------------------------------------------

def _sign(email: str) -> str:
    payload = base64.urlsafe_b64encode(email.encode()).decode()
    mac = hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}.{mac}"


def _unsign(token: str | None) -> str | None:
    """Return the email a cookie proves, or None if it proves nothing."""
    if not token or "." not in token:
        return None
    payload, _, mac = token.partition(".")
    expected = hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        email = base64.urlsafe_b64decode(payload.encode()).decode()
    except Exception:  # noqa: BLE001 - a malformed cookie is simply not a session
        return None
    return email if email in USERS else None


def current_user(request: Request) -> dict[str, str] | None:
    """The signed-in user for this request, or None."""
    email = _unsign(request.cookies.get(COOKIE))
    if email is None:
        return None
    return {"email": email, **{k: v for k, v in USERS[email].items() if k != "password"}}


def _deny(request: Request, *roles: str):
    """Redirect/403 response when the caller may not see a role's page, else None."""
    user = current_user(request)
    if user is None:
        return RedirectResponse("/drone/login", status_code=303)
    if roles and user["role"] not in roles:
        return RedirectResponse(HOME[user["role"]] + "?denied=1", status_code=303)
    return None


def _page(name: str) -> FileResponse:
    return FileResponse(UI_DIR / name)


# -- auth ------------------------------------------------------------------

@router.get("/")
async def root(request: Request):
    """Send a signed-in user to their workspace, everyone else to the login."""
    user = current_user(request)
    return RedirectResponse(HOME[user["role"]] if user else "/drone/login", status_code=303)


@router.get("/login")
async def login_page():
    """The sign-in screen."""
    return _page("login.html")


@router.post("/login")
async def login(request: Request):
    """Check credentials and start a session."""
    # Parsed by hand so the app needs no multipart dependency for one form.
    form = parse_qs((await request.body()).decode())
    email = (form.get("email", [""])[0]).strip().lower()
    password = form.get("password", [""])[0]
    record = USERS.get(email)
    if record is None or not hmac.compare_digest(record["password"], _hash(password)):
        return RedirectResponse("/drone/login?error=1", status_code=303)
    response = RedirectResponse(HOME[record["role"]], status_code=303)
    response.set_cookie(COOKIE, _sign(email), httponly=True, samesite="lax")
    return response


@router.post("/logout")
async def logout():
    """End the session."""
    response = RedirectResponse("/drone/login", status_code=303)
    response.delete_cookie(COOKIE)
    return response


@router.get("/api/me")
async def api_me(request: Request):
    """Who is signed in, for the page shell to render."""
    user = current_user(request)
    if user is None:
        return JSONResponse({"error": "not signed in"}, status_code=401)
    return user


# -- technical: mission planning ------------------------------------------

@router.get("/tech/missions")
async def tech_missions(request: Request):
    """Mission planner — flight engineers only."""
    return _deny(request, "tech") or _page("missions.html")


@router.get("/api/missions")
async def api_missions(request: Request):
    """Every saved mission."""
    return _deny(request, "tech") or {"missions": MISSIONS}


@router.post("/api/missions")
async def api_create_mission(request: Request):
    """Save a mission draft and return it with its computed preflight score."""
    denied = _deny(request, "tech")
    if denied:
        return denied
    body = await request.json()
    mission = {
        "id": f"MSN-{1043 + len(MISSIONS)}",
        "name": (body.get("name") or "Untitled mission").strip(),
        "aircraft": body.get("aircraft", "SkyLoop X4"),
        "altitude_m": body.get("altitude_m", 0),
        "speed_ms": body.get("speed_ms", 0),
        "overlap_pct": body.get("overlap_pct", 0),
        "geofence_m": body.get("geofence_m", 0),
        "rth_altitude_m": body.get("rth_altitude_m", 0),
        "battery_floor_pct": body.get("battery_floor_pct", 0),
        "checklist": body.get("checklist", []),
        "status": "draft",
        "created": datetime.now(timezone.utc).date().isoformat(),
    }
    MISSIONS.append(mission)
    return {"saved": mission, "readiness": _readiness(mission)}


def _readiness(mission: dict[str, Any]) -> dict[str, Any]:
    """Preflight readiness: every checklist item plus parameters inside safe envelopes.

    Usage:
        _readiness({"altitude_m": 90, "checklist": ["battery"]})
    """
    checks = {
        "checklist_complete": len(mission.get("checklist", [])) >= 4,
        "altitude_within_limit": 0 < int(mission.get("altitude_m") or 0) <= 120,
        "speed_within_limit": 0 < float(mission.get("speed_ms") or 0) <= 12,
        "rth_above_obstacles": int(mission.get("rth_altitude_m") or 0) >= 40,
        "battery_reserve_set": int(mission.get("battery_floor_pct") or 0) >= 25,
    }
    passed = sum(checks.values())
    return {"checks": checks, "passed": passed, "total": len(checks),
            "cleared": passed == len(checks)}


# -- vice president: outcomes ---------------------------------------------

@router.get("/vp/readiness")
async def vp_readiness(request: Request):
    """Fleet readiness and training outcomes — VP only."""
    return _deny(request, "vp") or _page("readiness.html")


@router.get("/api/readiness")
async def api_readiness(request: Request, quarter: str = "2026-Q2"):
    """Outcome metrics for one quarter, plus fleet availability."""
    denied = _deny(request, "vp")
    if denied:
        return denied
    stats = QUARTERS.get(quarter, QUARTERS["2026-Q2"])
    ready = sum(1 for aircraft in FLEET if aircraft["status"] == "ready")
    return {
        "quarter": quarter,
        "quarters": sorted(QUARTERS),
        "fleet_ready_pct": round(100 * ready / len(FLEET)),
        "fleet": FLEET,
        "certification_rate_pct": round(100 * stats["pilots_certified"] / stats["pilots_enrolled"]),
        "against_target_pct": round(100 * stats["pilots_certified"] / stats["target_certified"]),
        "incidents_per_1k_hours": round(1000 * stats["incidents"] / stats["flight_hours"], 2),
        **stats,
    }


@router.post("/api/report")
async def api_report(request: Request):
    """Compose the quarterly outcome summary the VP hands upward."""
    denied = _deny(request, "vp")
    if denied:
        return denied
    body = await request.json()
    quarter = body.get("quarter", "2026-Q2")
    stats = QUARTERS.get(quarter, QUARTERS["2026-Q2"])
    ready = sum(1 for aircraft in FLEET if aircraft["status"] == "ready")
    lines = [
        f"{quarter}: {stats['pilots_certified']} pilots certified against a target of {stats['target_certified']}.",
        f"Fleet availability {round(100 * ready / len(FLEET))}% ({ready} of {len(FLEET)} airframes ready).",
        f"{stats['incidents']} incidents across {stats['flight_hours']} flight hours.",
        f"Cost per flight hour ${stats['cost_per_hour']}, training pipeline {stats['pilots_enrolled']} enrolled.",
    ]
    return {"quarter": quarter, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "summary": lines}


# -- marketing: campaigns --------------------------------------------------

@router.get("/marketing/campaigns")
async def marketing_campaigns(request: Request):
    """Course campaign composer — marketing only."""
    return _deny(request, "marketing") or _page("campaigns.html")


@router.get("/api/campaigns")
async def api_campaigns(request: Request):
    """Saved campaigns, plus the courses and segments to choose from."""
    return _deny(request, "marketing") or {
        "campaigns": CAMPAIGNS, "courses": COURSES, "segments": SEGMENTS}


@router.post("/api/campaigns")
async def api_create_campaign(request: Request):
    """Save a campaign as a draft. Publishing is deliberately not offered."""
    denied = _deny(request, "marketing")
    if denied:
        return denied
    body = await request.json()
    campaign = {
        "id": f"CMP-{205 + len(CAMPAIGNS)}",
        "name": (body.get("name") or "Untitled campaign").strip(),
        "course": body.get("course", "beginner"),
        "segment": body.get("segment", "hobbyist"),
        "channels": body.get("channels", []),
        "send_on": body.get("send_on", ""),
        "headline": body.get("headline", ""),
        "status": "draft",
        "created": datetime.now(timezone.utc).date().isoformat(),
    }
    CAMPAIGNS.append(campaign)
    return {"saved": campaign, "reach_estimate": 1200 + 400 * len(campaign["channels"])}
