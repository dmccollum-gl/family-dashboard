from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db, UserPrefs
from typing import List

router = APIRouter()


def _normalize_calendars(raw: list) -> list:
    """Convert legacy ["id"] entries to [{"id": ..., "color": null}] objects."""
    return [c if isinstance(c, dict) else {"id": c, "color": None} for c in (raw or [])]


@router.get("/{email}")
def get_user_prefs(email: str, db: Session = Depends(get_db)):
    prefs = db.get(UserPrefs, email)
    if not prefs:
        return {"email": email, "display_name": "", "display_color": "#1976d2", "selected_calendars": []}
    return {
        "email":               prefs.email,
        "display_name":        prefs.display_name or "",
        "display_color":       prefs.display_color or "#1976d2",
        "selected_calendars":  _normalize_calendars(prefs.selected_calendars),
    }


@router.put("/{email}")
def save_user_prefs(email: str, body: dict, db: Session = Depends(get_db)):
    prefs = db.get(UserPrefs, email)
    if not prefs:
        prefs = UserPrefs(email=email)
        db.add(prefs)
    if "display_name" in body:
        prefs.display_name = body["display_name"]
    if "display_color" in body:
        prefs.display_color = body["display_color"]
    if "selected_calendars" in body:
        prefs.selected_calendars = body["selected_calendars"]
    if "access_token" in body:
        prefs.access_token = body["access_token"]
    if "token_expiry" in body:
        prefs.token_expiry = body["token_expiry"]
    db.commit()
    return {"status": "saved"}


@router.get("")
def list_users(db: Session = Depends(get_db)):
    users = db.query(UserPrefs).all()
    return [
        {
            "email":        u.email,
            "display_name": u.display_name or "",
            "display_color": u.display_color or "#1976d2",
            "has_token":    bool(u.access_token),
            "has_refresh":  bool(u.refresh_token),
        }
        for u in users
    ]


@router.delete("/{email}")
def delete_user(email: str, db: Session = Depends(get_db)):
    prefs = db.get(UserPrefs, email)
    if not prefs:
        raise HTTPException(status_code=404, detail="User not found.")
    db.delete(prefs)
    db.commit()
    return {"status": "deleted"}
