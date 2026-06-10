from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pathlib import Path
import httpx, time
from database import get_db, UserPrefs
from auth_deps import current_user

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


def _resolve_account(db: Session, email: str) -> UserPrefs:
    """Apply the allow-list policy and return the UserPrefs row to sign in.

    Policy:
      • If the email already has a row (invited during setup, or a returning
        user), allow it.
      • Else if the database is completely empty, bootstrap this email as the
        owner (covers a fresh install where nobody was pre-invited).
      • Otherwise the dashboard is private and this email was never authorized
        — reject with 403.
    """
    prefs = db.get(UserPrefs, email)

    if prefs and prefs.blocked:
        raise HTTPException(status_code=403, detail="Your account has been blocked by an administrator.")

    if prefs:
        return prefs

    if db.query(UserPrefs).count() == 0:
        prefs = UserPrefs(
            email=email,
            selected_calendars=[{"id": email, "color": None}],
            role="owner",
        )
        db.add(prefs)
        return prefs

    raise HTTPException(
        status_code=403,
        detail="This dashboard is private. Ask the owner to add your email address before signing in.",
    )


def _establish_session(request: Request, prefs: UserPrefs):
    """Record the verified identity in the signed session cookie."""
    request.session["email"] = prefs.email
    request.session["role"] = prefs.role or "user"


async def _fetch_userinfo(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as http:
        info_res = await http.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if info_res.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to verify your Google account.")
    return info_res.json()


@router.post("/google")
async def exchange_google_code(body: dict, request: Request, db: Session = Depends(get_db)):
    """Auth-code (popup) flow: exchange the code server-side, then sign in.

    Preferred flow — yields a refresh token and verifies everything on the
    backend before establishing the session.
    """
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

    info  = await _fetch_userinfo(access_token)
    email = info["email"]

    # Allow-list check (raises 403 if not authorized).
    prefs = _resolve_account(db, email)

    if not prefs.selected_calendars:
        prefs.selected_calendars = [{"id": email, "color": None}]
    if not prefs.display_name:
        prefs.display_name = info.get("name", "")
    prefs.access_token = access_token
    if refresh_token:
        prefs.refresh_token = refresh_token
    prefs.token_expiry = expiry_ms
    db.commit()

    _establish_session(request, prefs)

    return {
        "email":        email,
        "name":         info.get("name"),
        "picture":      info.get("picture"),
        "token_expiry": expiry_ms,
        "role":         prefs.role or "user",
    }


@router.post("/session")
async def establish_token_session(body: dict, request: Request, db: Session = Depends(get_db)):
    """Implicit-token (fallback) flow: verify the access token, then sign in.

    Used when no client secret is configured. The token is re-verified against
    Google here so the backend — not the client — decides who the user is and
    whether they're allowed in. Replaces the old unauthenticated PUT path.
    """
    access_token = body.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="Missing access token.")
    expires_in = int(body.get("expires_in") or 3600)
    expiry_ms  = int((time.time() + expires_in) * 1000)

    info  = await _fetch_userinfo(access_token)
    email = info["email"]

    # Allow-list check (raises 403 if not authorized).
    prefs = _resolve_account(db, email)

    if not prefs.display_name:
        prefs.display_name = info.get("name", "")
    if not prefs.selected_calendars:
        prefs.selected_calendars = [{"id": email, "color": None}]
    # Only the user's own sign-in may set calendars passed from the client.
    if isinstance(body.get("selected_calendars"), list):
        prefs.selected_calendars = body["selected_calendars"]
    prefs.access_token = access_token
    prefs.token_expiry = expiry_ms
    db.commit()

    _establish_session(request, prefs)

    return {
        "email":        email,
        "name":         info.get("name"),
        "picture":      info.get("picture"),
        "token_expiry": expiry_ms,
        "role":         prefs.role or "user",
    }


@router.get("/me")
def whoami(request: Request, db: Session = Depends(get_db)):
    """Return the signed-in identity (source of truth for the frontend on load).

    Returns {authenticated: false} rather than 401 so the UI can render a
    signed-out state without treating it as an error.
    """
    email = request.session.get("email")
    if not email:
        return {"authenticated": False}
    user = db.get(UserPrefs, email)
    if not user or user.blocked:
        request.session.clear()
        return {"authenticated": False}
    return {
        "authenticated": True,
        "email":         user.email,
        "name":          user.display_name or "",
        "role":          user.role or "user",
    }


@router.post("/logout")
def logout(request: Request):
    """Clear the server session."""
    request.session.clear()
    return {"status": "signed_out"}
