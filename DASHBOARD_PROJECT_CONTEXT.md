# Family Dashboard — Full Project Context

## Project Overview
A Raspberry Pi kiosk for a household. The Pi sits connected to a TV/monitor and shows a full-screen family calendar, weather, and news ticker. Any family member can open the web app on their phone or computer, sign in with Google, and their calendar events appear on the shared display. An admin controls who has access and configures system settings.

**Hardware**: Raspberry Pi Zero 2W — 512 MB RAM, ARM64, Pi OS Bookworm, no keyboard/mouse  
**Pi IP**: `10.115.115.243` (static DHCP lease), user/pass: `dashboard`/`dashboard`  
**Stack**: FastAPI + SQLite backend (port 8001) → nginx reverse proxy → React/MUI SPA; Pygame display runs directly on Pi console (SDL kmsdrm, no X11)  
**Image**: `pi/output/family-dashboard-v2.img` — flashable with Raspberry Pi Imager

---

## Frontend — Pages & Elements

### App.jsx (root)
- Checks `/api/setup/status` on load — redirects everything to `/setup` if WiFi isn't configured or hotspot is active
- Theme provider: light / dark / auto modes. Auto computes day/night from OpenWeatherMap sunrise/sunset times; falls back to 6 am–8 pm = light
- Theme cycles light → dark → auto on a toggle button in Dashboard header
- Routes: `/` Dashboard, `/settings` Settings, `/admin` Admin, `/setup` Setup

---

### Page: Setup (`/setup`)
**Purpose**: First-boot captive portal wizard. Pi creates a `Dashboard-Setup` WiFi hotspot; user connects and browses to `http://10.42.0.1`.

**Elements**:
- WiFi SSID + Password fields — stored by `pi-setup-apply.sh` into NetworkManager config
- Hostname field — sets the Pi's mDNS name (e.g. `family-dashboard`)
- City/location field — pre-populates weather config
- Activation code field — prevents random people on the hotspot from configuring the Pi
- Submit button — POSTs to `/api/setup/apply`, Pi reboots and joins the home network
- After setup, all routes are available; Setup route is no longer reachable

---

### Page: Dashboard (`/`)
**Purpose**: The main family view. Shown on phone/computer browsers and mirrors what the Pi display shows.

**Header bar elements**:
- App title / logo (left)
- Light/dark/auto theme toggle button (icon cycles through modes)
- "Sign In with Google" button — opens Google OAuth popup; on success, user is added to the database and their calendars appear

**Calendar grid** (main content):
- View tabs: Day / Week / 2 Week / Month — switches the calendar grid
- Navigation arrows — previous/next period
- Today button — snaps back to current period
- Calendar grid cells — each day column, rows by time slot
- Events rendered as colored chips using each user's chosen `display_color`
- Events from all signed-in, non-blocked family members are shown together
- Clicking an event shows a detail popover (title, time, calendar owner)

**Footer bar elements**:
- Current time (live clock)
- CPU % and RAM usage (fetched from `/api/system/info` every 30 s)
- Settings button — navigates to `/settings`
- *(Admin button was removed in v2 — Admin is URL-only at `/admin`)*

---

### Page: Settings (`/settings`)
**Purpose**: User-facing settings. What a user sees depends on their role.

#### My Account section (all roles)
- Avatar / color swatch — click to open a color picker; chosen color is used for this user's calendar events on the shared display
- Display name field — editable name shown in Family Members list
- Calendar list — checkboxes for each Google Calendar in the user's account; checked calendars appear on the shared display
- Save button — PUTs to `/api/user-prefs/{email}`

#### Family Members section (admin + owner only)
- List of all signed-in users with their avatar color, name, and email
- Each calendar row is clickable — opens **AssignCalendarDialog**:
  - Shows the calendar name and which user owns it
  - Toggle buttons: assign as Primary or Secondary display calendar for any dashboard user
  - Primary = always shown; Secondary = shown when space allows
- Per-user action buttons (admin/owner):
  - **Promote to Admin** — grants admin role (any admin/owner can do this)
  - **Demote** — removes admin role (owner only)
  - **Block** — prevents user from re-signing in; removes their calendars from display immediately. Admins cannot block other admins or owners
  - **Unblock** — re-enables a blocked user
  - **Delete** — removes user record entirely (lets them re-enroll fresh)
- Blocked users shown with a visual indicator; their calendars are greyed out

#### RSS Feeds section (admin + owner only)
- List of configured RSS feed URLs with labels
- Add Feed button — URL + label fields, validated before save
- Remove button per feed
- Feeds are shown as a scrolling ticker on the Pi display, refreshed every 60 seconds

#### Weather Location section (admin + owner only)
- Location field — accepts city/state ("Nashville, TN") or US ZIP code
- Units toggle — Imperial (°F) or Metric (°C)
- Saves to `dashboard_config.json` via `/api/settings/weather`

#### Add Calendar by URL section (always visible)
- URL field — paste a public Google Calendar or iCal URL
- Assign dropdown — pick which family member this calendar belongs to
- Primary / Secondary toggle — how the calendar is weighted on the display
- Info alert shown if no family members are signed in yet

---

### Page: Admin (`/admin`)
**Purpose**: System configuration. Accessed by URL only — no link from the app. Intended for the owner during initial setup; family members never see this page.

#### Google OAuth Credentials section
- Client ID field — from Google Cloud Console → APIs & Services → Credentials
- Client Secret field — show/hide toggle (masked after first save)
- Save button — writes to `/opt/dashboard/backend/.env`; backend restart picks up new values
- Explanation text pointing to Google Cloud Console

#### OpenWeatherMap section
- API Key field — free key from openweathermap.org (show/hide toggle)
- Location field — city/state or ZIP
- Units toggle — Imperial / Metric
- Save button — writes to `dashboard_config.json`

#### Pi Display section
- Theme toggle — Auto (sunrise/sunset) / Light / Dark — controls the Pi Pygame display color scheme
- View toggle — Day / Week / 2 Week / Month — default calendar view on the Pi display
- Rolling view toggle — when enabled, the anchor auto-advances so today is always visible (all views except Day)
- Custom FQDN field — optional; if set, shown in Pi display footer instead of hostname.local (for future real-domain use; `.local` cannot be used with Google OAuth)
- Save button — writes to `dashboard_config.json`; Pi display picks up changes within 30 s

#### Restart Services section
- **Restart Backend** button — calls `POST /api/settings/restart/backend`; backend restarts via `sudo systemctl restart dashboard-backend` (1 s delay so HTTP response completes first). Dashboard reconnects automatically.
- **Restart Display** button — calls `POST /api/settings/restart/display`; kills `display.py` process; `.bash_profile` loop restarts it within 5 s.
- Info/error alerts shown after click

#### Reset Install section
- **Reset Install** button — opens confirmation dialog
- Confirmation dialog explains: removes all signed-in users + RSS feeds; OAuth/weather credentials kept
- Calls `POST /api/setup/reset` — use to hand the dashboard to a new family or start fresh

---

## Pi Display — Pygame (display.py)
Runs full-screen directly on tty1 via SDL kmsdrm. No keyboard/mouse input (Q/ESC kept for development only).

**Layout zones**:

**Top bar** (always visible):
- Left: Date + live clock (seconds tick)
- Center: RSS ticker — scrolls headlines from configured feeds, refreshed every 60 s
- Right: Current weather — icon, temperature, description; then 4-day forecast icons with high/low
- Location label shown below weather (city name or configured string, not raw ZIP)

**Calendar grid** (main area):
- Current view: Day / Week / 2 Week / Month
- Rolling view: when enabled, anchor automatically snaps to today's period so today is always visible
- Events colored by user's `display_color`
- Multiple users' events layered per day cell

**Footer** (bottom bar):
- Left: custom FQDN or `hostname.local`
- Right: CPU% and RAM usage, updated every 30 s from `/proc/stat` and `/proc/meminfo` (no threads — inline reads, ~100 µs impact)

**Render strategy**: dirty rendering — two cached surfaces (`topbar_surf`, `grid_surf`). Only redrawn when inputs change (new events, new weather, new settings). Target: 2 FPS main loop, independent fetch threads for weather (30 min), calendar (10 min), RSS (1 min), settings (30 s).

---

## Backend — API Routes

| Method | Path | What it does |
|---|---|---|
| GET | `/api/health` | Liveness check |
| POST | `/api/auth/google` | Exchange Google auth code for tokens; creates user record; assigns `owner` role if first user |
| GET | `/api/user-prefs` | List all users (name, color, token status, role, blocked) |
| GET | `/api/user-prefs/{email}` | Get one user's prefs |
| PUT | `/api/user-prefs/{email}` | Save prefs (name, color, calendars, tokens); 403 if blocked |
| DELETE | `/api/user-prefs/{email}` | Delete user |
| PATCH | `/api/user-prefs/{email}/role` | Change role (owner/admin/user) with permission checks |
| PATCH | `/api/user-prefs/{email}/blocked` | Block/unblock user |
| GET | `/api/calendar/events` | Aggregate events from all non-blocked users; 90 s cache; auto-refreshes tokens |
| GET | `/api/weather/current` | Current conditions from OpenWeatherMap |
| GET | `/api/weather/forecast` | 5-day forecast from OpenWeatherMap |
| GET | `/api/rss` | List configured RSS feeds |
| PUT | `/api/rss` | Save RSS feed list |
| GET | `/api/settings/oauth` | Get OAuth config (secret masked) |
| PUT | `/api/settings/oauth` | Save OAuth credentials to `.env` |
| GET | `/api/settings/weather` | Get weather config |
| PUT | `/api/settings/weather` | Save weather config |
| GET | `/api/settings/display` | Get display config (theme, view, rolling_view, custom_fqdn) |
| PUT | `/api/settings/display` | Save display config |
| POST | `/api/settings/restart/backend` | Restart uvicorn via systemctl (1 s delay) |
| POST | `/api/settings/restart/display` | Kill display.py (loop restarts it) |
| GET | `/api/system/info` | CPU % and RAM usage from /proc |
| GET | `/api/setup/status` | `{configured, setup_mode}` |
| POST | `/api/setup/apply` | Apply WiFi/hostname from first-boot form |
| POST | `/api/setup/reset` | Remove all users + RSS feeds |

---

## Database — `user_prefs` table (SQLite)

| Column | Type | Notes |
|---|---|---|
| email | TEXT PK | Google account |
| display_name | TEXT | |
| display_color | TEXT | Hex, default `#1976d2` |
| selected_calendars | JSON | List of Google calendar IDs |
| access_token | TEXT | OAuth access token |
| refresh_token | TEXT | OAuth refresh token |
| token_expiry | INTEGER | ms since epoch |
| role | TEXT | `owner` \| `admin` \| `user` |
| blocked | INTEGER | 0 = active, 1 = blocked |

Schema migrations in `_migrate()` use `ALTER TABLE ADD COLUMN` with try/except (safe to run on existing DB).

---

## Role System
- **owner**: First user to ever sign in. Can do everything including demoting admins. Only one owner.
- **admin**: Can manage other users (promote to admin, block/unblock standard users, delete). Cannot touch other admins or the owner.
- **user**: Default. Sees only their own My Account in Settings. Cannot manage others.
- **blocked**: Cannot sign in (backend rejects). Their calendars removed from display. Still visible in Family Members for unblocking.

---

## Image Build System (`pi/`)

| File | Purpose |
|---|---|
| `build-image.sh` | Main script — builds frontend, downloads Pi OS base, runs Docker |
| `docker-customize.sh` | Runs inside Docker container — mounts image, mounts chroot, copies files |
| `chroot-setup.sh` | Runs inside ARM64 chroot — installs packages, creates user, configures systemd/nginx/sudoers |
| `services/` | systemd unit files (`dashboard-backend.service`, `dashboard-display.service`, `dashboard-setup.service`) |
| `pi-setup-apply.sh` | First-boot helper; `dashboard` user has NOPASSWD sudo for this + `systemctl restart dashboard-backend` |
| `setup-mode.sh` | Manages hotspot / NetworkManager toggle |
| `.cache/` | Cached Pi OS base image (not rebuilt unless `--no-cache`) |
| `output/family-dashboard-v2.img` | Latest flashable image |

**Build**: `bash pi/build-image.sh` from project root. Requires Docker Desktop running. ~15–30 min first run (downloads ARM packages); subsequent runs use cached base image and are faster.

**Flash**: Use Raspberry Pi Imager → Use Custom Image. No customization step needed — WiFi is set via the captive portal on first boot.

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
  "owm_api_key": "...",
  "owm_location": "Nashville, TN",
  "owm_units": "imperial",
  "rss_feeds": [{"url": "...", "label": "..."}],
  "display_theme": "auto",
  "display_view": "week",
  "rolling_view": true,
  "custom_fqdn": ""
}
```

---

## Pending Work (as of 2026-05-20)

### Backend (not yet deployed)
1. **`auth.py`** — check `prefs.blocked` → 403; auto-assign `role='owner'` to first user
2. **`user_prefs.py`** — add `role`/`blocked` to all GET responses; 403 on PUT if blocked; add `PATCH /{email}/role` and `PATCH /{email}/blocked` with permission checks
3. **`calendar.py`** — filter `UserPrefs.blocked != 1` in user query (~line 110)
4. **`system_settings.py`** — add `custom_fqdn` + `rolling_view` to display GET/PUT

### Frontend (not yet deployed)
5. **`Dashboard/index.jsx`** — remove Admin button from footer
6. **`Settings/index.jsx`** — role-gated sections; promote/demote/block/unblock buttons in Family Members
7. **`Admin/index.jsx`** — custom FQDN field + rolling view toggle in DisplaySettings

### Display
8. **`display.py`** — remove keyboard navigation (keep Q/ESC); rolling view auto-advance; use `custom_fqdn` in footer
