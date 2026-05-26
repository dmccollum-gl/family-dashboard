# Dashboard Tunnel Provisioning Server

A FastAPI service that runs on the central Mac Mini and automates:

1. Creating a Cloudflare Argo tunnel for each new Pi dashboard device
2. Registering the device's FQDN (`{hostname}.mccollumtechnology.com`) in the Google OAuth2 client's authorized redirect URIs and JavaScript origins

Each Pi calls this server during first-run setup via the setup wizard.

---

## Prerequisites

- Python 3.11+
- A Cloudflare account managing `mccollumtechnology.com`
- A Google Cloud project with an OAuth 2.0 web application credential
- A Google Cloud service account with the **OAuth Config Editor** role

---

## Environment setup

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Fill in all values in .env (see comments in the file)
```

### Required `.env` values

| Variable | Description |
|---|---|
| `CLOUDFLARE_API_TOKEN` | API token with **Cloudflare Tunnel:Edit** and **DNS:Edit** perms |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID (from the account home URL) |
| `CLOUDFLARE_ZONE_ID` | Zone ID for `mccollumtechnology.com` |
| `BASE_DOMAIN` | `mccollumtechnology.com` |
| `GOOGLE_CLOUD_PROJECT_ID` | GCP project owning the OAuth client |
| `GOOGLE_OAUTH_CLIENT_ID` | Full client ID string ending in `.apps.googleusercontent.com` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Inline JSON string **or** path to the SA key file |
| `ADMIN_API_KEY` | Random secret for admin endpoints (use `openssl rand -hex 32`) |
| `DATABASE_URL` | `sqlite:///./server.db` (default) |
| `FERNET_KEY` | Encryption key for tunnel tokens at rest (see below) |

#### Generate a Fernet key

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the output as `FERNET_KEY` in `.env`. **Keep this secret and back it up** — losing it means you cannot decrypt existing tunnel tokens from the database.

---

## Running the server

```bash
cd server
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

The server binds to port 8080 by default. The Pi's `PROVISIONING_SERVER_URL` in `backend/.env` must point here (e.g., `http://192.168.1.10:8080`).

### Alongside the dashboard app for local development

The dashboard backend runs on port 8001, so the provisioning server on 8080 has no conflict.  Run both:

```bash
# Terminal 1 — dashboard backend
cd backend
source ../.venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8001 --reload

# Terminal 2 — provisioning server
cd server
source .venv/.../bin/activate
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

Set `PROVISIONING_SERVER_URL=https://provision.mccollumtechnology.com` in `backend/.env` (or `http://localhost:8080` for local dev).

---

## Generating activation codes

Activation codes are single-use and required for Pi setup. Generate a batch before shipping devices:

```bash
curl -X POST http://localhost:8080/api/provision/activation-codes \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"count": 20}'
```

Returns a JSON array of codes in `XXXX-XXXX-XXXX-XXXX` format. Print these and include them with each Pi shipment.

---

## Admin API

All admin endpoints require the `X-Admin-Key` header.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/provision/activation-codes` | Generate a batch of activation codes |
| `GET` | `/api/provision/activation-codes` | List all codes with used/unused status |
| `GET` | `/api/provision/tunnels` | List all provisioned devices |
| `GET` | `/health` | Health check |

---

## Deployment on Mac Mini

### Recommended: systemd-equivalent via launchd

Create `/Library/LaunchDaemons/com.mccollum.provisioning.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.mccollum.provisioning</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/davidmccollum/Documents/Claude/Dashboard/server/.venv/bin/uvicorn</string>
        <string>main:app</string>
        <string>--host</string>
        <string>0.0.0.0</string>
        <string>--port</string>
        <string>8080</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/davidmccollum/Documents/Claude/Dashboard/server</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/var/log/dashboard-provisioning.log</string>
    <key>StandardErrorPath</key>
    <string>/var/log/dashboard-provisioning.log</string>
</dict>
</plist>
```

Load it:

```bash
sudo launchctl load /Library/LaunchDaemons/com.mccollum.provisioning.plist
```

### Firewall

The provisioning server must be reachable from the Pi during setup. Since the Pi calls this server during the setup wizard (after the user enters WiFi credentials but before the reboot), it must be reachable over the internet at `https://provision.mccollumtechnology.com`. Set up a DNS record for that subdomain pointing to the Mac Mini's public IP, with TLS termination via a reverse proxy (nginx + Let's Encrypt, or a Cloudflare Tunnel on the Mac Mini itself).

---

## Security notes

- Activation codes are single-use and marked atomically with `WHERE used=0` to prevent race conditions
- Tunnel tokens are Fernet-encrypted at rest in SQLite; they're only decrypted once when returned at provision time
- Admin endpoints require `X-Admin-Key` — rotate this regularly
- The provisioning server should **not** be exposed to the public internet; it should only be reachable from the local network or a VPN
