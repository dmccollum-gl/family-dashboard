from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from database import ActivationCode, ProvisionedDevice, get_db
from services.activation import generate_codes

router = APIRouter()


def _require_admin(x_admin_key: str = Header(default="")):
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid or missing X-Admin-Key header.")


class GenerateCodesRequest(BaseModel):
    count: int = 10


@router.post("/activation-codes", dependencies=[Depends(_require_admin)])
def create_activation_codes(req: GenerateCodesRequest, db: Session = Depends(get_db)):
    if req.count < 1 or req.count > 500:
        raise HTTPException(status_code=400, detail="count must be between 1 and 500.")
    codes = generate_codes(req.count, db)
    return {"codes": codes, "count": len(codes)}


@router.get("/tunnels", dependencies=[Depends(_require_admin)])
def list_tunnels(db: Session = Depends(get_db)):
    records = db.query(ProvisionedDevice).order_by(ProvisionedDevice.created_at.desc()).all()
    return {
        "tunnels": [
            {
                "hostname":     r.hostname,
                "fqdn":         r.fqdn,
                "tunnel_id":    r.tunnel_id,
                "device_id":    r.device_id,
                "status":       r.status,
                "created_at":   r.created_at.isoformat() if r.created_at else None,
                "last_seen":    r.last_seen.isoformat() if r.last_seen else None,
            }
            for r in records
        ]
    }


@router.get("/activation-codes", dependencies=[Depends(_require_admin)])
def list_activation_codes(db: Session = Depends(get_db)):
    codes = db.query(ActivationCode).order_by(ActivationCode.issued_at.desc()).all()
    return {
        "codes": [
            {
                "code":             c.code,
                "issued_at":        c.issued_at.isoformat() if c.issued_at else None,
                "used":             bool(c.used),
                "used_at":          c.used_at.isoformat() if c.used_at else None,
                "used_by_hostname": c.used_by_hostname,
            }
            for c in codes
        ]
    }
