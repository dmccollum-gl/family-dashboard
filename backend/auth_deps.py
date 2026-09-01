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


def _owner_exists(db: Session) -> bool:
    return db.query(UserPrefs).filter(UserPrefs.role == "owner").count() > 0


def require_owner_or_bootstrap(request: Request, db: Session = Depends(get_db)):
    """Allow the owner — OR anyone, while no owner exists yet (first-time setup).

    A fresh device has no owner, and nobody can sign in until the OAuth
    credentials and (for remote access) an FQDN are configured. Those setup
    endpoints must therefore be reachable *before* the first login. As soon as
    an owner is established this behaves exactly like require_owner.

    Returns the owner UserPrefs when signed in, or None during the bootstrap
    window (endpoints only need it to gate access, not to identify the caller).
    """
    email = request.session.get("email")
    user  = db.get(UserPrefs, email) if email else None
    if user and not user.blocked and (user.role or "user") == "owner":
        return user
    if _owner_exists(db):
        raise HTTPException(status_code=403, detail="Only the owner can do this.")
    return None  # bootstrap: no owner yet — allow so setup can proceed
