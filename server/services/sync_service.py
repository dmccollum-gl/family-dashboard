"""
Peer sync service.

Each server exposes GET /api/sync/export and POST /api/sync/import.
After a successful provision/deprovision, changes are pushed to all active
peers immediately (push-on-write).  A background loop also runs every
SYNC_INTERVAL_SECONDS as a reconciliation pass to catch anything missed
(e.g. peer was offline during the push).

Merge rules
-----------
activation_codes:
  - If the code exists locally and used=1, never revert to used=0 — used
    is a one-way latch.
  - Otherwise the record with the later updated_at wins.

provisioned_devices:
  - The record with the later updated_at wins for all fields except
    tunnel_token_encrypted — we keep the local value to avoid overwriting
    a valid token with one from a server that can't decrypt it.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from config import settings
from database import (
    ActivationCode,
    ProvisionedDevice,
    SessionLocal,
    SyncPeer,
    get_server_id,
)

log = logging.getLogger("sync")


# ── Auth helper ───────────────────────────────────────────────────────────────

def _effective_sync_key() -> str:
    return settings.sync_key or settings.admin_api_key


def _sync_headers(peer_key: Optional[str] = None) -> dict:
    key = peer_key or _effective_sync_key()
    return {"X-Sync-Key": key, "Content-Type": "application/json"}


# ── Export ────────────────────────────────────────────────────────────────────

def export_records(db: Session, since: Optional[datetime] = None) -> dict:
    """Return all records (or those updated since `since`) for a peer to import."""
    def _q_devices():
        q = db.query(ProvisionedDevice)
        if since:
            q = q.filter(ProvisionedDevice.updated_at >= since)
        return q.all()

    def _q_codes():
        q = db.query(ActivationCode)
        if since:
            q = q.filter(ActivationCode.updated_at >= since)
        return q.all()

    return {
        "server_id":   get_server_id(),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "devices": [
            {
                "id":                     d.id,
                "hostname":               d.hostname,
                "fqdn":                   d.fqdn,
                "tunnel_id":              d.tunnel_id,
                "tunnel_token_encrypted": d.tunnel_token_encrypted,
                "dns_record_id":          d.dns_record_id,
                "device_id":              d.device_id,
                "activation_code":        d.activation_code,
                "status":                 d.status,
                "created_at":             d.created_at.isoformat() if d.created_at else None,
                "updated_at":             d.updated_at.isoformat() if d.updated_at else None,
                "origin_server_id":       d.origin_server_id,
            }
            for d in _q_devices()
        ],
        "activation_codes": [
            {
                "code":              c.code,
                "issued_at":         c.issued_at.isoformat() if c.issued_at else None,
                "updated_at":        c.updated_at.isoformat() if c.updated_at else None,
                "used_at":           c.used_at.isoformat() if c.used_at else None,
                "used_by_hostname":  c.used_by_hostname,
                "used":              c.used,
                "origin_server_id":  c.origin_server_id,
            }
            for c in _q_codes()
        ],
    }


# ── Import / merge ────────────────────────────────────────────────────────────

def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def import_records(payload: dict, db: Session) -> dict:
    """Merge records from a peer into the local database."""
    devices_imported = 0
    codes_imported   = 0

    # -- Activation codes ------------------------------------------------------
    for c in payload.get("activation_codes", []):
        remote_updated = _parse_dt(c.get("updated_at"))
        existing = db.get(ActivationCode, c["code"])

        if existing is None:
            db.add(ActivationCode(
                code             = c["code"],
                issued_at        = _parse_dt(c.get("issued_at")),
                updated_at       = remote_updated,
                used_at          = _parse_dt(c.get("used_at")),
                used_by_hostname = c.get("used_by_hostname"),
                used             = c.get("used", 0),
                origin_server_id = c.get("origin_server_id"),
            ))
            codes_imported += 1
        else:
            local_updated = existing.updated_at or datetime.min.replace(tzinfo=timezone.utc)
            if remote_updated and remote_updated > local_updated:
                # used is a one-way latch — never revert 1 → 0
                existing.used = max(existing.used, c.get("used", 0))
                if c.get("used") and not existing.used_at:
                    existing.used_at        = _parse_dt(c.get("used_at"))
                    existing.used_by_hostname = c.get("used_by_hostname")
                existing.updated_at = remote_updated
                codes_imported += 1

    # -- Provisioned devices ---------------------------------------------------
    for d in payload.get("devices", []):
        remote_updated = _parse_dt(d.get("updated_at"))
        existing = db.query(ProvisionedDevice).filter_by(id=d["id"]).first()

        if existing is None:
            db.add(ProvisionedDevice(
                id                     = d["id"],
                hostname               = d["hostname"],
                fqdn                   = d["fqdn"],
                tunnel_id              = d["tunnel_id"],
                tunnel_token_encrypted = d["tunnel_token_encrypted"],
                dns_record_id          = d.get("dns_record_id"),
                device_id              = d["device_id"],
                activation_code        = d["activation_code"],
                status                 = d.get("status", "active"),
                created_at             = _parse_dt(d.get("created_at")),
                updated_at             = remote_updated,
                origin_server_id       = d.get("origin_server_id"),
            ))
            devices_imported += 1
        else:
            local_updated = existing.updated_at or datetime.min.replace(tzinfo=timezone.utc)
            if remote_updated and remote_updated > local_updated:
                existing.hostname         = d["hostname"]
                existing.fqdn             = d["fqdn"]
                existing.tunnel_id        = d["tunnel_id"]
                existing.dns_record_id    = d.get("dns_record_id")
                existing.device_id        = d["device_id"]
                existing.status           = d.get("status", existing.status)
                existing.updated_at       = remote_updated
                existing.origin_server_id = d.get("origin_server_id")
                # Do NOT overwrite tunnel_token_encrypted — only the server
                # that provisioned the device can decrypt it; keep local copy.
                devices_imported += 1

    db.commit()
    return {"imported_devices": devices_imported, "imported_codes": codes_imported}


# ── Push to a single peer ─────────────────────────────────────────────────────

async def push_to_peer(peer: SyncPeer, payload: dict) -> bool:
    """POST payload to a peer's import endpoint. Returns True on success."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{peer.url.rstrip('/')}/api/sync/import",
                headers=_sync_headers(peer.peer_sync_key),
                json=payload,
            )
        if resp.status_code == 200:
            _update_peer_status(peer.id, "ok")
            return True
        log.warning("Sync push to %s returned HTTP %s", peer.url, resp.status_code)
        _update_peer_status(peer.id, "error", f"HTTP {resp.status_code}")
        return False
    except Exception as exc:
        log.warning("Sync push to %s failed: %s", peer.url, exc)
        _update_peer_status(peer.id, "error", str(exc))
        return False


def _update_peer_status(peer_id: str, status: str, error: Optional[str] = None):
    db = SessionLocal()
    try:
        peer = db.get(SyncPeer, peer_id)
        if peer:
            peer.last_sync_at     = datetime.now(timezone.utc)
            peer.last_sync_status = status
            peer.last_sync_error  = error
            db.commit()
    finally:
        db.close()


# ── Push a single change to all peers immediately (fire-and-forget) ───────────

def push_change_to_peers(payload: dict):
    """Called after a write; schedules background push to all active peers."""
    async def _run():
        db = SessionLocal()
        try:
            peers = db.query(SyncPeer).filter_by(active=1).all()
            peer_list = list(peers)
        finally:
            db.close()
        await asyncio.gather(*[push_to_peer(p, payload) for p in peer_list])

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(_run())
    except Exception:
        pass


# ── Pull from a single peer ───────────────────────────────────────────────────

async def pull_from_peer(peer: SyncPeer) -> dict:
    """GET records from a peer and merge them locally."""
    params = {}
    if peer.last_sync_at:
        params["since"] = peer.last_sync_at.isoformat()

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{peer.url.rstrip('/')}/api/sync/export",
                headers=_sync_headers(peer.peer_sync_key),
                params=params,
            )
        if resp.status_code != 200:
            _update_peer_status(peer.id, "error", f"HTTP {resp.status_code}")
            return {}
        payload = resp.json()
    except Exception as exc:
        log.warning("Sync pull from %s failed: %s", peer.url, exc)
        _update_peer_status(peer.id, "error", str(exc))
        return {}

    db = SessionLocal()
    try:
        result = import_records(payload, db)
    finally:
        db.close()

    _update_peer_status(peer.id, "ok")
    return result


# ── Full reconciliation pass ──────────────────────────────────────────────────

async def reconcile_all_peers():
    """Pull from every active peer. Called by the background loop."""
    db = SessionLocal()
    try:
        peers = db.query(SyncPeer).filter_by(active=1).all()
        peer_list = list(peers)
    finally:
        db.close()

    if not peer_list:
        return

    results = await asyncio.gather(*[pull_from_peer(p) for p in peer_list])
    total = sum((r.get("imported_devices", 0) + r.get("imported_codes", 0)) for r in results if r)
    if total:
        log.info("Reconciliation: merged %d record(s) from %d peer(s)", total, len(peer_list))


# ── Background loop ───────────────────────────────────────────────────────────

async def sync_loop():
    interval = settings.sync_interval_seconds
    log.info("Sync loop started (interval=%ds)", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await reconcile_all_peers()
        except Exception as exc:
            log.error("Sync loop error: %s", exc)


# ── Initial full sync with a new peer ─────────────────────────────────────────

async def full_sync_with_peer(peer: SyncPeer) -> dict:
    """Bidirectional full sync — used when first adding a peer."""
    # Pull all their records
    pull_result = await pull_from_peer(peer)

    # Push all our records to them
    db = SessionLocal()
    try:
        payload = export_records(db, since=None)
    finally:
        db.close()
    await push_to_peer(peer, payload)

    return pull_result


# ── Peer connectivity check ───────────────────────────────────────────────────

async def check_peer(url: str, peer_sync_key: Optional[str] = None) -> dict:
    """Test connectivity to a peer. Returns {ok, server_id, error}."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{url.rstrip('/')}/health",
                headers=_sync_headers(peer_sync_key),
            )
        if resp.status_code == 200:
            # Also try the sync export endpoint to verify the sync key works
            async with httpx.AsyncClient(timeout=10) as client:
                check = await client.get(
                    f"{url.rstrip('/')}/api/sync/export",
                    headers=_sync_headers(peer_sync_key),
                    params={"since": "2099-01-01T00:00:00Z"},  # empty response, just checking auth
                )
            if check.status_code == 403:
                return {"ok": False, "error": "Sync key rejected by peer. Check X-Sync-Key matches."}
            server_id = check.json().get("server_id", "unknown") if check.status_code == 200 else "unknown"
            return {"ok": True, "server_id": server_id}
        return {"ok": False, "error": f"Health check returned HTTP {resp.status_code}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
