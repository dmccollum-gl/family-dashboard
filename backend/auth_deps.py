"""
Server-side authentication dependencies.

Identity lives in the signed session cookie (set by routers/auth.py on a
verified Google sign-in), NOT in any client-supplied value. Every privileged
endpoint depends on one of the helpers below so the backend — not the React
UI — is the real security boundary.

Roles: "owner" > "admin" > "user". The owner is the first account to sign in
(or the account seeded during setup); only allow-listed emails may sign in at
all (enforced in routers/auth.py).
"""
from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session

from database import get_db, UserPrefs


def current_user(request: Request, db: Session = Depends(get_db)) -> UserPrefs:
    """Resolve the signed-in user from the session cookie, or 401."""
    email = request.session.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    user = db.get(UserPrefs, email)
    if not user:
        # Session points at a user that was deleted — treat as signed out.
        request.session.clear()
        raise HTTPException(status_code=401, detail="Session expired. Sign in again.")
    if user.blocked:
        request.session.clear()
        raise HTTPException(status_code=403, detail="Your account has been blocked.")
    return user


def require_role(*allowed: str):
    """Dependency factory: require the signed-in user to hold one of *allowed.*"""
    def _dep(user: UserPrefs = Depends(current_user)) -> UserPrefs:
        if (user.role or "user") not in allowed:
            raise HTTPException(
                status_code=403,
                detail="You don't have permission to do this.",
            )
        return user
    return _dep


# Common gates
require_owner = require_role("owner")
require_admin = require_role("owner", "admin")
