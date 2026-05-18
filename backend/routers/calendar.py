from fastapi import APIRouter, HTTPException
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from pathlib import Path
import httpx
from database import SessionLocal, UserPrefs

router = APIRouter()

ENV_PATH = Path(__file__).parent.parent / ".env"


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


def _get_valid_token(user: UserPrefs, env: dict) -> str | None:
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
        # Use httpx (verify=False) to bypass macOS Python SSL cert issues
        with httpx.Client(verify=False, timeout=10) as client:
            res = client.post("https://oauth2.googleapis.com/token", data={
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

        db = SessionLocal()
        try:
            p = db.get(UserPrefs, user.email)
            if p:
                p.access_token = new_token
                p.token_expiry = expiry_ms
                db.commit()
        finally:
            db.close()

        return new_token
    except Exception:
        return None


@router.get("/events")
async def get_all_events(start: str = None, end: str = None):
    db = SessionLocal()
    try:
        users = db.query(UserPrefs).filter(UserPrefs.access_token.isnot(None)).all()
    finally:
        db.close()

    env = _read_env()
    now = datetime.now(timezone.utc)
    time_min = start or now.isoformat()
    time_max = end   or (now + timedelta(days=7)).isoformat()

    all_events   = []
    expired_users = []

    async with httpx.AsyncClient(timeout=10) as client:
        for user in users:
            token = _get_valid_token(user, env)
            if not token:
                expired_users.append(user.display_name or user.email)
                continue

            cal_ids = user.selected_calendars or [user.email]

            # Fetch calendar list once per user to get display names
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

            for cal_id in cal_ids:
                cal_name = cal_names.get(cal_id, cal_id)
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
                        start   = ev.get("start", {})
                        end     = ev.get("end",   {})
                        all_day = "date" in start and "dateTime" not in start
                        all_events.append({
                            "id":           ev["id"] + cal_id,
                            "title":        ev.get("summary", "(no title)"),
                            "start":        start.get("dateTime") or start.get("date"),
                            "end":          end.get("dateTime")   or end.get("date"),
                            "allDay":       all_day,
                            "color":        user.display_color or "#1976d2",
                            "userName":     user.display_name  or user.email,
                            "calendarName": cal_name,
                        })
                except Exception:
                    continue

    return {"events": all_events, "expired_users": expired_users}


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
    token = _get_valid_token(user, env)
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
    token = _get_valid_token(user, env)
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
    token = _get_valid_token(user, env)
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
