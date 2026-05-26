#!/usr/bin/env python3
"""
Provisioning Server — HTTP First-Boot Setup Wizard

Runs on port 80 before .env exists.  Point any browser on the same network
to http://<mac-mini-ip>/ to configure the server without a keyboard or monitor.

On completion:
  • Writes /opt/provisioning/.env
  • Writes /opt/provisioning/.tunnel-token
  • Creates the Mac Mini Cloudflare tunnel (provision.<base_domain>)
  • Starts provisioning-server.service and cloudflared-provisioning.service
  • Marks /opt/provisioning/.configured and disables this service
"""

import asyncio
import base64
import json
import os
import subprocess
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import google.oauth2.service_account
import google.auth.transport.requests

APP_DIR = Path(os.environ.get("APP_DIR", "/opt/provisioning"))
ENV_FILE      = APP_DIR / ".env"
TOKEN_FILE    = APP_DIR / ".tunnel-token"
CONFIGURED    = APP_DIR / ".configured"
CF_BASE       = "https://api.cloudflare.com/client/v4"

app = FastAPI(title="Provisioning Setup")


# ── Request model ─────────────────────────────────────────────────────────────

class PeerEntry(BaseModel):
    url:      str
    name:     str = ""
    sync_key: str = ""


class ConfigureRequest(BaseModel):
    cloudflare_api_token:         str
    cloudflare_account_id:        str
    cloudflare_zone_id:           str
    base_domain:                  str = "mccollumtechnology.com"
    google_cloud_project_id:      str
    google_oauth_client_id:       str
    google_service_account_json:  str   # full JSON content as a string
    admin_api_key:                str
    new_password:                 str   # new password for 'provision' OS user
    peers:                        list[PeerEntry] = []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cf_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def _validate_cloudflare(api_token: str, account_id: str) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{CF_BASE}/accounts/{account_id}",
            headers=_cf_headers(api_token),
        )
    data = resp.json()
    if not data.get("success"):
        msg = (data.get("errors") or [{}])[0].get("message", "Invalid token or account ID")
        raise ValueError(f"Cloudflare: {msg}")


async def _validate_google(project_id: str, sa_json: str) -> None:
    try:
        info = json.loads(sa_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Service account JSON is not valid JSON: {exc}")
    try:
        creds = google.oauth2.service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        req = google.auth.transport.requests.Request()
        creds.refresh(req)
    except Exception as exc:
        raise ValueError(f"Service account credentials invalid: {exc}")
    # Verify project access
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}",
            headers={"Authorization": f"Bearer {creds.token}"},
        )
    if resp.status_code == 403:
        raise ValueError("Service account does not have access to that Google Cloud project.")
    if resp.status_code not in (200, 404):
        raise ValueError(f"Google Cloud project check returned HTTP {resp.status_code}.")


async def _create_mac_tunnel(
    api_token: str, account_id: str, zone_id: str, base_domain: str
) -> tuple[str, str]:
    """Create the Mac Mini Cloudflare tunnel. Returns (tunnel_id, tunnel_token)."""
    secret = base64.b64encode(os.urandom(32)).decode()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{CF_BASE}/accounts/{account_id}/cfd_tunnel",
            headers=_cf_headers(api_token),
            json={"name": "provisioning-server", "tunnel_secret": secret},
        )
    data = resp.json()
    if not data.get("success"):
        msg = (data.get("errors") or [{}])[0].get("message", "Tunnel creation failed")
        raise ValueError(f"Cloudflare tunnel: {msg}")
    tunnel_id    = data["result"]["id"]
    tunnel_token = data["result"]["token"]

    # Configure ingress: provision.<domain> → http://127.0.0.1:8080
    async with httpx.AsyncClient(timeout=30) as client:
        await client.put(
            f"{CF_BASE}/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations",
            headers=_cf_headers(api_token),
            json={
                "config": {
                    "ingress": [
                        {"hostname": f"provision.{base_domain}", "service": "http://127.0.0.1:8080"},
                        {"service": "http_status:404"},
                    ]
                }
            },
        )

    # Create CNAME DNS record
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(
            f"{CF_BASE}/zones/{zone_id}/dns_records",
            headers=_cf_headers(api_token),
            json={
                "type":    "CNAME",
                "name":    f"provision.{base_domain}",
                "content": f"{tunnel_id}.cfargotunnel.com",
                "proxied": True,
                "ttl":     1,
            },
        )

    return tunnel_id, tunnel_token


async def _start_services() -> None:
    subprocess.run(["systemctl", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "start", "provisioning-server.service"], check=True)
    for _ in range(30):
        await asyncio.sleep(2)
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get("http://127.0.0.1:8080/health")
                if r.status_code == 200:
                    break
        except Exception:
            pass
    subprocess.run(["systemctl", "start", "cloudflared-provisioning.service"], check=False)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    if CONFIGURED.exists():
        return HTMLResponse(ALREADY_CONFIGURED_HTML)
    return HTMLResponse(SETUP_HTML)


@app.get("/api/setup/status")
async def status():
    return {"configured": CONFIGURED.exists()}


@app.post("/api/setup/configure")
async def configure(req: ConfigureRequest):
    if CONFIGURED.exists():
        return {"success": False, "error": "Server is already configured."}

    progress = []
    try:
        # 1. Validate Cloudflare
        progress.append("Validating Cloudflare credentials…")
        await _validate_cloudflare(req.cloudflare_api_token, req.cloudflare_account_id)

        # 2. Validate Google
        progress.append("Validating Google Cloud credentials…")
        await _validate_google(req.google_cloud_project_id, req.google_service_account_json)

        # 3. Create tunnel
        progress.append(f"Creating Cloudflare tunnel for provision.{req.base_domain}…")
        _, tunnel_token = await _create_mac_tunnel(
            req.cloudflare_api_token,
            req.cloudflare_account_id,
            req.cloudflare_zone_id,
            req.base_domain,
        )

        # 4. Write files
        progress.append("Writing configuration…")
        fernet_key = Fernet.generate_key().decode()
        ENV_FILE.write_text(
            f"CLOUDFLARE_API_TOKEN={req.cloudflare_api_token}\n"
            f"CLOUDFLARE_ACCOUNT_ID={req.cloudflare_account_id}\n"
            f"CLOUDFLARE_ZONE_ID={req.cloudflare_zone_id}\n"
            f"BASE_DOMAIN={req.base_domain}\n"
            f"GOOGLE_CLOUD_PROJECT_ID={req.google_cloud_project_id}\n"
            f"GOOGLE_OAUTH_CLIENT_ID={req.google_oauth_client_id}\n"
            f"GOOGLE_SERVICE_ACCOUNT_JSON={req.google_service_account_json}\n"
            f"ADMIN_API_KEY={req.admin_api_key}\n"
            f"DATABASE_URL=sqlite:///./server.db\n"
            f"FERNET_KEY={fernet_key}\n"
        )
        ENV_FILE.chmod(0o600)
        TOKEN_FILE.write_text(tunnel_token)
        TOKEN_FILE.chmod(0o600)
        # Fix ownership so the 'provision' user can read them
        subprocess.run(["chown", "provision:provision", str(ENV_FILE), str(TOKEN_FILE)], check=False)

        # 5. Set OS password
        if req.new_password:
            progress.append("Setting server login password…")
            subprocess.run(
                ["chpasswd"],
                input=f"provision:{req.new_password}",
                text=True, check=True,
            )

        # 6. Start services
        progress.append("Starting provisioning server and Cloudflare tunnel…")
        await _start_services()

        # 7. Register peer servers
        peer_results = []
        if req.peers:
            progress.append("Registering peer servers…")
            async with httpx.AsyncClient(timeout=30) as client:
                for peer in req.peers:
                    if not peer.url:
                        continue
                    body: dict = {"url": peer.url}
                    if peer.name:
                        body["name"] = peer.name
                    if peer.sync_key:
                        body["peer_sync_key"] = peer.sync_key
                    try:
                        r = await client.post(
                            "http://127.0.0.1:8080/api/sync/peers",
                            headers={
                                "X-Admin-Key": req.admin_api_key,
                                "Content-Type": "application/json",
                            },
                            json=body,
                        )
                        if r.status_code == 200:
                            progress.append(f"  Synced with {peer.url}")
                            peer_results.append({"url": peer.url, "ok": True})
                        else:
                            detail = r.json().get("detail", f"HTTP {r.status_code}")
                            progress.append(f"  Warning: could not sync with {peer.url}: {detail}")
                            peer_results.append({"url": peer.url, "ok": False, "error": detail})
                    except Exception as exc:
                        progress.append(f"  Warning: could not reach {peer.url}: {exc}")
                        peer_results.append({"url": peer.url, "ok": False, "error": str(exc)})

        # 8. Mark configured
        CONFIGURED.touch()
        subprocess.run(["systemctl", "disable", "--now", "provisioning-setup-http.service"], check=False)

        progress.append("Done!")
        return {
            "success":       True,
            "fqdn":          f"provision.{req.base_domain}",
            "admin_api_key": req.admin_api_key,
            "peers":         peer_results,
            "progress":      progress,
        }

    except Exception as exc:
        return {"success": False, "error": str(exc), "progress": progress}


# ── Embedded HTML UI ──────────────────────────────────────────────────────────

SETUP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Provisioning Server Setup</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#f5f5f7;min-height:100vh;display:flex;align-items:center;
       justify-content:center;padding:16px}
  .card{background:#fff;border-radius:12px;box-shadow:0 2px 16px rgba(0,0,0,.1);
        max-width:640px;width:100%;padding:36px}
  h1{font-size:1.4rem;font-weight:700;color:#1d1d1f;margin-bottom:4px}
  .subtitle{color:#6e6e73;font-size:.9rem;margin-bottom:28px}
  .section{margin-bottom:24px;padding-bottom:24px;border-bottom:1px solid #e5e5ea}
  .section:last-of-type{border-bottom:none}
  h2{font-size:.85rem;font-weight:600;text-transform:uppercase;
     letter-spacing:.05em;color:#6e6e73;margin-bottom:14px}
  .field{margin-bottom:14px}
  label{display:block;font-size:.85rem;font-weight:500;color:#1d1d1f;margin-bottom:5px}
  input,textarea{width:100%;padding:9px 12px;border:1.5px solid #d2d2d7;
                 border-radius:8px;font-size:.9rem;font-family:inherit;
                 transition:border-color .15s}
  input:focus,textarea:focus{outline:none;border-color:#0071e3}
  textarea{font-family:'SF Mono',monospace;font-size:.75rem;height:140px;resize:vertical}
  .hint{font-size:.75rem;color:#6e6e73;margin-top:4px}
  .row{display:flex;gap:12px}
  .row .field{flex:1}
  .btn-row{display:flex;gap:10px;align-items:center;margin-top:8px}
  .gen-btn{padding:6px 12px;background:#f5f5f7;border:1.5px solid #d2d2d7;
           border-radius:6px;font-size:.8rem;cursor:pointer;white-space:nowrap}
  .gen-btn:hover{background:#e5e5ea}
  .submit-btn{width:100%;padding:13px;background:#0071e3;color:#fff;border:none;
              border-radius:10px;font-size:1rem;font-weight:600;cursor:pointer;
              margin-top:8px;transition:background .15s}
  .submit-btn:hover:not(:disabled){background:#0077ed}
  .submit-btn:disabled{background:#a1c4f0;cursor:not-allowed}
  .progress{display:none;margin-top:20px;padding:16px;background:#f5f5f7;
            border-radius:8px}
  .progress-title{font-weight:600;font-size:.9rem;margin-bottom:10px;color:#1d1d1f}
  .step{display:flex;align-items:center;gap:8px;padding:4px 0;font-size:.85rem;color:#3a3a3c}
  .step .icon{font-size:1rem}
  .error-box{display:none;margin-top:16px;padding:14px 16px;background:#fff0f0;
             border:1.5px solid #ffb3b3;border-radius:8px;color:#c0392b;font-size:.85rem}
  .success-box{display:none;margin-top:16px;padding:20px;background:#f0fff4;
               border:1.5px solid #b3ffcc;border-radius:8px}
  .success-box h3{color:#1a7f3c;font-size:1rem;margin-bottom:10px}
  .fqdn{font-family:'SF Mono',monospace;background:#e8f8ed;padding:8px 12px;
        border-radius:6px;font-size:.9rem;color:#1a7f3c;display:inline-block;margin:6px 0}
  .key-box{font-family:'SF Mono',monospace;background:#f5f5f7;padding:6px 10px;
           border-radius:6px;font-size:.8rem;word-break:break-all;margin:6px 0}
  .curl-box{background:#1d1d1f;color:#a8ff78;font-family:'SF Mono',monospace;
            font-size:.72rem;padding:12px;border-radius:8px;overflow-x:auto;
            white-space:pre;margin-top:10px}
  .toggle-row{display:flex;align-items:center;gap:10px;margin-bottom:14px;cursor:pointer}
  .toggle-row input[type=checkbox]{width:18px;height:18px;cursor:pointer;flex-shrink:0}
  .toggle-row label{font-size:.9rem;font-weight:500;color:#1d1d1f;cursor:pointer;margin:0}
  .peer-entry{background:#f9f9fb;border:1.5px solid #e5e5ea;border-radius:8px;
              padding:14px;margin-bottom:10px;position:relative}
  .peer-entry .remove-btn{position:absolute;top:10px;right:10px;background:none;
    border:none;font-size:1.1rem;cursor:pointer;color:#6e6e73;line-height:1}
  .peer-entry .remove-btn:hover{color:#c0392b}
  .add-peer-btn{padding:7px 14px;background:#f0f7ff;border:1.5px solid #b3d4ff;
    border-radius:6px;font-size:.82rem;font-weight:500;color:#0071e3;cursor:pointer}
  .add-peer-btn:hover{background:#e0efff}
  .peer-list-hidden{display:none}
</style>
</head>
<body>
<div class="card">
  <h1>Dashboard Provisioning Server</h1>
  <p class="subtitle">First-boot setup — enter your API credentials to configure the server.</p>

  <form id="form">

    <div class="section">
      <h2>Cloudflare</h2>
      <div class="field">
        <label>API Token <span style="color:#6e6e73;font-weight:400">(Tunnel:Edit + DNS:Edit permissions)</span></label>
        <input type="password" id="cf_token" placeholder="Paste your Cloudflare API token" required>
      </div>
      <div class="row">
        <div class="field">
          <label>Account ID</label>
          <input type="text" id="cf_account" placeholder="32-character hex ID" required>
          <p class="hint">dash.cloudflare.com → account home URL</p>
        </div>
        <div class="field">
          <label>Zone ID</label>
          <input type="text" id="cf_zone" placeholder="Zone ID for mccollumtechnology.com" required>
          <p class="hint">Zone overview page, right sidebar</p>
        </div>
      </div>
      <div class="field">
        <label>Base Domain</label>
        <input type="text" id="base_domain" value="mccollumtechnology.com" required>
      </div>
    </div>

    <div class="section">
      <h2>Google Cloud</h2>
      <div class="row">
        <div class="field">
          <label>Project ID</label>
          <input type="text" id="gcp_project" placeholder="my-gcp-project" required>
        </div>
        <div class="field">
          <label>OAuth Client ID</label>
          <input type="text" id="oauth_client" placeholder="…apps.googleusercontent.com" required>
        </div>
      </div>
      <div class="field">
        <label>Service Account Key <span style="color:#6e6e73;font-weight:400">(paste full JSON)</span></label>
        <textarea id="sa_json" placeholder='{ "type": "service_account", "project_id": "...", ... }' required></textarea>
        <p class="hint">IAM &amp; Admin → Service Accounts → Keys → Add Key → JSON. SA needs OAuth Config Editor role.</p>
      </div>
    </div>

    <div class="section">
      <h2>Server Config</h2>
      <div class="field">
        <label>Admin API Key</label>
        <div class="btn-row">
          <input type="text" id="admin_key" placeholder="Random secret for admin endpoints" required style="flex:1">
          <button type="button" class="gen-btn" onclick="generateKey()">Generate</button>
        </div>
        <p class="hint">Used as the X-Admin-Key header to generate activation codes.</p>
      </div>
      <div class="field">
        <label>Server Login Password</label>
        <input type="password" id="new_password" placeholder="New password for the 'provision' user" required>
        <p class="hint">Replaces the default 'dashboard' password for SSH access.</p>
      </div>
    </div>

    <div class="section">
      <h2>Peer Servers <span style="font-weight:400;text-transform:none;font-size:.8rem">(optional)</span></h2>
      <div class="toggle-row" onclick="togglePeers()">
        <input type="checkbox" id="has-peers">
        <label for="has-peers">Sync with other provisioning servers</label>
      </div>
      <p class="hint" style="margin-bottom:12px">
        If you have multiple Mac Minis running this server, add them here and their databases
        will be kept in sync automatically.
      </p>
      <div id="peer-container" class="peer-list-hidden">
        <div id="peer-list"></div>
        <button type="button" class="add-peer-btn" onclick="addPeer()">+ Add Server</button>
      </div>
    </div>

    <div id="error-box" class="error-box"></div>

    <button type="submit" class="submit-btn" id="submit-btn">
      Configure Server →
    </button>

  </form>

  <div id="progress" class="progress">
    <div class="progress-title">Setting up your server…</div>
    <div id="steps"></div>
  </div>

  <div id="success-box" class="success-box">
    <h3>✓ Server is ready!</h3>
    <p>Your provisioning server is live at:</p>
    <div id="fqdn-display" class="fqdn"></div>
    <p style="margin-top:12px;font-size:.85rem;color:#3a3a3c">Admin API Key:</p>
    <div id="key-display" class="key-box"></div>
    <p style="margin-top:12px;font-size:.85rem;color:#3a3a3c">Generate activation codes for Pi devices:</p>
    <div id="curl-display" class="curl-box"></div>
    <p style="margin-top:14px;font-size:.8rem;color:#6e6e73">
      Set <code>PROVISIONING_SERVER_URL=https://<span id="fqdn-inline"></span></code>
      in <code>backend/.env</code> on your Pi images.
    </p>
  </div>

</div>
<script>
function generateKey() {
  const arr = new Uint8Array(32);
  crypto.getRandomValues(arr);
  document.getElementById('admin_key').value = Array.from(arr)
    .map(b => b.toString(16).padStart(2,'0')).join('');
}
generateKey();

// ── Peer management ──────────────────────────────────────────────────────────
let peerCount = 0;

function togglePeers() {
  const cb = document.getElementById('has-peers');
  const container = document.getElementById('peer-container');
  // cb.checked is the OLD value at this point (click hasn't toggled it yet when
  // called from the parent div's onclick), so we invert.
  const willShow = !cb.checked;
  container.style.display = willShow ? 'block' : 'none';
  if (willShow && document.getElementById('peer-list').children.length === 0) {
    addPeer();
  }
}

function addPeer() {
  const id = ++peerCount;
  const el = document.createElement('div');
  el.className = 'peer-entry';
  el.id = `peer-${id}`;
  el.innerHTML = `
    <button type="button" class="remove-btn" onclick="removePeer(${id})" title="Remove">×</button>
    <div class="field" style="margin-bottom:10px">
      <label>Server URL</label>
      <input type="url" id="peer-url-${id}" placeholder="https://provision2.example.com" style="width:100%">
      <p class="hint">Full URL of the other provisioning server (no trailing slash).</p>
    </div>
    <div class="row">
      <div class="field" style="margin-bottom:0">
        <label>Friendly Name <span style="color:#6e6e73;font-weight:400">(optional)</span></label>
        <input type="text" id="peer-name-${id}" placeholder="Mac Mini 2">
      </div>
      <div class="field" style="margin-bottom:0">
        <label>Sync Key <span style="color:#6e6e73;font-weight:400">(if different)</span></label>
        <input type="password" id="peer-key-${id}" placeholder="Leave blank to use same key">
        <p class="hint">Only needed if the peer has a different SYNC_KEY or admin key.</p>
      </div>
    </div>`;
  document.getElementById('peer-list').appendChild(el);
}

function removePeer(id) {
  const el = document.getElementById(`peer-${id}`);
  if (el) el.remove();
}

function getPeers() {
  const peers = [];
  document.querySelectorAll('.peer-entry').forEach(el => {
    const id = el.id.replace('peer-', '');
    const url = document.getElementById(`peer-url-${id}`).value.trim();
    if (!url) return;
    peers.push({
      url,
      name:     document.getElementById(`peer-name-${id}`).value.trim(),
      sync_key: document.getElementById(`peer-key-${id}`).value.trim(),
    });
  });
  return peers;
}

// ── Progress steps ────────────────────────────────────────────────────────────
function addStep(text, icon='⏳') {
  const el = document.createElement('div');
  el.className = 'step';
  el.innerHTML = `<span class="icon">${icon}</span><span>${text}</span>`;
  document.getElementById('steps').appendChild(el);
  return el;
}

// ── Form submit ───────────────────────────────────────────────────────────────
document.getElementById('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('submit-btn');
  btn.disabled = true;
  btn.textContent = 'Configuring…';
  document.getElementById('error-box').style.display = 'none';
  document.getElementById('progress').style.display = 'block';
  document.getElementById('steps').innerHTML = '';
  addStep('Connecting to Cloudflare and Google Cloud…');

  const payload = {
    cloudflare_api_token:        document.getElementById('cf_token').value.trim(),
    cloudflare_account_id:       document.getElementById('cf_account').value.trim(),
    cloudflare_zone_id:          document.getElementById('cf_zone').value.trim(),
    base_domain:                 document.getElementById('base_domain').value.trim(),
    google_cloud_project_id:     document.getElementById('gcp_project').value.trim(),
    google_oauth_client_id:      document.getElementById('oauth_client').value.trim(),
    google_service_account_json: document.getElementById('sa_json').value.trim(),
    admin_api_key:               document.getElementById('admin_key').value.trim(),
    new_password:                document.getElementById('new_password').value,
    peers:                       document.getElementById('has-peers').checked ? getPeers() : [],
  };

  try {
    const res = await fetch('/api/setup/configure', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    document.getElementById('steps').innerHTML = '';
    if (data.progress) {
      data.progress.forEach(s => addStep(s, '✓'));
    }

    if (data.success) {
      document.getElementById('form').style.display = 'none';
      document.getElementById('progress').style.display = 'none';
      const fqdn = data.fqdn;
      document.getElementById('fqdn-display').textContent = fqdn;
      document.getElementById('fqdn-inline').textContent = fqdn;
      document.getElementById('key-display').textContent = data.admin_api_key;
      document.getElementById('curl-display').textContent =
        `curl -X POST https://${fqdn}/api/provision/activation-codes \\\\\\n` +
        `  -H "X-Admin-Key: ${data.admin_api_key}" \\\\\\n` +
        `  -H "Content-Type: application/json" \\\\\\n` +
        `  -d '{"count": 10}'`;
      document.getElementById('success-box').style.display = 'block';
    } else {
      const box = document.getElementById('error-box');
      box.textContent = data.error || 'Unknown error';
      box.style.display = 'block';
      btn.disabled = false;
      btn.textContent = 'Configure Server →';
    }
  } catch (err) {
    const box = document.getElementById('error-box');
    box.textContent = 'Network error: ' + err.message;
    box.style.display = 'block';
    btn.disabled = false;
    btn.textContent = 'Configure Server →';
  }
});
</script>
</body>
</html>
"""

ALREADY_CONFIGURED_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Provisioning Server</title>
<style>body{font-family:-apple-system,sans-serif;display:flex;align-items:center;
  justify-content:center;min-height:100vh;background:#f5f5f7}
.card{background:#fff;border-radius:12px;box-shadow:0 2px 16px rgba(0,0,0,.1);
  padding:36px;max-width:480px;text-align:center}
h1{font-size:1.3rem;margin-bottom:8px}p{color:#6e6e73;font-size:.9rem}</style>
</head>
<body><div class="card">
<div style="font-size:2.5rem">✅</div>
<h1>Provisioning Server is Running</h1>
<p>Setup is complete. The provisioning API is available on port 8080.</p>
<p style="margin-top:12px">
  <a href="http://localhost:8080/health">Check health endpoint</a>
</p>
</div></body></html>
"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=80)
