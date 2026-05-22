import asyncio
import time
from fastapi import APIRouter, HTTPException
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from pathlib import Path
import httpx
from database import SessionLocal, UserPrefs

router = APIRouter()

ENV_PATH = Path(__file__).parent.parent / ".env"

# In-memory cache + lock for /events — prevents concurrent fetches from all hitting Google.
_events_cache: dict = {"data": None, "ts": 0.0}
_events_lock: asyncio.Lock | None = None  # created lazily (can't create at module import time)
_CACHE_TTL = 90  # seconds


def _get_events_lock() -> asyncio.Lock:
    global _events_lock
    if _events_lock is None:
        _events_lock = asyncio.Lock()
    return _events_lock


def _read_env() -> dict:
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


async def _get_valid_token(user: UserPrefs, env: dict) -> str | None:
    """Return a valid access token, refreshing via refresh_token if needed."""
    now_ms    = _now_ms()
    buffer_ms = 5 * 60 * 1000

    if user.access_token and user.token_expiry and user.token_expiry > now_ms + buffer_ms:
        return user.access_token

    if not user.refresh_token:
        return None

    client_id     = env.get("GOOGLE_CLIENT_ID")
    client_secret = env.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None

    try:
        async with httpx.AsyncClient(verify=False, timeout=10) as client:
            res = await client.post("https://oauth2.googleapis.com/token", data={
                "grant_type":    "refresh_token",
                "refresh_token": user.refresh_token,
                "client_id":     client_id,
                "client_secret": client_secret,
            })
        if res.status_code != 200:
            return None
        td          = res.json()
        new_token   = td.get("access_token")
        expires_in  = td.get("expires_in", 3600)
        expiry_ms   = int((datetime.now(timezone.utc).timestamp() + expires_in) * 1000)
        if not new_token:
            return None

        def _update_db():
            db = SessionLocal()
            try:
                p = db.get(UserPrefs, user.email)
                if p:
                    p.access_token = new_token
                    p.token_expiry = expiry_ms
                    db.commit()
            finally:
                db.close()

        await asyncio.to_thread(_update_db)
        return new_token
    except Exception:
        return None


@router.get("/events")
async def get_all_events(start: str = None, end: str = None):
    use_cache = not start and not end

    # Fast path: return cached result without acquiring lock.
    if use_cache and _events_cache["data"] is not None:
        if time.monotonic() - _events_cache["ts"] < _CACHE_TTL:
            return _events_cache["data"]

    # Serialize concurrent fetches: second caller waits, then hits the cache the
    # first caller just populated — avoids duplicate Google API bursts.
    async with _get_events_lock():
        if use_cache and _events_cache["data"] is not None:
            if time.monotonic() - _events_cache["ts"] < _CACHE_TTL:
                return _events_cache["data"]

        db = SessionLocal()
        try:
            users = db.query(UserPrefs).filter(UserPrefs.access_token.isnot(None)).all()
        finally:
            db.close()

        env = _read_env()
        now = datetime.now(timezone.utc)
        # Fetch one week back so timed events on past days of the current view are visible.
        week_ago = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        time_min = start or week_ago.isoformat()
        time_max = end   or (now + timedelta(days=8)).isoformat()

        all_events    = []
        expired_users = []

        async with httpx.AsyncClient(timeout=10) as client:
            for user in users:
                token = await _get_valid_token(user, env)
                if not token:
                    expired_users.append(user.display_name or user.email)
                    continue

                # Normalize selected_calendars: support both legacy ["id"] and new [{"id","color"}] formats.
                raw_cals = user.selected_calendars or [user.email]
                cal_configs = [
                    c if isinstance(c, dict) else {"id": c, "color": None}
                    for c in raw_cals
                ]

                cal_names = {}
                try:
                    list_res = await client.get(
                        "https://www.googleapis.com/calendar/v3/users/me/calendarList",
                        params={"maxResults": "250"},
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    if list_res.status_code == 200:
                        for cal in list_res.json().get("items", []):
                            cal_names[cal["id"]] = cal.get("summaryOverride") or cal.get("summary") or cal["id"]
                except Exception:
                    pass

                for cfg in cal_configs:
                    cal_id    = cfg["id"]
                    cal_color = cfg.get("color") or user.display_color or "#1976d2"
                    cal_name  = cal_names.get(cal_id, cal_id)
                    try:
                        res = await client.get(
                            f"https://www.googleapis.com/calendar/v3/calendars/{quote(cal_id, safe='')}/events",
                            params={
                                "timeMin":      time_min,
                                "timeMax":      time_max,
                                "singleEvents": "true",
                                "orderBy":      "startTime",
                                "maxResults":   "250",
                            },
                            headers={"Authorization": f"Bearer {token}"},
                        )
                        if res.status_code != 200:
                            continue
                        for ev in res.json().get("items", []):
                            if ev.get("status") == "cancelled":
                                continue
                            ev_start = ev.get("start", {})
                            ev_end   = ev.get("end",   {})
                            all_day  = "date" in ev_start and "dateTime" not in ev_start
                            all_events.append({
                                "id":           ev["id"] + cal_id,
                                "title":        ev.get("summary", "(no title)"),
                                "start":        ev_start.get("dateTime") or ev_start.get("date"),
                                "end":          ev_end.get("dateTime")   or ev_end.get("date"),
                                "allDay":       all_day,
                                "color":        cal_color,
                                "userName":     user.display_name  or user.email,
                                "calendarName": cal_name,
                            })
                    except Exception:
                        continue

        result = {"events": all_events, "expired_users": expired_users}
        if use_cache:
            _events_cache["data"] = result
            _events_cache["ts"]   = time.monotonic()
        return result


@router.get("/list/{email}")
async def get_calendar_list(email: str):
    db = SessionLocal()
    try:
        user = db.get(UserPrefs, email)
    finally:
        db.close()

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    env   = _read_env()
    token = await _get_valid_token(user, env)
    if not token:
        raise HTTPException(status_code=401, detail="Token expired — please sign in again.")

    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(
            "https://www.googleapis.com/calendar/v3/users/me/calendarList",
            params={"maxResults": "250"},
            headers={"Authorization": f"Bearer {token}"},
        )
    if res.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to fetch calendar list from Google.")

    return {"calendars": res.json().get("items", [])}


@router.delete("/subscription/{email}/{calendar_id:path}")
async def unsubscribe_calendar(email: str, calendar_id: str):
    db = SessionLocal()
    try:
        user = db.get(UserPrefs, email)
    finally:
        db.close()

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    env   = _read_env()
    token = await _get_valid_token(user, env)
    if not token:
        raise HTTPException(status_code=401, detail="Token expired — please sign in again.")

    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.delete(
            f"https://www.googleapis.com/calendar/v3/users/me/calendarList/{quote(calendar_id, safe='')}",
            headers={"Authorization": f"Bearer {token}"},
        )

    if res.status_code not in (200, 204):
        try:
            detail = res.json().get("error", {}).get("message", "Failed to unsubscribe.")
        except Exception:
            detail = "Failed to unsubscribe."
        raise HTTPException(status_code=502, detail=detail)

    return {"status": "unsubscribed"}


@router.post("/subscription/{email}")
async def subscribe_calendar(email: str, body: dict):
    calendar_id = body.get("calendar_id")
    if not calendar_id:
        raise HTTPException(status_code=400, detail="Missing calendar_id.")

    db = SessionLocal()
    try:
        user = db.get(UserPrefs, email)
    finally:
        db.close()

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    env   = _read_env()
    token = await _get_valid_token(user, env)
    if not token:
        raise HTTPException(status_code=401, detail="Token expired — please sign in again.")

    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.post(
            "https://www.googleapis.com/calendar/v3/users/me/calendarList",
            headers={"Authorization": f"Bearer {token}"},
            json={"id": calendar_id},
        )

    if res.status_code not in (200, 201):
        try:
            detail = res.json().get("error", {}).get("message", "Failed to subscribe.")
        except Exception:
            detail = "Failed to subscribe."
        raise HTTPException(status_code=502, detail=detail)

    return {"status": "subscribed"}
