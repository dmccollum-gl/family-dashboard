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
        "api_key":  _MASKED if cfg.get("owm_api_key") else "",
        "location": cfg.get("owm_location", ""),
        "units":    cfg.get("owm_units", "imperial"),
        "configured": bool(cfg.get("owm_api_key") and cfg.get("owm_location")),
    }


@router.put("/weather")
def save_weather_config(body: dict):
    updates = {}
    if body.get("api_key") and body["api_key"] != _MASKED:
        updates["owm_api_key"] = body["api_key"]
    if "location" in body:
        updates["owm_location"] = body["location"]
    if "units" in body:
        updates["owm_units"] = body["units"]
    if updates:
        _write_config(updates)
    return {"status": "saved"}


# ── RSS Feeds ──────────────────────────────────────────────────────────────────

@router.get("/rss")
def get_rss_config():
    cfg = _read_config()
    return {"feeds": cfg.get("rss_feeds", [])}


@router.put("/rss")
def save_rss_config(body: dict):
    feeds = body.get("feeds", [])
    # Each feed: {"url": "...", "label": "..."}
    cleaned = [
        {"url": f["url"].strip(), "label": f.get("label", "").strip()}
        for f in feeds
        if f.get("url", "").strip()
    ]
    _write_config({"rss_feeds": cleaned})
    return {"status": "saved", "count": len(cleaned)}


# ── Display settings ───────────────────────────────────────────────────────────

VALID_THEMES = {"auto", "light", "dark"}
VALID_VIEWS  = {"day", "week", "2week", "month", "rolling"}


@router.get("/display")
def get_display_config():
    cfg = _read_config()
    return {
        "theme": cfg.get("display_theme", "auto"),
        "view":  cfg.get("display_view",  "week"),
    }


@router.put("/display")
def save_display_config(body: dict):
    updates = {}
    if body.get("theme") in VALID_THEMES:
        updates["display_theme"] = body["theme"]
    if body.get("view") in VALID_VIEWS:
        updates["display_view"] = body["view"]
    if updates:
        _write_config(updates)
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
