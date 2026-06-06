from fastapi import APIRouter
from pathlib import Path
import json, subprocess, threading

router = APIRouter()

CONFIG_PATH = Path(__file__).parent.parent / "dashboard_config.json"
ENV_PATH    = Path(__file__).parent.parent / ".env"
_MASKED = "••••••••"


def _read_env() -> dict:
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def _write_env(updates: dict):
    env = _read_env()
    env.update({k: v for k, v in updates.items() if v})
    ENV_PATH.write_text("\n".join(f"{k}={v}" for k, v in env.items()) + "\n")


def _read_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text())


def _write_config(data: dict):
    existing = _read_config()
    existing.update(data)
    CONFIG_PATH.write_text(json.dumps(existing, indent=2))


# ── OAuth ─────────────────────────────────────────────────────────────────────

@router.get("/oauth")
def get_oauth_config():
    env = _read_env()
    return {
        "client_id":     env.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": _MASKED if env.get("GOOGLE_CLIENT_SECRET") else "",
        "configured":    bool(env.get("GOOGLE_CLIENT_ID") and env.get("GOOGLE_CLIENT_SECRET")),
    }


@router.put("/oauth")
def save_oauth_config(body: dict):
    updates = {}
    if body.get("client_id"):
        updates["GOOGLE_CLIENT_ID"] = body["client_id"]
    if body.get("client_secret") and body["client_secret"] != _MASKED:
        updates["GOOGLE_CLIENT_SECRET"] = body["client_secret"]
    if updates:
        _write_env(updates)
    return {"status": "saved", "restart_required": True}


# ── Weather ────────────────────────────────────────────────────────────────────

@router.get("/weather")
def get_weather_config():
    cfg = _read_config()
    return {
        "location":   cfg.get("owm_location", ""),
        "units":      cfg.get("owm_units", "imperial"),
        "configured": bool(cfg.get("owm_location")),
    }


@router.put("/weather")
def save_weather_config(body: dict):
    updates = {}
    if "location" in body:
        new_loc = body["location"]
        updates["owm_location"] = new_loc
        # Clear geocoding cache if address changed so it re-geocodes on next fetch.
        if new_loc != _read_config().get("owm_location"):
            updates["_geo_for"] = None
            updates["_geo_lat"] = None
            updates["_geo_lon"] = None
    if "units" in body:
        updates["owm_units"] = body["units"]
    if updates:
        _write_config(updates)
    return {"status": "saved"}


# ── RSS Feeds ──────────────────────────────────────────────────────────────────

@router.get("/rss")
def get_rss_config():
    cfg = _read_config()
    return {
        "feeds":       cfg.get("rss_feeds",   []),
        "mode":        cfg.get("rss_mode",    "shuffle"),
        "dad_jokes":   cfg.get("dad_jokes",   True),
        "hacker_news": cfg.get("hacker_news", True),
    }


@router.put("/rss")
def save_rss_config(body: dict):
    feeds = body.get("feeds", [])
    cleaned = [
        {"url": f["url"].strip(), "label": f.get("label", "").strip()}
        for f in feeds
        if f.get("url", "").strip()
    ]
    updates: dict = {"rss_feeds": cleaned}
    if body.get("mode") in {"shuffle", "rotate"}:
        updates["rss_mode"] = body["mode"]
    if "dad_jokes" in body:
        updates["dad_jokes"] = bool(body["dad_jokes"])
    if "hacker_news" in body:
        updates["hacker_news"] = bool(body["hacker_news"])
    _write_config(updates)
    return {"status": "saved", "count": len(cleaned)}


# ── Display settings ───────────────────────────────────────────────────────────

VALID_THEMES        = {"auto", "light", "dark"}
VALID_VIEWS         = {"day", "week", "2week", "month", "rolling"}
VALID_WEATHER_VIEWS = {"daily", "hourly"}


@router.get("/display")
def get_display_config():
    cfg = _read_config()
    return {
        "theme":        cfg.get("display_theme",        "auto"),
        "view":         cfg.get("display_view",         "week"),
        "weather_view": cfg.get("display_weather_view", "daily"),
        "custom_fqdn":  cfg.get("custom_fqdn",          ""),
    }


@router.put("/display")
def save_display_config(body: dict):
    updates = {}
    if body.get("theme") in VALID_THEMES:
        updates["display_theme"] = body["theme"]
    if body.get("view") in VALID_VIEWS:
        updates["display_view"] = body["view"]
    if body.get("weather_view") in VALID_WEATHER_VIEWS:
        updates["display_weather_view"] = body["weather_view"]
    if "custom_fqdn" in body:
        updates["custom_fqdn"] = body["custom_fqdn"].strip()
    if updates:
        _write_config(updates)
    return {"status": "saved"}


# ── Permissions ───────────────────────────────────────────────────────────────

SECTIONS = [
    "weather_location",
    "pi_display",
    "family_calendars",
    "family_members",
    "rss_feeds",
    "restart_services",
]
DEFAULT_PERMISSIONS = {
    "admin": SECTIONS[:],
    "user":  ["family_calendars"],
}


@router.get("/permissions")
def get_permissions():
    cfg = _read_config()
    perms = cfg.get("permissions", {})
    return {
        "sections": SECTIONS,
        "admin":    perms.get("admin", DEFAULT_PERMISSIONS["admin"]),
        "user":     perms.get("user",  DEFAULT_PERMISSIONS["user"]),
    }


@router.put("/permissions")
def save_permissions(body: dict):
    cfg = _read_config()
    perms = dict(cfg.get("permissions", {}))
    if "admin" in body and isinstance(body["admin"], list):
        perms["admin"] = [s for s in body["admin"] if s in SECTIONS]
    if "user" in body and isinstance(body["user"], list):
        perms["user"] = [s for s in body["user"] if s in SECTIONS]
    _write_config({"permissions": perms})
    return {"status": "saved"}


# ── Restart ────────────────────────────────────────────────────────────────────

def _after(delay: float, fn):
    import time
    time.sleep(delay)
    fn()


@router.post("/restart/backend")
def restart_backend():
    threading.Thread(
        target=_after,
        args=(1.0, lambda: subprocess.run(["sudo", "systemctl", "restart", "dashboard-backend"], check=False)),
        daemon=True,
    ).start()
    return {"status": "restarting"}


@router.post("/restart/display")
def restart_display():
    # display.py runs as the same user (dashboard), so pkill works without sudo.
    # The .bash_profile while-loop restarts it automatically after 5 s.
    threading.Thread(
        target=_after,
        args=(1.0, lambda: subprocess.run(["pkill", "-f", "display.py"], check=False)),
        daemon=True,
    ).start()
    return {"status": "restarting"}
