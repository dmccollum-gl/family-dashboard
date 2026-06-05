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
| Configured flag | `/opt/dashboard/.configured` |

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
- **Display**: Python + pygame-ce (import is still `import pygame`), SDL kmsdrm (no X11)
- **Proxy**: nginx reverse proxy + static file server

## Database schema — `user_prefs`
| Column | Type | Notes |
|---|---|---|
| email | TEXT PK | Google account email |
| display_name | TEXT | Full name |
| display_color | TEXT | Hex color for calendar events |
| selected_calendars | JSON | List of `{"id": str, "color": str|null}` objects |
| access_token | TEXT | Google OAuth access token |
| refresh_token | TEXT | Google OAuth refresh token |
| token_expiry | INTEGER | ms since epoch |
| role | TEXT | `owner` \| `admin` \| `user` |
| blocked | INTEGER | 0 = active, 1 = blocked |

Role rules: first user ever gets `owner`; any admin/owner can grant `admin`; only `owner` can demote admins; blocked users cannot re-sign-in and their calendars are hidden on the display.

## Config file — `dashboard_config.json`
Keys: `owm_api_key` (legacy, unused by weather.py), `owm_location`, `owm_units`, `rss_feeds` (list), `display_theme` (auto/light/dark), `display_view` (day/week/2week/month/rolling), `display_weather_view` (daily/hourly), `custom_fqdn`, `_geo_for`, `_geo_lat`, `_geo_lon` (geocode cache)

**Weather uses Open-Meteo (free, no API key).** `owm_api_key` is a legacy key still in config but not used.

## Weather — Open-Meteo
- `weather.py` uses Open-Meteo API (free, no key needed)
- Geocoding via Open-Meteo geocoding API; results cached in `dashboard_config.json` as `_geo_lat`/`_geo_lon`
- 5-minute in-memory cache for both current/forecast and hourly endpoints
- WMO weather codes mapped to OWM-style icon codes (e.g. `01d`) for display compatibility

## Image builder
- `pi/build-image.sh` — **macOS-native** (hdiutil mounts FAT32 boot partition; no Docker required)
- Workflow: downloads Pi OS Lite arm64, mounts boot partition, stages `firstrun.sh` into `cmdline.txt`
- `firstrun.sh` runs apt + pip on Pi's first boot (~15 min)
- Output: `pi/output/family-dashboard-v2.img`
- Cached base image: `pi/.cache/raspios-lite-arm64.img`
- Key scripts: `pi/chroot-setup.sh`, `pi/pi-setup-apply.sh`, `pi/setup-mode.sh`
- First-boot: Pi starts `Dashboard-Setup` WiFi hotspot; user connects and visits `http://10.42.0.1`

## Google OAuth notes
- Uses `postmessage` redirect_uri (popup flow) — no redirect URI registered in Google Console
- Only **authorized JavaScript origins** matter: `http://10.115.115.243` works; `.local` hostnames do NOT work (Google permanently rejects them)
- Static DHCP lease = IP never changes = no need for a real domain on the local network
- OAuth tokens + Calendar API calls are the only traffic that leaves the local network

---

## Implemented (as of 2026-05-30)

### Backend ✓
- **`auth.py`**: Blocked check → 403; auto-assign `role='owner'` for first user; `role` in return value
- **`user_prefs.py`**: `role`+`blocked` in all GET responses; 403 on PUT if blocked; `PATCH /{email}/role`; `PATCH /{email}/blocked`; owner cannot be deleted
- **`calendar.py`**: Filter `blocked != 1` in user query
- **`system_settings.py`**: `GET/PUT /api/settings/permissions` — configurable per-role section access

### Frontend ✓
- **`Settings/index.jsx`**: Role-gated sections (owner sees all; admin+user filtered by permissions config); FamilyMembers has role chips + promote/demote/block/unblock menu; PermissionsSettings component (owner configures admin+user; admin configures user only within their own sections)
- **`Admin/index.jsx`**: Rolling_view ToggleButtonGroup added to DisplaySettings ✓

### Display ✓
- ~~`display.py` keyboard nav~~ **DONE** ✓ — all nav keys removed; exit is Ctrl+X only
- Auto-detect display resolution via `pygame.display.Info()` + `list_modes()` fallback ✓

### DB bootstrap ✓
- `mccollumdavidj@gmail.com` set to `role='owner'` directly in SQLite (existing users had no role before auth.py was updated)
