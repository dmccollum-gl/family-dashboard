import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from database import ProvisionedDevice, get_db, get_server_id
from services.activation import validate_and_use
from services.encryption import encrypt_token
from services.sync_service import export_records, push_change_to_peers
from integrations.cloudflare import (
    CloudflareError,
    create_dns_record,
    create_tunnel,
    delete_dns_record,
    delete_tunnel,
    find_dns_record,
)
from integrations.google_cloud import GoogleCloudError, add_redirect_uri, remove_redirect_uri

router = APIRouter()

_HOSTNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _validate_hostname(hostname: str) -> str:
    """Lowercase and validate; return clean slug or raise 400."""
    slug = hostname.lower().strip()
    if not _HOSTNAME_RE.match(slug):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid hostname '{slug}'. Use only lowercase letters, digits, and hyphens.",
        )
    return slug


class ProvisionRequest(BaseModel):
    hostname:       str
    pi_device_id:   str
    activation_code: str


@router.post("/tunnel")
async def provision_tunnel(req: ProvisionRequest, db: Session = Depends(get_db)):
    hostname = _validate_hostname(req.hostname)

    # -- Validate activation code (atomic single-use) -------------------------
    # Check first to avoid burning Cloudflare/GCP quota on invalid codes.
    if not validate_and_use(req.activation_code, hostname, db):
        raise HTTPException(
            status_code=400,
            detail="Invalid or already-used activation code.",
        )

    # -- Reject duplicate hostnames -------------------------------------------
    existing = db.query(ProvisionedDevice).filter_by(hostname=hostname).first()
    if existing and existing.status != "deprovisioned":
        raise HTTPException(
            status_code=409,
            detail=f"Hostname '{hostname}' is already taken.",
        )

    fqdn           = f"{hostname}.{settings.base_domain}"
    tunnel_name    = f"{hostname}-dashboard"
    tunnel_data    = {}
    dns_record_id  = None

    # -- Cloudflare: create tunnel + DNS record --------------------------------
    try:
        tunnel_data = await create_tunnel(
            settings.cloudflare_account_id,
            tunnel_name,
            settings.cloudflare_api_token,
        )
        dns_record_id = await create_dns_record(
            settings.cloudflare_zone_id,
            hostname,
            tunnel_data["tunnel_id"],
            settings.base_domain,
            settings.cloudflare_api_token,
        )
    except CloudflareError as exc:
        raise HTTPException(status_code=502, detail=f"Cloudflare error: {exc}")

    # -- Google Cloud: register redirect URI ----------------------------------
    try:
        await add_redirect_uri(
            settings.google_cloud_project_id,
            settings.google_oauth_client_id,
            fqdn,
            settings.google_service_account_json,
        )
    except GoogleCloudError as exc:
        # Best-effort cleanup: delete the tunnel and DNS record we just created.
        try:
            await delete_tunnel(
                settings.cloudflare_account_id,
                tunnel_data["tunnel_id"],
                settings.cloudflare_api_token,
            )
            if dns_record_id:
                await delete_dns_record(
                    settings.cloudflare_zone_id,
                    dns_record_id,
                    settings.cloudflare_api_token,
                )
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=f"Google Cloud error: {exc}")

    # -- Persist to database --------------------------------------------------
    now             = datetime.now(timezone.utc)
    encrypted_token = encrypt_token(tunnel_data["tunnel_token"])
    record = ProvisionedDevice(
        id                     = str(uuid.uuid4()),
        hostname               = hostname,
        fqdn                   = fqdn,
        tunnel_id              = tunnel_data["tunnel_id"],
        tunnel_token_encrypted = encrypted_token,
        dns_record_id          = dns_record_id,
        device_id              = req.pi_device_id,
        activation_code        = req.activation_code,
        status                 = "active",
        created_at             = now,
        updated_at             = now,
        origin_server_id       = get_server_id(),
    )

    # Upsert: replace deprovisioned record for the same hostname if it exists.
    if existing:
        db.delete(existing)
        db.flush()

    db.add(record)
    db.commit()

    # Push this new record to all peers immediately (fire-and-forget).
    push_change_to_peers(export_records(db, since=None))

    return {
        "fqdn":         fqdn,
        "tunnel_token": tunnel_data["tunnel_token"],
        "tunnel_id":    tunnel_data["tunnel_id"],
    }


@router.get("/status/{hostname}")
def get_status(hostname: str, db: Session = Depends(get_db)):
    hostname = _validate_hostname(hostname)
    record = db.query(ProvisionedDevice).filter_by(hostname=hostname).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"No record for hostname '{hostname}'.")
    return {
        "hostname":   record.hostname,
        "fqdn":       record.fqdn,
        "status":     record.status,
        "tunnel_id":  record.tunnel_id,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "last_seen":  record.last_seen.isoformat() if record.last_seen else None,
    }


@router.delete("/tunnel/{hostname}")
async def deprovision_tunnel(hostname: str, db: Session = Depends(get_db)):
    hostname = _validate_hostname(hostname)
    record = db.query(ProvisionedDevice).filter_by(hostname=hostname).first()
    if not record or record.status == "deprovisioned":
        raise HTTPException(status_code=404, detail=f"No active record for hostname '{hostname}'.")

    errors = []

    # -- Cloudflare: delete tunnel and DNS record -----------------------------
    try:
        await delete_tunnel(
            settings.cloudflare_account_id,
            record.tunnel_id,
            settings.cloudflare_api_token,
        )
    except CloudflareError as exc:
        errors.append(f"Cloudflare tunnel deletion failed: {exc}")

    dns_id = record.dns_record_id
    if not dns_id:
        # Fall back to lookup if the record_id wasn't stored.
        try:
            dns_id = await find_dns_record(
                settings.cloudflare_zone_id,
                record.fqdn,
                settings.cloudflare_api_token,
            )
        except CloudflareError:
            pass

    if dns_id:
        try:
            await delete_dns_record(
                settings.cloudflare_zone_id,
                dns_id,
                settings.cloudflare_api_token,
            )
        except CloudflareError as exc:
            errors.append(f"Cloudflare DNS deletion failed: {exc}")

    # -- Google Cloud: remove redirect URI ------------------------------------
    try:
        await remove_redirect_uri(
            settings.google_cloud_project_id,
            settings.google_oauth_client_id,
            record.fqdn,
            settings.google_service_account_json,
        )
    except GoogleCloudError as exc:
        errors.append(f"Google Cloud URI removal failed: {exc}")

    # -- Mark record inactive (even if some cleanup steps failed) -------------
    record.status     = "deprovisioned"
    record.updated_at = datetime.now(timezone.utc)
    db.commit()

    push_change_to_peers(export_records(db, since=None))

    response = {"hostname": hostname, "status": "deprovisioned"}
    if errors:
        response["warnings"] = errors
    return response
