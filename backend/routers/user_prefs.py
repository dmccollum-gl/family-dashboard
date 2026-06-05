from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db, UserPrefs

router = APIRouter()


def _normalize_calendars(raw: list) -> list:
    return [c if isinstance(c, dict) else {"id": c, "color": None} for c in (raw or [])]


def _prefs_dict(u: UserPrefs) -> dict:
    return {
        "email":               u.email,
        "display_name":        u.display_name or "",
        "display_color":       u.display_color or "#1976d2",
        "selected_calendars":  _normalize_calendars(u.selected_calendars),
        "role":                u.role or "user",
        "blocked":             bool(u.blocked),
        "has_token":           bool(u.access_token),
        "has_refresh":         bool(u.refresh_token),
    }


@router.get("")
def list_users(db: Session = Depends(get_db)):
    return [_prefs_dict(u) for u in db.query(UserPrefs).all()]


@router.get("/{email}")
def get_user_prefs(email: str, db: Session = Depends(get_db)):
    prefs = db.get(UserPrefs, email)
    if not prefs:
        raise HTTPException(status_code=404, detail="User not found.")
    return _prefs_dict(prefs)


@router.put("/{email}")
def save_user_prefs(email: str, body: dict, db: Session = Depends(get_db)):
    prefs = db.get(UserPrefs, email)
    if not prefs:
        prefs = UserPrefs(email=email)
        db.add(prefs)
    elif prefs.blocked:
        raise HTTPException(status_code=403, detail="Account is blocked.")
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


@router.patch("/{email}/role")
def set_role(email: str, body: dict, db: Session = Depends(get_db)):
    new_role = body.get("role")
    requester_email = body.get("requester")
    if new_role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="role must be 'admin' or 'user'.")

    requester = db.get(UserPrefs, requester_email) if requester_email else None
    if not requester or requester.role not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Insufficient permissions.")

    target = db.get(UserPrefs, email)
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    if target.role == "owner":
        raise HTTPException(status_code=403, detail="Cannot change owner's role.")
    # admin can only grant/revoke user role, not create new admins
    if requester.role == "admin" and new_role == "admin":
        raise HTTPException(status_code=403, detail="Admins cannot promote to admin.")
    # only owner can demote an admin
    if target.role == "admin" and requester.role != "owner":
        raise HTTPException(status_code=403, detail="Only owner can demote an admin.")

    target.role = new_role
    db.commit()
    return {"status": "updated", "role": new_role}


@router.patch("/{email}/blocked")
def set_blocked(email: str, body: dict, db: Session = Depends(get_db)):
    blocked = body.get("blocked")
    requester_email = body.get("requester")
    if not isinstance(blocked, bool):
        raise HTTPException(status_code=400, detail="blocked must be a boolean.")

    requester = db.get(UserPrefs, requester_email) if requester_email else None
    if not requester or requester.role not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Insufficient permissions.")

    target = db.get(UserPrefs, email)
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    if target.role == "owner":
        raise HTTPException(status_code=403, detail="Cannot block the owner.")

    target.blocked = 1 if blocked else 0
    db.commit()
    return {"status": "updated", "blocked": blocked}


@router.delete("/{email}")
def delete_user(email: str, db: Session = Depends(get_db)):
    prefs = db.get(UserPrefs, email)
    if not prefs:
        raise HTTPException(status_code=404, detail="User not found.")
    if prefs.role == "owner":
        raise HTTPException(status_code=403, detail="Cannot delete the owner account.")
    db.delete(prefs)
    db.commit()
    return {"status": "deleted"}
