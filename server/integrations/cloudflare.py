"""Cloudflare API integration.

Uses httpx for all HTTP calls.  Every public function raises CloudflareError on
failure so callers can surface a meaningful message without swallowing details.
"""
import base64
import os
from typing import Optional

import httpx

CF_BASE = "https://api.cloudflare.com/client/v4"


class CloudflareError(Exception):
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


def _headers(api_token: str) -> dict:
    return {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}


def _check(response: httpx.Response, context: str) -> dict:
    try:
        data = response.json()
    except Exception:
        raise CloudflareError(
            f"{context}: non-JSON response (HTTP {response.status_code})",
            response.status_code,
        )
    if not data.get("success"):
        errors = data.get("errors", [])
        msg = errors[0].get("message", "Unknown error") if errors else "Unknown error"
        raise CloudflareError(f"{context}: {msg}", response.status_code)
    return data


async def create_tunnel(account_id: str, name: str, api_token: str) -> dict:
    """Create a named Cloudflare tunnel.

    Returns dict with keys ``tunnel_id`` and ``tunnel_token``.
    The token is a JWT that the Pi passes to ``cloudflared tunnel run --token``.
    """
    secret = base64.b64encode(os.urandom(32)).decode()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{CF_BASE}/accounts/{account_id}/cfd_tunnel",
            headers=_headers(api_token),
            json={"name": name, "tunnel_secret": secret},
        )
    data = _check(resp, "create_tunnel")
    result = data["result"]
    token = result.get("token") or result.get("tunnel_token", "")
    if not token:
        raise CloudflareError("create_tunnel: Cloudflare returned no tunnel token", 500)
    return {"tunnel_id": result["id"], "tunnel_token": token}


async def create_dns_record(
    zone_id: str,
    hostname: str,
    tunnel_id: str,
    base_domain: str,
    api_token: str,
) -> str:
    """Create a proxied CNAME record pointing to the tunnel.

    Returns the DNS record ID (needed later for deletion).
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{CF_BASE}/zones/{zone_id}/dns_records",
            headers=_headers(api_token),
            json={
                "type":    "CNAME",
                "name":    f"{hostname}.{base_domain}",
                "content": f"{tunnel_id}.cfargotunnel.com",
                "proxied": True,
                "ttl":     1,
            },
        )
    data = _check(resp, "create_dns_record")
    return data["result"]["id"]


async def delete_tunnel(account_id: str, tunnel_id: str, api_token: str) -> None:
    """Delete a Cloudflare tunnel (cascade=true cleans up active connections)."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.delete(
            f"{CF_BASE}/accounts/{account_id}/cfd_tunnel/{tunnel_id}",
            headers=_headers(api_token),
            params={"cascade": "true"},
        )
    _check(resp, "delete_tunnel")


async def delete_dns_record(zone_id: str, record_id: str, api_token: str) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.delete(
            f"{CF_BASE}/zones/{zone_id}/dns_records/{record_id}",
            headers=_headers(api_token),
        )
    _check(resp, "delete_dns_record")


async def get_tunnel(account_id: str, tunnel_id: str, api_token: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{CF_BASE}/accounts/{account_id}/cfd_tunnel/{tunnel_id}",
            headers=_headers(api_token),
        )
    data = _check(resp, "get_tunnel")
    return data["result"]


async def list_tunnels(account_id: str, api_token: str) -> list:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{CF_BASE}/accounts/{account_id}/cfd_tunnel",
            headers=_headers(api_token),
        )
    data = _check(resp, "list_tunnels")
    return data.get("result", [])


async def find_dns_record(
    zone_id: str, name: str, api_token: str
) -> Optional[str]:
    """Return the DNS record ID for ``name``, or None if not found."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{CF_BASE}/zones/{zone_id}/dns_records",
            headers=_headers(api_token),
            params={"name": name, "type": "CNAME"},
        )
    data = _check(resp, "find_dns_record")
    records = data.get("result", [])
    return records[0]["id"] if records else None
