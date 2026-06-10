from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db, UserPrefs
from auth_deps import current_user, require_admin

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
def list_users(db: Session = Depends(get_db), me: UserPrefs = Depends(current_user)):
    return [_prefs_dict(u) for u in db.query(UserPrefs).all()]


@router.get("/{email}")
def get_user_prefs(email: str, db: Session = Depends(get_db),
                   me: UserPrefs = Depends(current_user)):
    prefs = db.get(UserPrefs, email)
    if not prefs:
        raise HTTPException(status_code=404, detail="User not found.")
    return _prefs_dict(prefs)


@router.put("/{email}")
def save_user_prefs(email: str, body: dict, db: Session = Depends(get_db),
                    me: UserPrefs = Depends(current_user)):
    is_self  = (email == me.email)
    is_admin = (me.role or "user") in ("admin", "owner")
    if not is_self and not is_admin:
        raise HTTPException(status_code=403, detail="You can only edit your own settings.")

    prefs = db.get(UserPrefs, email)
    if not prefs:
        # Only admins/owner may create rows for other emails; users only self.
        if not is_self and not is_admin:
            raise HTTPException(status_code=403, detail="Not allowed.")
        prefs = UserPrefs(email=email)
        db.add(prefs)
    elif prefs.blocked and is_self:
        raise HTTPException(status_code=403, detail="Account is blocked.")

    if "display_name" in body:
        prefs.display_name = body["display_name"]
    if "display_color" in body:
        prefs.display_color = body["display_color"]
    if "selected_calendars" in body:
        prefs.selected_calendars = body["selected_calendars"]
    # OAuth tokens may only be written for one's own account. Normal sign-in
    # now goes through /api/auth/session, but keep this guard regardless.
    if is_self:
        if "access_token" in body:
            prefs.access_token = body["access_token"]
        if "token_expiry" in body:
            prefs.token_expiry = body["token_expiry"]
    db.commit()
    return {"status": "saved"}


@router.post("/invite")
def invite_user(body: dict, db: Session = Depends(get_db),
                me: UserPrefs = Depends(require_admin)):
    """Pre-authorize an email so it can sign in (allow-list entry).

    Creates a token-less UserPrefs row. Owner may invite admins or users;
    admins may invite users only.
    """
    email = (body.get("email") or "").strip().lower()
    role  = body.get("role", "user")
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email address is required.")
    if role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="role must be 'admin' or 'user'.")
    if role == "admin" and (me.role or "user") != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can invite admins.")

    if db.get(UserPrefs, email):
        raise HTTPException(status_code=409, detail="That email is already authorized.")

    db.add(UserPrefs(
        email=email,
        role=role,
        selected_calendars=[{"id": email, "color": None}],
    ))
    db.commit()
    return {"status": "invited", "email": email, "role": role}


@router.patch("/{email}/role")
def set_role(email: str, body: dict, db: Session = Depends(get_db),
             me: UserPrefs = Depends(require_admin)):
    new_role = body.get("role")
    if new_role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="role must be 'admin' or 'user'.")

    target = db.get(UserPrefs, email)
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    if target.role == "owner":
        raise HTTPException(status_code=403, detail="Cannot change owner's role.")
    # admin can only grant/revoke user role, not create new admins
    if (me.role or "user") == "admin" and new_role == "admin":
        raise HTTPException(status_code=403, detail="Admins cannot promote to admin.")
    # only owner can demote an admin
    if target.role == "admin" and (me.role or "user") != "owner":
        raise HTTPException(status_code=403, detail="Only owner can demote an admin.")

    target.role = new_role
    db.commit()
    return {"status": "updated", "role": new_role}


@router.patch("/{email}/blocked")
def set_blocked(email: str, body: dict, db: Session = Depends(get_db),
                me: UserPrefs = Depends(require_admin)):
    blocked = body.get("blocked")
    if not isinstance(blocked, bool):
        raise HTTPException(status_code=400, detail="blocked must be a boolean.")

    target = db.get(UserPrefs, email)
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    if target.role == "owner":
        raise HTTPException(status_code=403, detail="Cannot block the owner.")

    target.blocked = 1 if blocked else 0
    db.commit()
    return {"status": "updated", "blocked": blocked}


@router.delete("/{email}")
def delete_user(email: str, db: Session = Depends(get_db),
                me: UserPrefs = Depends(current_user)):
    prefs = db.get(UserPrefs, email)
    if not prefs:
        raise HTTPException(status_code=404, detail="User not found.")
    # Users may remove their own account; removing anyone else requires admin.
    if email != me.email and (me.role or "user") not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="You can only remove your own account.")
    if prefs.role == "owner":
        raise HTTPException(status_code=403, detail="Cannot delete the owner account.")
    db.delete(prefs)
    db.commit()
    return {"status": "deleted"}
