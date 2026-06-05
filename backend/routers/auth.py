from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pathlib import Path
import httpx, time
from database import get_db, UserPrefs

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


@router.post("/google")
async def exchange_google_code(body: dict, db: Session = Depends(get_db)):
    code = body.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code.")

    env = _read_env()
    client_id     = env.get("GOOGLE_CLIENT_ID")
    client_secret = env.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="OAuth credentials not configured in Admin settings.")

    async with httpx.AsyncClient(timeout=10) as http:
        token_res = await http.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code":          code,
                "client_id":     client_id,
                "client_secret": client_secret,
                "redirect_uri":  "postmessage",
                "grant_type":    "authorization_code",
            },
        )

    if token_res.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {token_res.text}")

    td            = token_res.json()
    access_token  = td.get("access_token")
    refresh_token = td.get("refresh_token")
    expires_in    = td.get("expires_in", 3600)
    expiry_ms     = int((time.time() + expires_in) * 1000)

    async with httpx.AsyncClient(timeout=10) as http:
        info_res = await http.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if info_res.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to fetch user info from Google.")

    info  = info_res.json()
    email = info["email"]

    prefs = db.get(UserPrefs, email)

    if prefs and prefs.blocked:
        raise HTTPException(status_code=403, detail="Your account has been blocked by an administrator.")

    if not prefs:
        is_first = db.query(UserPrefs).count() == 0
        prefs = UserPrefs(
            email=email,
            selected_calendars=[{"id": email, "color": None}],
            role="owner" if is_first else "user",
        )
        db.add(prefs)
    if not prefs.selected_calendars:
        prefs.selected_calendars = [{"id": email, "color": None}]
    if not prefs.display_name:
        prefs.display_name = info.get("name", "")
    prefs.access_token = access_token
    if refresh_token:
        prefs.refresh_token = refresh_token
    prefs.token_expiry = expiry_ms
    db.commit()

    return {
        "email":        email,
        "name":         info.get("name"),
        "picture":      info.get("picture"),
        "token_expiry": expiry_ms,
        "role":         prefs.role or "user",
    }
