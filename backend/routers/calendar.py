import asyncio
import base64
import re
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
_last_good_events: dict | None = None  # survives cache expiry; returned when Google is unreachable
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


# Consecutive refresh failures per user (in-memory). After _MAX_REFRESH_FAILURES
# strikes the stored token is deleted so the user is signed out and must sign in
# again, instead of looping forever on a dead token. Reset on any success.
_refresh_failures: dict[str, int] = {}
_MAX_REFRESH_FAILURES = 3


def _clear_user_token(email: str) -> None:
    """Delete a user's stored Google token — logs them out of calendar access."""
    db = SessionLocal()
    try:
        p = db.get(UserPrefs, email)
        if p:
            p.access_token  = None
            p.refresh_token = None
            p.token_expiry  = None
            db.commit()
    finally:
        db.close()


async def _note_refresh_failure(email: str) -> None:
    """Record a failed refresh; after 3 strikes, delete the dead token."""
    n = _refresh_failures.get(email, 0) + 1
    _refresh_failures[email] = n
    if n >= _MAX_REFRESH_FAILURES:
        await asyncio.to_thread(_clear_user_token, email)
        _refresh_failures.pop(email, None)


async def _get_valid_token(user: UserPrefs, env: dict) -> str | None:
    """Return a valid access token, refreshing via refresh_token if needed.

    If the refresh fails _MAX_REFRESH_FAILURES times in a row, the stored token
    is deleted (the user is signed out) rather than retried indefinitely.
    """
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
            await _note_refresh_failure(user.email)
            return None
        td          = res.json()
        new_token   = td.get("access_token")
        expires_in  = td.get("expires_in", 3600)
        expiry_ms   = int((datetime.now(timezone.utc).timestamp() + expires_in) * 1000)
        if not new_token:
            await _note_refresh_failure(user.email)
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
        _refresh_failures.pop(user.email, None)   # success → clear the strike count
        return new_token
    except Exception:
        await _note_refresh_failure(user.email)
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
            users = db.query(UserPrefs).filter(UserPrefs.blocked != 1).all()
        finally:
            db.close()

        env = _read_env()
        now = datetime.now(timezone.utc)
        # Fetch one week back so timed events on past days of the current view are visible.
        week_ago = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        time_min = start or week_ago.isoformat()
        time_max = end   or (now + timedelta(days=8)).isoformat()

        all_events     = []
        expired_users  = []
        network_errors = 0  # count Google API connection failures

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
                    network_errors += 1

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
                        network_errors += 1
                        continue

        global _last_good_events

        # If Google was unreachable and we have a prior good result, serve it rather
        # than caching an empty list that would blank the display.
        if network_errors and not all_events and _last_good_events is not None:
            return _last_good_events

        result = {"events": all_events, "expired_users": expired_users}
        if all_events:
            _last_good_events = result
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

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(
                "https://www.googleapis.com/calendar/v3/users/me/calendarList",
                params={"maxResults": "250"},
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception:
        raise HTTPException(status_code=503, detail="Could not reach Google Calendar API.")
    if res.status_code != 200:
        raise HTTPException(status_code=422, detail="Failed to fetch calendar list from Google.")

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

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.delete(
                f"https://www.googleapis.com/calendar/v3/users/me/calendarList/{quote(calendar_id, safe='')}",
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception:
        raise HTTPException(status_code=503, detail="Could not reach Google Calendar API.")

    if res.status_code not in (200, 204):
        try:
            detail = res.json().get("error", {}).get("message", "Failed to unsubscribe.")
        except Exception:
            detail = "Failed to unsubscribe."
        raise HTTPException(status_code=422, detail=detail)

    return {"status": "unsubscribed"}


def _calendar_share_url(calendar_id: str) -> str:
    """Construct the Google Calendar sharing link for a given calendar ID."""
    cid = base64.urlsafe_b64encode(calendar_id.encode()).decode().rstrip("=")
    return f"https://calendar.google.com/calendar/r?cid={cid}"


@router.post("/subscription/{email}")
async def subscribe_calendar(email: str, body: dict):
    calendar_id  = body.get("calendar_id")
    owner_email  = body.get("owner_email")  # caller passes this when assigning from their own calendar list
    if not calendar_id:
        raise HTTPException(status_code=400, detail="Missing calendar_id.")

    db = SessionLocal()
    try:
        user  = db.get(UserPrefs, email)
        owner = db.get(UserPrefs, owner_email) if (owner_email and owner_email != email) else None
    finally:
        db.close()

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    env   = _read_env()
    token = await _get_valid_token(user, env)
    if not token:
        raise HTTPException(status_code=401, detail="Token expired — please sign in again.")

    async def _subscribe():
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                return await client.post(
                    "https://www.googleapis.com/calendar/v3/users/me/calendarList",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"id": calendar_id},
                )
        except Exception:
            raise HTTPException(status_code=503, detail="Could not reach Google Calendar API.")

    res = await _subscribe()

    if res.status_code in (200, 201):
        return {"status": "subscribed"}

    # Non-404 Google error — surface it directly.
    if res.status_code != 404:
        try:
            detail = res.json().get("error", {}).get("message", "") or "Failed to subscribe."
        except Exception:
            detail = "Failed to subscribe."
        raise HTTPException(status_code=422, detail=detail)

    # 404: calendar not accessible to the target user.
    # Try granting access via the owner's token if we have it.
    acl_denied = False  # True when owner exists but lacks ACL write permission

    owner_token = await _get_valid_token(owner, env) if owner else None
    if owner_token:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                acl_res = await client.post(
                    f"https://www.googleapis.com/calendar/v3/calendars/{quote(calendar_id, safe='')}/acl",
                    headers={"Authorization": f"Bearer {owner_token}"},
                    json={"role": "reader", "scope": {"type": "user", "value": email}},
                )
            if acl_res.status_code in (200, 201):
                # Access granted — retry the subscription.
                res = await _subscribe()
                if res.status_code in (200, 201):
                    return {"status": "subscribed"}
            elif acl_res.status_code == 403:
                # Owner can't manage ACL — they subscribed to this calendar themselves
                # (it belongs to someone outside the dashboard).
                acl_denied = True
        except HTTPException:
            raise
        except Exception:
            pass

    # For imported ICS calendars, try to recover the original subscription URL from the
    # owner's calendar metadata and reuse it to subscribe the target user directly.
    if "@import.calendar.google.com" in calendar_id and owner_token:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                meta_res = await client.get(
                    f"https://www.googleapis.com/calendar/v3/users/me/calendarList/{quote(calendar_id, safe='')}",
                    headers={"Authorization": f"Bearer {owner_token}"},
                )
            if meta_res.status_code == 200:
                meta = meta_res.json()
                ics_url = None
                for val in meta.values():
                    if isinstance(val, str):
                        m = re.search(r'webcal://\S+|https?://\S+?\.ics\b\S*', val, re.IGNORECASE)
                        if m:
                            ics_url = m.group(0).rstrip(".,;)>\"'").replace("webcal://", "https://")
                            break
                if ics_url:
                    async with httpx.AsyncClient(timeout=10) as client:
                        ics_res = await client.post(
                            "https://www.googleapis.com/calendar/v3/users/me/calendarList",
                            headers={"Authorization": f"Bearer {token}"},
                            json={"id": ics_url},
                        )
                    if ics_res.status_code in (200, 201):
                        return {"status": "subscribed"}
        except Exception:
            pass

    # Can't subscribe automatically — return the sharing URL so the frontend can guide the user.
    return {"status": "share_required", "share_url": _calendar_share_url(calendar_id)}
