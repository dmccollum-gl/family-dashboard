"""Google Cloud OAuth2 client management.

Uses a service account with ``https://www.googleapis.com/auth/cloud-platform``
scope to fetch and patch the OAuth 2.0 web client's redirect URIs and
authorized JavaScript origins.

Required service account permissions:
  - roles/oauthconfig.editor  (OAuth Config Editor)
  - Or equivalently: the "Client OAuth2 Brand management" permission set in IAM

The GOOGLE_SERVICE_ACCOUNT_JSON setting may be either:
  - A raw JSON string (the content of the service account key file), or
  - A filesystem path to the JSON key file.
"""

import json
from typing import Optional

import httpx
import google.oauth2.service_account
import google.auth.transport.requests

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

# Endpoint for managing OAuth2 web-application clients.
# Requires an access token from a service account that has the OAuth Config
# Editor role on the project.
_OAUTH_CLIENTS_BASE = "https://oauth2.googleapis.com/v2/oauthclients"


class GoogleCloudError(Exception):
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


def _load_sa_info(service_account_json: str) -> dict:
    """Parse service account JSON from a string or file path."""
    try:
        return json.loads(service_account_json)
    except json.JSONDecodeError:
        with open(service_account_json) as fh:
            return json.load(fh)


def _get_access_token(service_account_json: str) -> str:
    info = _load_sa_info(service_account_json)
    creds = google.oauth2.service_account.Credentials.from_service_account_info(
        info, scopes=SCOPES
    )
    request = google.auth.transport.requests.Request()
    creds.refresh(request)
    return creds.token


async def get_oauth_client(
    project_id: str, client_id: str, service_account_json: str
) -> dict:
    """Return the current OAuth2 client config (includes redirectUris / jsOrigins)."""
    token = _get_access_token(service_account_json)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{_OAUTH_CLIENTS_BASE}/{client_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"project": project_id},
        )
    if resp.status_code != 200:
        raise GoogleCloudError(
            f"get_oauth_client failed (HTTP {resp.status_code}): {resp.text}",
            resp.status_code,
        )
    return resp.json()


async def add_redirect_uri(
    project_id: str,
    client_id: str,
    fqdn: str,
    service_account_json: str,
) -> None:
    """Idempotently append ``https://{fqdn}`` to redirectUris and jsOrigins."""
    origin = f"https://{fqdn}"
    current = await get_oauth_client(project_id, client_id, service_account_json)

    redirect_uris = list(current.get("redirectUris", []))
    js_origins    = list(current.get("jsOrigins", []))

    changed = False
    if origin not in redirect_uris:
        redirect_uris.append(origin)
        changed = True
    if origin not in js_origins:
        js_origins.append(origin)
        changed = True

    if not changed:
        return  # already present – nothing to do

    token = _get_access_token(service_account_json)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.patch(
            f"{_OAUTH_CLIENTS_BASE}/{client_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
            },
            params={"project": project_id},
            json={"redirectUris": redirect_uris, "jsOrigins": js_origins},
        )
    if resp.status_code not in (200, 204):
        raise GoogleCloudError(
            f"add_redirect_uri failed (HTTP {resp.status_code}): {resp.text}",
            resp.status_code,
        )


async def remove_redirect_uri(
    project_id: str,
    client_id: str,
    fqdn: str,
    service_account_json: str,
) -> None:
    """Idempotently remove ``https://{fqdn}`` from redirectUris and jsOrigins."""
    origin = f"https://{fqdn}"
    current = await get_oauth_client(project_id, client_id, service_account_json)

    redirect_uris = [u for u in current.get("redirectUris", []) if u != origin]
    js_origins    = [o for o in current.get("jsOrigins",    []) if o != origin]

    token = _get_access_token(service_account_json)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.patch(
            f"{_OAUTH_CLIENTS_BASE}/{client_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
            },
            params={"project": project_id},
            json={"redirectUris": redirect_uris, "jsOrigins": js_origins},
        )
    if resp.status_code not in (200, 204):
        raise GoogleCloudError(
            f"remove_redirect_uri failed (HTTP {resp.status_code}): {resp.text}",
            resp.status_code,
        )
