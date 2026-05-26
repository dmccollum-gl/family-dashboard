import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from database import SyncPeer, get_db, get_server_id
from services.sync_service import (
    check_peer,
    export_records,
    full_sync_with_peer,
    import_records,
    reconcile_all_peers,
)

router = APIRouter()


# ── Auth ──────────────────────────────────────────────────────────────────────

def _effective_sync_key() -> str:
    return settings.sync_key or settings.admin_api_key


def _require_sync(x_sync_key: str = Header(default="")):
    if x_sync_key != _effective_sync_key():
        raise HTTPException(status_code=403, detail="Invalid or missing X-Sync-Key header.")


def _require_admin(x_admin_key: str = Header(default="")):
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid or missing X-Admin-Key header.")


# ── Sync data endpoints (called by peers) ────────────────────────────────────

@router.get("/export", dependencies=[Depends(_require_sync)])
def export_data(
    since: Optional[str] = Query(default=None, description="ISO 8601 datetime — only return records updated after this"),
    db: Session = Depends(get_db),
):
    """Export all (or incremental) records for a peer to import."""
    since_dt: Optional[datetime] = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid 'since' datetime format.")

    return export_records(db, since=since_dt)


@router.post("/import", dependencies=[Depends(_require_sync)])
def import_data(payload: dict, db: Session = Depends(get_db)):
    """Accept records from a peer and merge them into the local database."""
    try:
        result = import_records(payload, db)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Import failed: {exc}")
    return result


# ── Peer management (admin only) ──────────────────────────────────────────────

class AddPeerRequest(BaseModel):
    url:           str
    name:          Optional[str] = None
    peer_sync_key: Optional[str] = None  # their sync key; defaults to our own if omitted


@router.get("/peers", dependencies=[Depends(_require_admin)])
def list_peers(db: Session = Depends(get_db)):
    peers = db.query(SyncPeer).order_by(SyncPeer.added_at).all()
    return {
        "server_id": get_server_id(),
        "peers": [
            {
                "id":               p.id,
                "url":              p.url,
                "name":             p.name,
                "active":           bool(p.active),
                "last_sync_at":     p.last_sync_at.isoformat() if p.last_sync_at else None,
                "last_sync_status": p.last_sync_status,
                "last_sync_error":  p.last_sync_error,
            }
            for p in peers
        ],
    }


@router.post("/peers", dependencies=[Depends(_require_admin)])
async def add_peer(req: AddPeerRequest, db: Session = Depends(get_db)):
    """Add a peer, test connectivity, then perform a full bidirectional sync."""
    url = req.url.rstrip("/")

    existing = db.query(SyncPeer).filter_by(url=url).first()
    if existing and existing.active:
        raise HTTPException(status_code=409, detail=f"Peer '{url}' is already registered.")

    # Test connectivity before saving
    connectivity = await check_peer(url, req.peer_sync_key)
    if not connectivity["ok"]:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot reach peer at {url}: {connectivity['error']}",
        )

    # Upsert
    if existing:
        existing.active        = 1
        existing.name          = req.name or existing.name
        existing.peer_sync_key = req.peer_sync_key
        peer = existing
    else:
        peer = SyncPeer(
            id            = str(uuid.uuid4()),
            url           = url,
            name          = req.name or connectivity.get("server_id"),
            peer_sync_key = req.peer_sync_key,
            added_at      = datetime.now(timezone.utc),
        )
        db.add(peer)
    db.commit()
    db.refresh(peer)

    # Full bidirectional sync
    sync_result = await full_sync_with_peer(peer)

    return {
        "peer_id":         peer.id,
        "url":             url,
        "peer_server_id":  connectivity.get("server_id"),
        "sync_result":     sync_result,
    }


@router.delete("/peers/{peer_id}", dependencies=[Depends(_require_admin)])
def remove_peer(peer_id: str, db: Session = Depends(get_db)):
    peer = db.get(SyncPeer, peer_id)
    if not peer:
        raise HTTPException(status_code=404, detail="Peer not found.")
    peer.active = 0
    db.commit()
    return {"peer_id": peer_id, "status": "deactivated"}


@router.post("/trigger", dependencies=[Depends(_require_admin)])
async def trigger_sync():
    """Manually trigger a full reconciliation pass against all active peers."""
    await reconcile_all_peers()
    return {"status": "ok"}
