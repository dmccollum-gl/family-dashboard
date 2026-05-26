# Family Dashboard — Claude Code Context

## What this is
A Raspberry Pi kiosk for a family: full-screen Pygame display showing a shared Google Calendar, weather, and RSS ticker. A React web app lets family members sign in with Google and manage their settings. An Admin page (URL-only) handles system config.

## Hardware
- Raspberry Pi Zero 2W — ARM Cortex-A53, 512 MB RAM, Pi OS Bookworm arm64
- HDMI display, no keyboard/mouse attached — display is view-only

## Pi devices

### TEST — Pi Zero 2W @ `10.115.115.243` ← deploy here by default
- User/pass: `dashboard` / `dashboard`
- SSH: `sshpass -p dashboard ssh -o StrictHostKeyChecking=no dashboard@10.115.115.243`
- SCP: `sshpass -p dashboard scp -o StrictHostKeyChecking=no <file> dashboard@10.115.115.243:<dest>`

### PRODUCTION — Pi 3 @ `10.115.115.61` ← DO NOT TOUCH
- Actively in use as a live family calendar display
- Only deploy to this machine when explicitly instructed to push a production release
- Same credentials and paths as test Pi

- MOTD always prints to stderr — harmless noise, check actual exit codes or `echo ok`

## Paths on Pi
| What | Path |
|---|---|
| Backend source | `/opt/dashboard/backend/` |
| Frontend (nginx serves) | `/opt/dashboard/frontend-dist/` |
| SQLite DB | `/opt/dashboard/backend/dashboard.db` |
| Config JSON | `/opt/dashboard/backend/dashboard_config.json` |
| Env (OAuth keys) | `/opt/dashboard/backend/.env` |
| Python venv | `/opt/dashboard/venv/` |

## Services
- `dashboard-backend` — uvicorn on port 8001 (systemd, auto-restart)
- nginx — serves React SPA + proxies `/api/` to port 8001
- display.py — runs from `~/.bash_profile` while-loop on tty1 (needs real logind session for SDL kmsdrm; NOT a systemd service)

## Restart commands
```bash
# Backend (dashboard user has NOPASSWD for this — v2 image onwards)
echo dashboard | sudo -S systemctl restart dashboard-backend
# Display — kill and .bash_profile loop restarts it in 5s
pkill -f "display.py"
# If sudo not available, kill uvicorn directly (systemd restarts it)
pkill -f "uvicorn main:app"
```

## Deploy workflow
```bash
# 1. Build frontend
cd frontend && npm run build

# 2. Copy frontend
sshpass -p dashboard scp -o StrictHostKeyChecking=no -r frontend/dist/* dashboard@10.115.115.243:/opt/dashboard/frontend-dist/

# 3. Copy changed backend file(s)
sshpass -p dashboard scp -o StrictHostKeyChecking=no backend/routers/foo.py dashboard@10.115.115.243:/opt/dashboard/backend/routers/

# 4. Restart backend
sshpass -p dashboard ssh -o StrictHostKeyChecking=no dashboard@10.115.115.243 "pkill -f 'uvicorn main:app'"
```

## Stack
- **Backend**: FastAPI + uvicorn, SQLAlchemy ORM, SQLite, httpx
- **Frontend**: React 18, Vite, MUI (Material UI), React Router, Axios
- **Display**: Python + pygame, SDL kmsdrm (no X11)
- **Proxy**: nginx reverse proxy + static file server

## Database schema — `user_prefs`
| Column | Type | Notes |
|---|---|---|
| email | TEXT PK | Google account email |
| display_name | TEXT | Full name |
| display_color | TEXT | Hex color for calendar events |
| selected_calendars | JSON | List of Google calendar IDs to show |
| access_token | TEXT | Google OAuth access token |
| refresh_token | TEXT | Google OAuth refresh token |
| token_expiry | INTEGER | ms since epoch |
| role | TEXT | `owner` \| `admin` \| `user` |
| blocked | INTEGER | 0 = active, 1 = blocked |

Role rules: first user ever gets `owner`; any admin/owner can grant `admin`; only `owner` can demote admins; blocked users cannot re-sign-in and their calendars are hidden on the display.

## Config file — `dashboard_config.json`
Keys: `owm_api_key`, `owm_location`, `owm_units`, `rss_feeds` (list), `display_theme` (auto/light/dark), `display_view` (day/week/2week/month), `custom_fqdn`, `rolling_view`

## Image builder
- `pi/build-image.sh` — builds flashable `.img` using Docker (ARM64 chroot on Apple Silicon, QEMU on x86)
- Output: `pi/output/family-dashboard-v2.img`
- Cached base image: `pi/.cache/raspios-lite-arm64.img`
- Key scripts: `pi/chroot-setup.sh` (runs inside chroot), `pi/docker-customize.sh` (Docker wrapper)
- First-boot: Pi starts a `Dashboard-Setup` WiFi hotspot; user connects and visits `http://10.42.0.1` to enter WiFi credentials

## Google OAuth notes
- Uses `postmessage` redirect_uri (popup flow) — no redirect URI registered in Google Console
- Only **authorized JavaScript origins** matter: `http://10.115.115.243` works; `.local` hostnames do NOT work (Google permanently rejects them)
- Static DHCP lease = IP never changes = no need for a real domain on the local network
- OAuth tokens + Calendar API calls are the only traffic that leaves the local network

---

## Pending implementation (as of 2026-05-20)

### Backend
- **`auth.py`**: Check `prefs.blocked` → 403 before proceeding; auto-assign `role='owner'` when first user signs in (`db.query(UserPrefs).count() == 0`)
- **`user_prefs.py`**: Return `role` and `blocked` in all GET responses; reject PUT with 403 if blocked; add `PATCH /{email}/role` and `PATCH /{email}/blocked` endpoints
- **`calendar.py`** line ~110: filter `UserPrefs.blocked != 1` when querying users for event fetch
- **`system_settings.py`**: Add `custom_fqdn` and `rolling_view` to display GET/PUT

### Frontend
- **`Dashboard/index.jsx`**: Remove the Admin button from the footer (it's URL-only now)
- **`Settings/index.jsx`**: Role-gated rendering — `user` role sees only My Account; add promote/demote/block/unblock buttons to FamilyMembers section
- **`Admin/index.jsx`**: Add custom FQDN TextField and rolling_view toggle to DisplaySettings section

### Display
- **`display.py`**: Remove keyboard navigation (keep Q/ESC only for dev); implement rolling view (auto-advance anchor when current period doesn't contain today); use `custom_fqdn` from settings in footer
