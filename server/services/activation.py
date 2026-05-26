import secrets
import string
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy import text

from database import ActivationCode


def generate_codes(count: int, db: Session) -> list[str]:
    alphabet = string.ascii_uppercase + string.digits
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(alphabet) for _ in range(16))
        code = f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}"
        now = datetime.now(timezone.utc)
        db.add(ActivationCode(code=code, issued_at=now, updated_at=now))
        codes.append(code)
    db.commit()
    return codes


def validate_and_use(code: str, hostname: str, db: Session) -> bool:
    """Atomically mark a single-use code as used. Returns True only if the code
    was valid and unused (WHERE used=0 guard prevents races)."""
    now = datetime.now(timezone.utc)
    result = db.execute(
        text(
            "UPDATE activation_codes "
            "SET used=1, used_at=:now, used_by_hostname=:hostname, updated_at=:now "
            "WHERE code=:code AND used=0"
        ),
        {"now": now, "hostname": hostname, "code": code},
    )
    db.commit()
    return result.rowcount == 1
