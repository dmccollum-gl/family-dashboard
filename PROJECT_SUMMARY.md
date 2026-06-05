# Family Dashboard — Project Summary

## What it is
Raspberry Pi kiosk: shared Google Calendar + weather + RSS ticker on a wall-mounted display. React web app lets family sign in with Google and manage settings. Admin page (URL-only, no nav link) handles system config.

---

## Hardware / Devices
| Role | Model | IP | Notes |
|---|---|---|---|
| **PROD** | Pi 3 | `10.115.115.61` | Live, in use — **DO NOT TOUCH** unless explicitly asked |
| **TEST** | Pi Zero 2W | `10.115.115.243` | Default deploy target |

- Both: user `dashboard` / pw `dashboard`, same paths
- SSH: `sshpass -p dashboard ssh -o StrictHostKeyChecking=no dashboard@<IP>`
- SCP: `sshpass -p dashboard scp -o StrictHostKeyChecking=no <file> dashboard@<IP>:<dest>`
- macOS sudo pw: `0420` → use `echo "0420" | sudo -S <cmd>`

---

## Paths on Pi
| What | Path |
|---|---|
| Backend | `/opt/dashboard/backend/` |
| Frontend (nginx) | `/opt/dashboard/frontend-dist/` |
| SQLite DB | `/opt/dashboard/backend/dashboard.db` |
| Config JSON | `/opt/dashboard/backend/dashboard_config.json` |
| OAuth keys | `/opt/dashboard/backend/.env` |
| Python venv | `/opt/dashboard/venv/` |
| Configured flag | `/opt/dashboard/.configured` |

---

## Stack
- **Backend**: FastAPI + uvicorn (port 8001), SQLAlchemy ORM, SQLite, httpx
- **Frontend**: React 18, Vite, MUI (Material UI), React Router, axios
- **Display**: Python + pygame-ce (import still `pygame`), SDL kmsdrm — no X11
- **Proxy**: nginx — serves React SPA, proxies `/api/` → port 8001

---

## Backend — `backend/`

### `main.py` — route prefixes
```
/api/settings   → system_settings.py   (OAuth, weather, RSS, display config, restarts)
/api/user-prefs → user_prefs.py         (CRUD per user)
/api/auth       → auth.py               (Google OAuth token exchange)
/api/weather    → weather.py            (Open-Meteo current/forecast/hourly)
/api/rss        → rss.py               (RSS feed aggregator)
/api/calendar   → calendar.py          (Google Calendar API, events cache)
/api/system     → system_info.py       (CPU/RAM/uptime)
/api/setup      → setup.py             (WiFi setup wizard, reboot, reset)
```

### `database.py` — `user_prefs` table
| Column | Type | Notes |
|---|---|---|
| email | TEXT PK | Google account |
| display_name | TEXT | |
| display_color | TEXT | hex, calendar event color |
| selected_calendars | JSON | list of `{"id": str, "color": str|null}` objects |
| access_token | TEXT | |
| refresh_token | TEXT | |
| token_expiry | INTEGER | ms epoch |
| role | TEXT | `owner`/`admin`/`user` |
| blocked | INTEGER | 0=active, 1=blocked |

Role rules: first user → `owner`; admin/owner can grant `admin`; only `owner` demotes admins; blocked users can't sign in and their calendars are hidden.

### `dashboard_config.json` keys
`owm_api_key` (legacy, unused), `owm_location`, `owm_units`, `rss_feeds[]`, `display_theme` (auto/light/dark), `display_view` (day/week/2week/month/rolling), `display_weather_view` (daily/hourly), `custom_fqdn`, `_geo_for`, `_geo_lat`, `_geo_lon`

**Weather source is Open-Meteo (free, no API key).** `owm_api_key` is a legacy config key not read by weather.py.

### `weather.py` key details
- Uses Open-Meteo API (free, no API key required)
- Geocoding via Open-Meteo geocoding API; results cached in `dashboard_config.json` (`_geo_for`, `_geo_lat`, `_geo_lon`)
- 5-minute in-memory cache shared between `/weather/current`, `/weather/forecast`, and `/weather/hourly`
- WMO weather codes mapped to OWM-style icon codes for display.py compatibility

### `calendar.py` key details
- `GET /api/calendar/events` — fetches all non-blocked users' selected calendars; 90s server-side cache
- Token refresh: uses `httpx.Client(verify=False)` POST to `oauth2.googleapis.com/token` — NOT google-auth lib (macOS Python 3.14 SSL bug)
- Event dedup: `_merge()` groups by `(_nt(title), start//60)` — end-time dropped to tolerate Google sync drift
- Merged events get `color_list` attribute (list of hex colors from each calendar that has the event)
- `selected_calendars` stored as `{"id", "color"}` dicts; `_normalize_calendars()` in user_prefs handles legacy plain-string IDs

### `setup.py` key details
- `GET /api/setup/status` — returns `{configured, setup_mode, connected, connection_type, ssid}`
- `GET /api/setup/wifi/scan` — nmcli + iw scan, returns networks sorted by signal, filters out `Dashboard-Setup` hotspot
- `POST /api/setup/configure` — saves config, clears user data, runs `pi-setup-apply.sh` in background thread via `sudo -n`
- `POST /api/setup/reboot` — reboots Pi without reconfiguring WiFi
- `POST /api/setup/reset` — deletes all users + RSS feeds from DB, keeps OAuth/weather creds
- Only NOPASSWD sudo: `dashboard ALL=(ALL) NOPASSWD: /opt/dashboard/pi-setup-apply.sh`

---

## Frontend — `frontend/src/pages/`

### `/` — `Dashboard/index.jsx`
React-rendered calendar view (mirrors display.py but in browser). Components:
- `ClockWidget` — digital clock
- `WeatherWidget` — current + forecast strip
- `NewsWidget` — RSS ticker
- `CalendarGrid` — time grid with timed events + all-day row
- `Footer` — view controls (day/week/2week/month), nav arrows

### `/settings` — `Settings/index.jsx`
Collapsible Accordion sections (all `defaultExpanded`):
- `MyAccount` — Google sign-in, calendar picker, color picker, family sharing dialog
- `PiDisplay` — theme/view/weather_view toggles
- `FamilyMembers` — list all users
- `RestartServices` — restart backend / display buttons
- `WeatherLocation` — city/units
- `RssSettings` — add/remove RSS feeds

### `/admin` — `Admin/index.jsx` (URL-only, no nav link)
- `OAuthSettings` — Google client ID + secret
- `WeatherSettings` — location + units (Open-Meteo, no API key field)
- `ResetSection` — wipes all users + feeds

### `/setup` — `Setup/index.jsx`
2-step wizard (activation code removed; shown when device in hotspot mode or not configured):
1. **WiFi Network** — scan list (auto-rescan every 20s until SSID picked), manual entry fallback, password field. Persistent "Reboot Pi" button.
2. **Device Info** — device name, city/ZIP
3. **Applying** — progress → success/error

If already on home WiFi, skips step 0.

---

## Display — `backend/display.py` (~1450 lines)

### Architecture
- Dirty-rendering: `topbar_surf` and `grid_surf` cached between redraws; now-line overlaid cheaply each tick
- Main loop: 2 FPS; actual pixel work only on dirty flags
- `_s(px)` — scale function: `px * (W / 1280)` — all design in 1280px space
- `_FONT_SCALE = W / 1280` — all font sizes scaled to actual resolution (1920×1080 Pi renders at 1.5×)

### Fetch schedule
- Weather (current + forecast): 30 min
- Weather (hourly): 30 min
- Calendar: 10 min (server has 90s cache)
- RSS: 60 min
- Settings: 30 s

### Key functions
| Function | Purpose |
|---|---|
| `_rrect()` | Rounded rectangle fill |
| `_gradient_rrect()` | Horizontal gradient rounded rect (multi-calendar merged events) |
| `_layout_timed()` | Collision detection → assigns `col`/`tot` per event for side-by-side layout |
| `_parse_events()` | Splits raw events into timed/all-day, deduplicates via `_merge()` |
| `_draw_topbar()` | Clock, current weather + forecast/hourly strip (toggles on `weather_view`), RSS ticker |
| `_draw_timegrid()` | Main calendar grid (week/day/2week views) |
| `_draw_cardgrid()` | Month view (card-based) |
| `_draw_footer()` | View label, nav |
| `_event_text_color()` | WCAG luminance check → returns white or black for event text |

### Views
day / week (default) / 2week / month / rolling — keyboard: arrows=navigate, D/W/2/M=view, T=today, Q=quit (dev only)

### Theme
Auto light/dark from Open-Meteo sunrise/sunset timestamps; overridable via settings.

### Hourly weather
- Fetches `/api/weather/hourly` — displays next 12 hours as temperature + icon strip
- Toggle between daily forecast and hourly via `display_weather_view` setting

### Display startup (bash_profile, NOT systemd)
```bash
until curl -sf http://127.0.0.1:8001/api/health; do sleep 5; done
while true; do
  STATUS=$(curl -sf http://127.0.0.1:8001/api/setup/status)
  echo "$STATUS" | grep -q '"setup_mode":false' && break
  sleep 10
done
sleep 15
while true; do python3 display.py --fullscreen; sleep 5; done
```

---

## Pi Image Builder — `pi/`

### Build approach
**macOS-native** (no Docker required):
- `build-image.sh` downloads Pi OS Lite arm64, uses `hdiutil` to mount FAT32 boot partition, stages files and `firstrun.sh`
- `firstrun.sh` runs apt + pip on Pi's first boot (~15 min)
- No Docker — Docker Desktop on Apple Silicon has `unpigz exec format error` pulling `linux/arm64` images

### Scripts
| File | Purpose |
|---|---|
| `build-image.sh` | Main script — macOS native via hdiutil |
| `chroot-setup.sh` | Runs inside ARM64 chroot: apt-get, pip, systemd units, nginx, sudoers |
| `pi-setup-apply.sh` | Idempotent: nginx config, sudoers, tty1 autologin, gpu_mem=16, .bash_profile, WiFi via nmcli, hostname, reboot |
| `setup-mode.sh` | Runs on every boot: checks `.configured` flag, waits 20s for WiFi, starts hotspot if none |

### Boot flow
1. `setup-mode.sh` → if not configured or no WiFi in 20s → starts `Dashboard-Setup` hotspot
2. User connects phone/laptop to hotspot → visits `http://10.42.0.1`
3. Setup wizard → `POST /api/setup/configure` → `pi-setup-apply.sh` runs → reboots → connects to home WiFi
4. `.bash_profile` waits for uvicorn health + `setup_mode:false` → launches `display.py`

---

## Services
- `dashboard-backend` — systemd, uvicorn port 8001, auto-restart
- nginx — reverse proxy + static SPA server
- `display.py` — NOT a systemd service; launched from `~/.bash_profile` via getty@tty1 autologin (needs real logind/tty1 session for SDL kmsdrm DRM master)

### Restart commands (on Pi)
```bash
# Backend
echo dashboard | sudo -S systemctl restart dashboard-backend
# Display (bash_profile loop restarts in 5s)
pkill -f "display.py"
```

---

## Google OAuth
- Popup flow, `redirect_uri=postmessage`
- Only authorized JS origins matter (not redirect URIs)
- `http://10.115.115.243` authorized; `.local` hostnames rejected by Google permanently
- Static DHCP leases = IPs never change

---

## Key gotchas / lessons learned
- **Shell scripts: ASCII only** — Unicode (em-dash, ellipsis) in scripts causes `exec format error` or `unbound variable`. Check with `grep -Pc '[^\x00-\x7F]' script.sh`
- **pygame-ce not pygame** — `pygame` 2.6.x has no wheel for Python 3.14; use `pip install pygame-ce` (import is still `import pygame`)
- **gpu_mem=16** — default gpu_mem=128 leaves only 354MB; uvicorn+display.py exceed it → kswapd thrash. Set in config.txt.
- **display.py must wait for setup_mode:false** — otherwise starts during hotspot, fills uvicorn thread pool with API calls, blocks captive portal
- **`/api/health` and `/api/setup/status` must be `async def`** — sync routes block thread pool
- **pi-setup-apply.sh must be executable** — chmod +x; dashboard user owns it
- **nmcli needs `NoNewPrivileges` removed** from backend systemd service (done in apply script)
- **MOTD always prints to stderr** — harmless, check exit codes not stderr
- **No Docker for image build** — `unpigz exec format error` on Apple Silicon; use macOS-native hdiutil approach
- **Open-Meteo geocoding result cached in config** — if city doesn't geocode, check `_geo_lat`/`_geo_lon` in dashboard_config.json

---

## API Routes (complete)
| Method | Path | What it does |
|---|---|---|
| GET | `/api/health` | Liveness check |
| POST | `/api/auth/google` | Exchange Google auth code for tokens; creates user record |
| GET | `/api/user-prefs` | List all users |
| GET | `/api/user-prefs/{email}` | Get one user's prefs |
| PUT | `/api/user-prefs/{email}` | Save prefs (name, color, calendars, tokens) |
| DELETE | `/api/user-prefs/{email}` | Delete user |
| GET | `/api/calendar/events` | Aggregate events from all users; 90s cache |
| GET | `/api/weather/current` | Current conditions (Open-Meteo) |
| GET | `/api/weather/forecast` | 5-day forecast (Open-Meteo) |
| GET | `/api/weather/hourly` | Next 24h hourly (Open-Meteo) |
| GET | `/api/rss` | List configured RSS feeds |
| PUT | `/api/rss` | Save RSS feed list |
| GET | `/api/settings/oauth` | Get OAuth config (secret masked) |
| PUT | `/api/settings/oauth` | Save OAuth credentials to `.env` |
| GET | `/api/settings/weather` | Get weather config |
| PUT | `/api/settings/weather` | Save weather config |
| GET | `/api/settings/display` | Get display config (theme, view, weather_view, custom_fqdn) |
| PUT | `/api/settings/display` | Save display config |
| POST | `/api/settings/restart/backend` | Restart uvicorn via systemctl (1s delay) |
| POST | `/api/settings/restart/display` | Kill display.py (loop restarts it) |
| GET | `/api/system/info` | CPU% and RAM from /proc |
| GET | `/api/setup/status` | `{configured, setup_mode, connected, ssid}` |
| POST | `/api/setup/configure` | Apply WiFi/hostname from first-boot form |
| POST | `/api/setup/reboot` | Reboot Pi without reconfiguring |
| POST | `/api/setup/reset` | Remove all users + RSS feeds |

---

## Config Files

### `/opt/dashboard/backend/.env`
```
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
SECRET_KEY=...
FRONTEND_URL=http://10.115.115.243
```

### `/opt/dashboard/backend/dashboard_config.json`
```json
{
  "owm_location": "94556",
  "owm_units": "imperial",
  "rss_feeds": [],
  "display_theme": "auto",
  "display_view": "week",
  "display_weather_view": "daily",
  "custom_fqdn": "",
  "_geo_for": "94556",
  "_geo_lat": 37.82,
  "_geo_lon": -122.01
}
```

---

## Pending Work (as of 2026-05-30)

### Backend (not yet implemented)
1. **`auth.py`** — check `prefs.blocked` → 403; auto-assign `role='owner'` to first user (`db.query(UserPrefs).count() == 0`)
2. **`user_prefs.py`** — add `role`/`blocked` to all GET responses; 403 on PUT if blocked; add `PATCH /{email}/role` and `PATCH /{email}/blocked` with permission checks
3. **`calendar.py`** ~line 110 — filter `UserPrefs.blocked != 1` in user query

### Frontend (not yet implemented)
4. **`Settings/index.jsx`** — role-gated sections: `user` role sees only My Account; promote/demote/block/unblock in FamilyMembers
5. **`Admin/index.jsx`** — rolling_view toggle in DisplaySettings (custom FQDN field already done ✓)

### Display (not yet implemented)
6. **`display.py`** — remove keyboard nav beyond Q/ESC (LEFT/RIGHT/T/D/W/2/M still active); rolling view and `custom_fqdn` footer already done ✓

---

## Local dev
```bash
# Backend
cd backend && python3 -m uvicorn main:app --reload --port 8001

# Frontend (Vite proxies /api -> :8001)
cd frontend && npm run dev

# Display (windowed)
cd backend && python3 display.py
```

## Deploy to test Pi (10.115.115.243)
```bash
cd frontend && npm run build
sshpass -p dashboard scp -o StrictHostKeyChecking=no -r frontend/dist/* dashboard@10.115.115.243:/opt/dashboard/frontend-dist/
sshpass -p dashboard scp -o StrictHostKeyChecking=no backend/routers/foo.py dashboard@10.115.115.243:/opt/dashboard/backend/routers/
sshpass -p dashboard ssh -o StrictHostKeyChecking=no dashboard@10.115.115.243 "pkill -f 'uvicorn main:app'"
sshpass -p dashboard ssh -o StrictHostKeyChecking=no dashboard@10.115.115.243 "pkill -f 'display.py'"
```
