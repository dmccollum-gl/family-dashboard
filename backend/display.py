#!/usr/bin/env python3
"""
display.py — Pygame kiosk renderer for the family dashboard.

Usage:
    python3 display.py                         # windowed 1280×720, auto theme
    python3 display.py --fullscreen            # fullscreen, auto theme
    python3 display.py --fullscreen --theme dark

Keyboard:
    ← / →      previous / next period
    T          jump to today
    D          day view
    W          week view (default)
    2          2-week view
    M          month view
    ` (tilde)  cycle theme: auto → light → dark → auto
    Q / ESC    quit

Dirty-rendering architecture
─────────────────────────────
Two off-screen surfaces are cached between redraws:
  • topbar_surf  — redrawn when: minute changes, weather updates, RSS item advances
  • grid_surf    — redrawn when: events update, view/anchor changes, theme changes,
                                  or the calendar date rolls over at midnight
The now-line is drawn as a cheap overlay on the composited screen each tick so
it never stains the caches. The main loop runs at 2 FPS; actual pixel work only
happens when a dirty flag fires.

Fetch schedule
──────────────
  Weather  : every 30 minutes  (OWM data updates on a similar cadence)
  Calendar : every 10 minutes  (backend has a 90-second server-side cache)
  RSS      : every 60 minutes  (feeds rarely change intra-day; ticker cycles items)
"""

import argparse, json, os, socket, sys, threading, time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import urllib.request, urllib.parse

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

try:
    import pygame
    import pygame.gfxdraw
except ImportError:
    sys.exit("pygame is required:  pip install pygame-ce")

try:
    import qrcode as _qrcode
    _HAS_QRCODE = True
except ImportError:
    _HAS_QRCODE = False

try:
    import httpx as _httpx
    def _get_json(url: str, params: dict | None = None, timeout: int = 10):
        with _httpx.Client(verify=False, timeout=timeout) as c:
            r = c.get(url, params=params)
            r.raise_for_status()
            return r.json()
except ImportError:
    def _get_json(url: str, params: dict | None = None, timeout: int = 10):  # type: ignore[misc]
        if params:
            url = url + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())


# ── layout constants ─────────────────────────────────────────────────────────
API_BASE     = "http://localhost:8001/api"
GRID_START   = 8
GRID_END     = 21
TOPBAR_H     = 118
FOOTER_H     = 26
LABEL_W      = 52
ICON_CACHE   = Path("/tmp/dash_icons")
ICON_CACHE.mkdir(exist_ok=True)

DAYS_S   = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS_S = ["Jan","Feb","Mar","Apr","May","Jun",
            "Jul","Aug","Sep","Oct","Nov","Dec"]

HOTSPOT_SSID    = "Dashboard-Setup"
SETUP_URL       = "http://10.42.0.1"
CONFIGURED_FLAG = Path("/opt/dashboard/.configured")

# ── fetch intervals (seconds) ────────────────────────────────────────────────
WEATHER_INTERVAL  = 1800   # 30 min — matches OWM update cadence
CALENDAR_INTERVAL = 600    # 10 min — backend has 90-second server-side cache
RSS_INTERVAL      = 60     # 1 min  — ticker cycles; content may update frequently
SETTINGS_INTERVAL = 30     # 30 sec — pick up admin-configured theme/view changes
SYSINFO_INTERVAL  = 30     # 30 sec — CPU / RAM stats (fast /proc reads)

# ── colour palettes ──────────────────────────────────────────────────────────
LIGHT = dict(
    bg        = (241, 245, 252),
    surface   = (255, 255, 255),
    border    = (206, 217, 234),
    text      = (18,  26,  52 ),
    subtext   = (98,  115, 148),
    accent    = (37,  99,  235),
    today_bg  = (213, 229, 255),
    today_hdr = (37,  99,  235),
    now_line  = (220,  38,  38),
    footer    = (226, 233, 247),
    cal_red   = (220,  38,  38),
)
DARK = dict(
    bg        = (8,   10,  18 ),
    surface   = (18,  24,  40 ),
    border    = (42,  55,  84 ),
    text      = (248, 250, 255),
    subtext   = (178, 200, 235),
    accent    = (96,  165, 250),
    today_bg  = (20,  52, 108 ),
    today_hdr = (56, 126, 246),
    now_line  = (250,  85,  85),
    footer    = (8,   10,  18 ),
    cal_red   = (250,  85,  85),
)


# ════════════════════════════════════════════════════════════════════════════
# SETUP / WELCOME SCREEN
# ════════════════════════════════════════════════════════════════════════════

def _is_configured() -> bool:
    if not CONFIGURED_FLAG.parent.exists():
        return True
    return CONFIGURED_FLAG.exists()


def _make_qr_surface(data: str) -> Optional[pygame.Surface]:
    if not _HAS_QRCODE:
        return None
    try:
        qr = _qrcode.QRCode(border=2)
        qr.add_data(data)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        n = len(matrix)
        surf = pygame.Surface((n, n))
        surf.fill((255, 255, 255))
        for r, row in enumerate(matrix):
            for c, cell in enumerate(row):
                if cell:
                    surf.set_at((c, r), (0, 0, 0))
        return surf
    except Exception:
        return None


def _draw_setup_screen(surf: pygame.Surface, W: int, H: int,
                        wifi_qr: Optional[pygame.Surface],
                        url_qr: Optional[pygame.Surface]) -> None:
    C = DARK
    surf.fill(C["bg"])
    cx = W // 2

    title_y  = max(24, H // 12)
    title_sz = max(32, min(56, W // 24))
    _blit(surf, "Family Dashboard", title_sz, C["text"], cx, title_y, bold=True, anchor="midtop")
    sub_y = title_y + title_sz + 10
    _blit(surf, "Setup Required", 24, C["subtext"], cx, sub_y, anchor="midtop")

    divider_y = sub_y + 42
    pygame.draw.line(surf, C["border"], (W // 5, divider_y), (4 * W // 5, divider_y))

    qr_size  = min(int(H * 0.40), 260, (W // 2) - 60)
    gap      = max(60, W // 7)
    pad      = 14
    qr_y     = divider_y + 24
    left_cx  = cx - gap // 2 - qr_size // 2 - pad
    right_cx = cx + gap // 2 + qr_size // 2 + pad

    panels = [
        (wifi_qr, left_cx,  "1. Connect to WiFi",  HOTSPOT_SSID),
        (url_qr,  right_cx, "2. Open in browser",  SETUP_URL),
    ]
    for qr_surf, panel_cx, label, sublabel in panels:
        box = pygame.Rect(
            panel_cx - qr_size // 2 - pad, qr_y - pad,
            qr_size + pad * 2,             qr_size + pad * 2,
        )
        pygame.draw.rect(surf, (255, 255, 255), box, border_radius=10)
        if qr_surf:
            scaled = pygame.transform.scale(qr_surf, (qr_size, qr_size))
            surf.blit(scaled, (panel_cx - qr_size // 2, qr_y))
        lbl_y = qr_y + qr_size + pad + 14
        _blit(surf, label,    20, C["text"],   panel_cx, lbl_y,      anchor="midtop")
        _blit(surf, sublabel, 18, C["accent"], panel_cx, lbl_y + 28, bold=True, anchor="midtop")

    dot_count = int(time.time() * 1.5) % 4
    _blit(surf, "Waiting for setup" + "." * dot_count,
          15, C["subtext"], cx, H - 18, anchor="midbottom")


# ════════════════════════════════════════════════════════════════════════════
# SHARED STATE — independent fetch threads per data source
# ════════════════════════════════════════════════════════════════════════════
_lock = threading.Lock()
_state: dict = {
    # data
    "weather":       None,
    "forecast":      [],
    "rss":           [],
    "events":        [],
    "settings":      {"theme": "auto", "view": "week"},
    "sysinfo":       {"cpu": "--", "ram": "--"},
    # timestamps (monotonic) — used as cache-invalidation tokens
    "weather_ts":    0.0,
    "calendar_ts":   0.0,
    "rss_ts":        0.0,
    "settings_ts":   0.0,
    "sysinfo_ts":    0.0,
    # in-flight guards
    "weather_busy":  False,
    "calendar_busy": False,
    "rss_busy":      False,
    "settings_busy": False,
}


def _fetch_weather() -> None:
    patch: dict = {}
    try:
        patch["weather"] = _get_json(f"{API_BASE}/weather/current")
    except Exception:
        patch["weather"] = None
    try:
        fc = _get_json(f"{API_BASE}/weather/forecast")
        patch["forecast"] = fc.get("days", []) if isinstance(fc, dict) else []
    except Exception:
        patch["forecast"] = []
    patch["weather_ts"]   = time.monotonic()
    patch["weather_busy"] = False
    with _lock:
        _state.update(patch)


def _fetch_calendar() -> None:
    patch: dict = {}
    try:
        raw = _get_json(f"{API_BASE}/calendar/events")
        patch["events"] = raw if isinstance(raw, list) else raw.get("events", [])
    except Exception:
        patch["events"] = []
    patch["calendar_ts"]   = time.monotonic()
    patch["calendar_busy"] = False
    with _lock:
        _state.update(patch)


def _fetch_rss() -> None:
    patch: dict = {}
    try:
        raw  = _get_json(f"{API_BASE}/rss/feed")
        items = raw if isinstance(raw, list) else raw.get("items", [])
        patch["rss"] = [
            {"title": i.get("title", ""), "source": i.get("source", i.get("feed_label", ""))}
            for i in items[:80]
        ]
    except Exception:
        patch["rss"] = []
    patch["rss_ts"]   = time.monotonic()
    patch["rss_busy"] = False
    with _lock:
        _state.update(patch)


def _fetch_settings() -> None:
    try:
        raw = _get_json(f"{API_BASE}/settings/display")
        with _lock:
            _state["settings"]      = raw
            _state["settings_ts"]   = time.monotonic()
            _state["settings_busy"] = False
    except Exception:
        with _lock:
            _state["settings_ts"]   = time.monotonic()
            _state["settings_busy"] = False


def _schedule_fetches(force_all: bool = False) -> None:
    """Start fetch threads for any source that is stale or force-refreshed."""
    now = time.monotonic()
    with _lock:
        s = {k: _state[k] for k in (
            "weather_ts", "calendar_ts", "rss_ts", "settings_ts",
            "weather_busy", "calendar_busy", "rss_busy", "settings_busy",
        )}

    if not s["weather_busy"] and (force_all or now - s["weather_ts"] >= WEATHER_INTERVAL):
        with _lock:
            _state["weather_busy"] = True
        threading.Thread(target=_fetch_weather, daemon=True).start()

    if not s["calendar_busy"] and (force_all or now - s["calendar_ts"] >= CALENDAR_INTERVAL):
        with _lock:
            _state["calendar_busy"] = True
        threading.Thread(target=_fetch_calendar, daemon=True).start()

    if not s["rss_busy"] and (force_all or now - s["rss_ts"] >= RSS_INTERVAL):
        with _lock:
            _state["rss_busy"] = True
        threading.Thread(target=_fetch_rss, daemon=True).start()

    if not s["settings_busy"] and (force_all or now - s["settings_ts"] >= SETTINGS_INTERVAL):
        with _lock:
            _state["settings_busy"] = True
        threading.Thread(target=_fetch_settings, daemon=True).start()


# ════════════════════════════════════════════════════════════════════════════
# SYSTEM INFO  (CPU + RAM — fast /proc reads, no network)
# ════════════════════════════════════════════════════════════════════════════
_cpu_state: dict = {"prev_busy": 0, "prev_total": 0, "ready": False}


def _read_sysinfo() -> dict:
    result: dict = {}
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        user   = int(parts[1])
        nice   = int(parts[2])
        system = int(parts[3])
        idle   = int(parts[4])
        iowait = int(parts[5]) if len(parts) > 5 else 0
        busy   = user + nice + system
        total  = busy + idle + iowait
        if _cpu_state["ready"]:
            d_total = total - _cpu_state["prev_total"]
            d_busy  = busy  - _cpu_state["prev_busy"]
            pct = round(100 * d_busy / d_total) if d_total > 0 else 0
            result["cpu"] = f"{pct}%"
        else:
            result["cpu"] = "--"
        _cpu_state.update({"prev_busy": busy, "prev_total": total, "ready": True})
    except Exception:
        result["cpu"] = "--"
    try:
        mem: dict = {}
        with open("/proc/meminfo") as f:
            for line in f:
                cols = line.split()
                if len(cols) >= 2:
                    mem[cols[0].rstrip(":")] = int(cols[1])
        total_mb = mem.get("MemTotal",     0) // 1024
        avail_mb = mem.get("MemAvailable", 0) // 1024
        used_mb  = total_mb - avail_mb
        result["ram"] = f"{used_mb}/{total_mb} MB"
    except Exception:
        result["ram"] = "--"
    return result


# ════════════════════════════════════════════════════════════════════════════
# ICON CACHE
# ════════════════════════════════════════════════════════════════════════════
_icon_cache: dict[str, Optional[pygame.Surface]] = {}


def _load_icon(code: str, size: int) -> Optional[pygame.Surface]:
    key = f"{code}_{size}"
    if key in _icon_cache:
        return _icon_cache[key]
    path = ICON_CACHE / f"{code}.png"
    if not path.exists():
        try:
            urllib.request.urlretrieve(
                f"https://openweathermap.org/img/wn/{code}@4x.png", path)
        except Exception:
            _icon_cache[key] = None
            return None
    try:
        surf = pygame.image.load(str(path)).convert_alpha()
        surf = pygame.transform.smoothscale(surf, (size, size))
        _icon_cache[key] = surf
        return surf
    except Exception:
        _icon_cache[key] = None
        return None


# ════════════════════════════════════════════════════════════════════════════
# FONT / TEXT HELPERS
# ════════════════════════════════════════════════════════════════════════════
_fonts: dict = {}


def _font(size: int, bold: bool = False) -> pygame.font.Font:
    key = (size, bold)
    if key not in _fonts:
        # Load Liberation Sans TTF directly — crisper than the SysFont name-lookup path.
        try:
            variant = "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"
            _fonts[key] = pygame.font.Font(
                f"/usr/share/fonts/truetype/liberation/{variant}", size)
        except Exception:
            for name in ("DejaVu Sans", "Liberation Sans", "FreeSans", "Arial", None):
                try:
                    _fonts[key] = pygame.font.SysFont(name, size, bold=bold)
                    break
                except Exception:
                    continue
            else:
                _fonts[key] = pygame.font.Font(None, size)
    return _fonts[key]


def _txt(text: str, size: int, color: tuple, bold: bool = False) -> pygame.Surface:
    return _font(size, bold).render(str(text), True, color)


def _blit(surf: pygame.Surface, text: str, size: int, color: tuple,
          x: int, y: int, bold: bool = False, anchor: str = "topleft") -> pygame.Rect:
    ts = _txt(text, size, color, bold)
    r  = ts.get_rect()
    setattr(r, anchor, (x, y))
    surf.blit(ts, r)
    return r


def _trunc(text: str, fnt: pygame.font.Font, max_w: int) -> str:
    if fnt.size(text)[0] <= max_w:
        return text
    while text and fnt.size(text + "…")[0] > max_w:
        text = text[:-1]
    return text + "…"


def _rrect(surf: pygame.Surface, color: tuple, rect: pygame.Rect,
           radius: int = 5, alpha: int = 255) -> None:
    if alpha < 255:
        tmp = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
        pygame.draw.rect(tmp, (*color, alpha), tmp.get_rect(), border_radius=radius)
        surf.blit(tmp, rect.topleft)
    else:
        pygame.draw.rect(surf, color, rect, border_radius=radius)


def _hex_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except Exception:
        return (80, 130, 220)


def _event_text_color(bg: tuple) -> tuple:
    """Return black or white for maximum contrast against bg (WCAG relative luminance)."""
    def _lin(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    lum = 0.2126 * _lin(bg[0]) + 0.7152 * _lin(bg[1]) + 0.0722 * _lin(bg[2])
    return (0, 0, 0) if lum > 0.179 else (255, 255, 255)


# ════════════════════════════════════════════════════════════════════════════
# THEME
# ════════════════════════════════════════════════════════════════════════════
def _theme_name(weather, mode: str) -> str:
    """Return 'light' or 'dark' given the chosen mode and live weather data."""
    if mode == "light":
        return "light"
    if mode == "dark":
        return "dark"
    # auto: sunrise/sunset from weather, fall back to clock hour
    now = time.time()
    if weather:
        sr, ss = weather.get("sunrise", 0), weather.get("sunset", 0)
        if sr and ss:
            return "light" if sr <= now < ss else "dark"
    return "light" if 6 <= datetime.now().hour < 20 else "dark"


# ════════════════════════════════════════════════════════════════════════════
# EVENT PARSING + LAYOUT
# ════════════════════════════════════════════════════════════════════════════
def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    # Bare date strings (all-day events) have no time component. Treat as local
    # midnight — not UTC midnight, which would shift the date backward in PDT.
    if len(s) == 10 and "T" not in s:
        try:
            return datetime.strptime(s, "%Y-%m-%d").astimezone()
        except ValueError:
            pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone()
        except ValueError:
            continue
    return None


def _layout_timed(evs: list) -> list:
    evs = sorted(evs, key=lambda e: e["start"])
    ends: list[float] = []
    for ev in evs:
        placed = False
        for i, ce in enumerate(ends):
            if ce <= ev["start"].timestamp():
                ev["_col"] = i
                ends[i] = ev["end"].timestamp()
                placed = True
                break
        if not placed:
            ev["_col"] = len(ends)
            ends.append(ev["end"].timestamp())
    for ev in evs:
        mx = ev["_col"]
        for ov in evs:
            if ov is not ev:
                if (ev["start"].timestamp() < ov["end"].timestamp() and
                        ev["end"].timestamp() > ov["start"].timestamp()):
                    mx = max(mx, ov["_col"])
        ev["_total"] = mx + 1
    return evs


def _parse_events(raw: list, start: date, end: date) -> tuple[list, list]:
    all_day, timed = [], []
    for ev in raw:
        s_raw    = ev.get("start") or ""
        e_raw    = ev.get("end")   or ""
        # events from the API have flat start/end strings (not nested dicts)
        if isinstance(s_raw, dict):
            s_raw = s_raw.get("dateTime") or s_raw.get("date", "")
        if isinstance(e_raw, dict):
            e_raw = e_raw.get("dateTime") or e_raw.get("date", "")
        ev_start = _parse_dt(str(s_raw))
        ev_end   = _parse_dt(str(e_raw))
        if not ev_start:
            continue
        if not ev_end:
            ev_end = ev_start + timedelta(hours=1)
        is_allday = ev.get("allDay", False)
        if not is_allday and isinstance(ev.get("start"), dict):
            is_allday = ("date" in ev["start"] and "dateTime" not in ev["start"])
        if is_allday:
            # Include multi-day all-day events that overlap the window, not just ones that start in it.
            if not (ev_start.date() < end and ev_end.date() > start):
                continue
        else:
            if not (start <= ev_start.date() < end):
                continue
        if not isinstance(ev_start, datetime):
            ev_start = datetime.combine(ev_start, datetime.min.time()).astimezone()
        if not isinstance(ev_end, datetime):
            ev_end   = datetime.combine(ev_end,   datetime.min.time()).astimezone()
        rec = {
            "title":  ev.get("title") or ev.get("summary") or "Untitled",
            "start":  ev_start,
            "end":    ev_end,
            "color":  ev.get("color") or "#1976d2",
            "_col":   0,
            "_total": 1,
        }
        (all_day if is_allday else timed).append(rec)
    _layout_timed(timed)
    return all_day, timed


# ════════════════════════════════════════════════════════════════════════════
# RSS TICKER
# ════════════════════════════════════════════════════════════════════════════
class _Ticker:
    def __init__(self) -> None:
        self.idx   = 0
        self.t0    = time.time()
        self.dwell = 8.0

    def current(self, items: list) -> dict:
        if not items:
            return {}
        if time.time() - self.t0 >= self.dwell:
            self.idx = (self.idx + 1) % max(1, len(items))
            self.t0  = time.time()
        return items[self.idx % len(items)]

    def draw(self, surf: pygame.Surface, rect: pygame.Rect,
             items: list, size: int, color: tuple) -> None:
        item = self.current(items)
        if not item:
            return

        source = item.get("source", "")
        title  = item.get("title", "")

        # Source label — fixed on far left, bold, slightly subdued
        src_fnt  = _font(size - 2, bold=True)
        src_surf = src_fnt.render(source, True, color) if source else None
        SOURCE_W = (src_surf.get_width() + 16) if src_surf else 0

        # Headline word-wraps to at most 2 lines in the remaining width
        fnt    = _font(size)
        text_x = rect.x + SOURCE_W + (8 if SOURCE_W else 4)
        text_w = rect.right - text_x - 8

        words  = title.split()
        lines: list[str] = []
        cur    = ""
        for word in words:
            test = (cur + " " + word).strip()
            if fnt.size(test)[0] <= text_w:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = word
            if len(lines) >= 2:
                cur = ""
                break
        if cur and len(lines) < 2:
            lines.append(cur)
        lines = lines[:2]
        if not lines:
            lines = [_trunc(title, fnt, text_w)]
        elif len(lines) == 2:
            lines[1] = _trunc(lines[1], fnt, text_w)
        else:
            lines[0] = _trunc(lines[0], fnt, text_w)

        line_h  = fnt.get_linesize()
        total_h = len(lines) * line_h
        y0      = rect.centery - total_h // 2

        old_clip = surf.get_clip()
        surf.set_clip(rect)
        if src_surf:
            surf.blit(src_surf, src_surf.get_rect(midleft=(rect.x + 6, rect.centery)))
        for i, line in enumerate(lines):
            surf.blit(fnt.render(line, True, color), (text_x, y0 + i * line_h))
        surf.set_clip(old_clip)


# ════════════════════════════════════════════════════════════════════════════
# TOP BAR  (renders into its own cached surface)
# ════════════════════════════════════════════════════════════════════════════
def _draw_topbar(surf: pygame.Surface, C: dict, W: int,
                 weather, forecast: list, rss: list, ticker: _Ticker) -> None:
    surf.fill(C["surface"])
    # Subtle darkening gradient at the top of the bar for depth
    grad = pygame.Surface((W, 40), pygame.SRCALPHA)
    grad.fill((0, 0, 0, 18))
    surf.blit(grad, (0, 0))
    # Accent line at the bottom instead of a flat gray border
    pygame.draw.line(surf, C["accent"], (0, TOPBAR_H - 2), (W, TOPBAR_H - 2), 2)

    now = datetime.now()

    # ── Calendar page icon ───────────────────────────────────────────────
    CAL_X, CAL_Y, CAL_W = 10, 8, 70
    pygame.draw.rect(surf, C["surface"], (CAL_X, CAL_Y, CAL_W, 80), border_radius=6)
    pygame.draw.rect(surf, C["border"],  (CAL_X, CAL_Y, CAL_W, 80), 1, border_radius=6)
    pygame.draw.rect(surf, C["cal_red"], (CAL_X, CAL_Y, CAL_W, 22), border_radius=6)
    pygame.draw.rect(surf, C["cal_red"], (CAL_X, CAL_Y + 16, CAL_W, 6))

    t = _txt(MONTHS_S[now.month - 1], 13, (255, 255, 255), bold=True)
    surf.blit(t, t.get_rect(center=(CAL_X + CAL_W // 2, CAL_Y + 11)))

    t = _txt(str(now.day), 30, C["text"], bold=True)
    surf.blit(t, t.get_rect(center=(CAL_X + CAL_W // 2, CAL_Y + 44)))

    t = _txt(DAYS_S[now.weekday()], 13, C["subtext"])
    surf.blit(t, t.get_rect(center=(CAL_X + CAL_W // 2, CAL_Y + 68)))

    # ── Clock ────────────────────────────────────────────────────────────
    clk_s = _txt(now.strftime("%-I:%M"), 71, C["text"], bold=True)
    clk_r = clk_s.get_rect(midleft=(CAL_X + CAL_W + 14, TOPBAR_H // 2))
    surf.blit(clk_s, clk_r)
    ampm_s = _txt(now.strftime("%p"), 18, C["text"])
    ampm_r = ampm_s.get_rect(topleft=(clk_r.right + 3, clk_r.top + 6))
    surf.blit(ampm_s, ampm_r)
    clock_right = ampm_r.right + 12

    # ── Weather (right side) ─────────────────────────────────────────────
    # Layout (right → left):
    #   [forecast strip: 4 cols × FC_COL px] [gap] [icon CUR_ICON_SZ] [gap] [text block TEXT_W]
    # All three sub-sections are computed before blitting to guarantee no overlap.
    wx_left = W
    if weather:
        unit  = weather.get("unit_symbol", "°F")
        icon  = weather.get("icon", "")
        temp  = weather.get("temp", "--")
        hi    = weather.get("temp_max", "")
        lo    = weather.get("temp_min", "")
        feels = weather.get("feels_like", "")
        hum   = weather.get("humidity", "")
        wind  = weather.get("wind_speed", "")
        wunit = weather.get("wind_unit", "mph")
        desc  = weather.get("description", "")
        loc   = weather.get("location_label", weather.get("city", ""))

        # ── 4-day forecast strip (rightmost) ─────────────────────────────
        FC_COL   = 62
        fc_strip = (forecast or [])[1:5]
        fc_total = len(fc_strip) * FC_COL
        fc_x0    = W - fc_total - 6

        for i, fd in enumerate(fc_strip):
            cx = fc_x0 + i * FC_COL + FC_COL // 2
            fi = _load_icon(fd.get("icon", ""), 44)
            if fi:
                surf.blit(fi, fi.get_rect(midtop=(cx, 4)))
            try:
                dlbl = DAYS_S[datetime.strptime(fd["date"], "%Y-%m-%d").weekday()]
            except Exception:
                dlbl = ""
            _blit(surf, dlbl,                            11, C["subtext"], cx, 52, anchor="midtop")
            _blit(surf, f"{fd.get('high', '')}{unit}",  13, C["text"],    cx, 67, bold=True, anchor="midtop")
            _blit(surf, f"{fd.get('low',  '')}",        11, C["subtext"], cx, 84, anchor="midtop")

        # ── Current weather block (icon + text, left of forecast) ─────────
        CUR_ICON_SZ = 54
        TEXT_W      = 185
        INNER_GAP   = 8
        block_right = fc_x0 - 2
        block_left  = block_right - CUR_ICON_SZ - INNER_GAP - TEXT_W

        ic = _load_icon(icon, CUR_ICON_SZ) if icon else None
        if ic:
            surf.blit(ic, ic.get_rect(midleft=(block_left, TOPBAR_H // 2)))

        tx = block_left + CUR_ICON_SZ + INNER_GAP
        _blit(surf, f"{temp}{unit}",               38, C["accent"],  tx,  4, bold=True)
        _blit(surf, desc,                          12, C["subtext"], tx, 51)
        _blit(surf, f"H:{hi}{unit}  L:{lo}{unit}", 12, C["subtext"], tx, 67)
        _blit(surf, f"{feels}{unit}  {hum}%  {wind} {wunit}", 12, C["subtext"], tx, 82)
        if loc:
            _blit(surf, loc,                       11, C["subtext"], tx, 98)

        wx_left = block_left - 10

    # ── RSS ticker (center gap) ──────────────────────────────────────────
    gap_x1 = clock_right
    gap_x2 = wx_left - 8
    if gap_x2 - gap_x1 > 60:
        trect = pygame.Rect(gap_x1, 0, gap_x2 - gap_x1, TOPBAR_H)
        ticker.draw(surf, trect, rss, 30, C["text"])


# ════════════════════════════════════════════════════════════════════════════
# TIME GRID  (renders into its own cached surface; now-line excluded)
# ════════════════════════════════════════════════════════════════════════════
def _draw_timegrid(surf: pygame.Surface, C: dict, events_raw: list,
                   start: date, num_days: int,
                   x: int, y: int, w: int, h: int) -> int:
    """Draw the time-grid view. Returns allday_h so the now-line overlay can match."""
    today      = date.today()
    grid_hours = GRID_END - GRID_START
    col_w      = (w - LABEL_W) // num_days
    hdr_h      = 24

    # Parse events first — needed to compute allday_h before laying out the grid.
    end_date = start + timedelta(days=num_days)
    all_day_evs, timed_evs = _parse_events(events_raw, start, end_date)

    # Assign all-day events to rows using a greedy span-aware algorithm.
    AD_ROW_H   = 26
    MAX_AD_ROWS = 3
    day_rows   = [[False] * MAX_AD_ROWS for _ in range(num_days)]  # day_rows[col][row]
    ad_placed  = []  # (ev, col_start, col_end, row)
    for ev in all_day_evs:
        col_s = max(0, (ev["start"].date() - start).days)
        col_e = min(num_days, (ev["end"].date() - start).days)
        if col_s >= col_e:
            continue
        row = next(
            (r for r in range(MAX_AD_ROWS)
             if all(not day_rows[c][r] for c in range(col_s, col_e))),
            -1,
        )
        if row == -1:
            continue
        for c in range(col_s, col_e):
            day_rows[c][row] = True
        ad_placed.append((ev, col_s, col_e, row))

    rows_needed = max(
        (r + 1 for c in range(num_days) for r in range(MAX_AD_ROWS) if day_rows[c][r]),
        default=0,
    )
    allday_h = rows_needed * AD_ROW_H + 4 if rows_needed else 4

    grid_top = y + hdr_h + allday_h
    grid_h   = h - hdr_h - allday_h
    row_h    = grid_h / grid_hours

    pygame.draw.rect(surf, C["surface"], (x, y, w, h))

    # ── Column background fills (today, weekend) ──────────────────────────────
    for d in range(num_days):
        dt = start + timedelta(days=d)
        cx = x + LABEL_W + d * col_w
        if dt == today:
            pygame.draw.rect(surf, C["today_bg"], (cx, y, col_w, h))
        elif dt.weekday() >= 5:
            wknd = pygame.Surface((col_w, h), pygame.SRCALPHA)
            wknd.fill((100, 100, 128, 10))
            surf.blit(wknd, (cx, y))

    # ── Day header labels — accent pill for today, plain text for others ──────
    for d in range(num_days):
        dt  = start + timedelta(days=d)
        cx  = x + LABEL_W + d * col_w
        lbl = f"{DAYS_S[dt.weekday()]} {dt.day}"
        if dt == today:
            pill_w = min(col_w - 10, 74)
            pill_r = pygame.Rect(cx + (col_w - pill_w) // 2, y + 3, pill_w, hdr_h - 6)
            pygame.draw.rect(surf, C.get("today_hdr", C["accent"]), pill_r, border_radius=10)
            _blit(surf, lbl, 13, (255, 255, 255), cx + col_w // 2, y + hdr_h // 2,
                  bold=True, anchor="center")
        else:
            _blit(surf, lbl, 13, C["text"], cx + col_w // 2, y + hdr_h // 2,
                  anchor="center")

    # Header row bottom separator
    pygame.draw.line(surf, C["border"], (x + LABEL_W, y + hdr_h), (x + w, y + hdr_h))

    # ── All-day zone subtle background + bottom separator ─────────────────────
    if rows_needed > 0:
        ad_bg = pygame.Surface((w - LABEL_W, allday_h), pygame.SRCALPHA)
        ad_bg.fill((100, 100, 128, 8))
        surf.blit(ad_bg, (x + LABEL_W, y + hdr_h))
        pygame.draw.line(surf, C["border"],
                         (x + LABEL_W, y + hdr_h + allday_h),
                         (x + w,       y + hdr_h + allday_h))

    # ── Subtle alternating hour bands for readability ─────────────────────────
    for hr_idx in range(0, grid_hours, 2):
        yy  = int(grid_top + hr_idx * row_h)
        bh  = int(row_h)
        bnd = pygame.Surface((w - LABEL_W, bh), pygame.SRCALPHA)
        bnd.fill((100, 100, 128, 8))
        surf.blit(bnd, (x + LABEL_W, yy))

    for hr_idx in range(grid_hours + 1):
        yy = int(grid_top + hr_idx * row_h)
        pygame.draw.line(surf, C["border"], (x + LABEL_W, yy), (x + w, yy))
        if hr_idx < grid_hours:
            hr   = GRID_START + hr_idx
            ap   = "am" if hr < 12 else "pm"
            disp = hr if hr <= 12 else hr - 12
            _blit(surf, f"{disp}{ap}", 11, C["subtext"],
                  x + LABEL_W - 4, yy + 2, anchor="topright")

    for d in range(1, num_days):
        cx = x + LABEL_W + d * col_w
        pygame.draw.line(surf, C["border"], (cx, y + hdr_h), (cx, y + h))

    # All-day event bars — spanning multiple columns when the event crosses day boundaries.
    fnt_ad = _font(14)
    for ev, col_s, col_e, row in ad_placed:
        ex  = x + LABEL_W + col_s * col_w + 2
        ew  = (col_e - col_s) * col_w - 4
        ey  = y + hdr_h + 2 + row * AD_ROW_H
        eh  = AD_ROW_H - 2
        clr = _hex_rgb(ev["color"])
        _rrect(surf, clr, pygame.Rect(ex, ey, ew, eh), 3, 240)
        # Glass sheen on upper portion
        sh_h = max(2, eh // 2)
        sh   = pygame.Surface((ew - 2, sh_h), pygame.SRCALPHA)
        sh.fill((255, 255, 255, 30))
        surf.blit(sh, (ex + 1, ey + 1))
        surf.blit(fnt_ad.render(_trunc(ev["title"], fnt_ad, ew - 4), True, _event_text_color(clr)),
                  (ex + 2, ey + (eh - fnt_ad.get_height()) // 2))

    def _clamp(v, lo, hi):
        return max(lo, min(hi, v))

    for ev in timed_evs:
        d = (ev["start"].date() - start).days
        if not (0 <= d < num_days):
            continue
        s_min = ev["start"].hour * 60 + ev["start"].minute
        e_min = ev["end"].hour   * 60 + ev["end"].minute
        s_min = _clamp(s_min, GRID_START * 60, GRID_END * 60)
        e_min = _clamp(e_min, GRID_START * 60, GRID_END * 60)
        if s_min >= e_min:
            continue
        ev_y = grid_top + (s_min - GRID_START * 60) / 60 * row_h
        ev_h = max(16, (e_min - s_min) / 60 * row_h - 2)
        col  = ev["_col"]
        tot  = ev["_total"]
        bx   = x + LABEL_W + d * col_w
        ev_x = bx + int(col_w * col / tot) + 1
        ev_w = int(col_w / tot) - 2
        clr  = _hex_rgb(ev["color"])
        # Full event body
        _rrect(surf, clr, pygame.Rect(int(ev_x), int(ev_y), ev_w, int(ev_h)), 4, 235)
        # Left accent stripe — lighter shade of the event color
        stripe_clr = tuple(min(255, c + 65) for c in clr)
        _rrect(surf, stripe_clr, pygame.Rect(int(ev_x), int(ev_y), 4, int(ev_h)), 4)
        # Glass sheen on upper portion
        if int(ev_h) >= 8:
            sh_h  = max(3, int(ev_h * 0.38))
            sheen = pygame.Surface((ev_w - 2, sh_h), pygame.SRCALPHA)
            sheen.fill((255, 255, 255, 22))
            surf.blit(sheen, (int(ev_x) + 1, int(ev_y) + 1))
        fnt      = _font(14 if ev_h < 40 else 16)
        ev_txt_c = _event_text_color(clr)
        surf.blit(fnt.render(_trunc(ev["title"], fnt, ev_w - 6), True, ev_txt_c),
                  (ev_x + 6, ev_y + 2))
        if ev_h >= 40:
            t2 = _font(13).render(ev["start"].strftime("%-I:%M %p"), True, ev_txt_c)
            surf.blit(t2, (ev_x + 6, ev_y + 20))

    # now-line is intentionally omitted here — drawn as an overlay in main()
    return allday_h


# ════════════════════════════════════════════════════════════════════════════
# NOW-LINE OVERLAY  (cheap; drawn directly on screen each tick)
# ════════════════════════════════════════════════════════════════════════════
def _draw_nowline(screen: pygame.Surface, C: dict,
                  view: str, anchor: date,
                  grid_x: int, grid_y: int, grid_w: int, grid_h: int,
                  num_days: int, allday_h: int = 26) -> None:
    if view not in ("day", "week", "2week", "rolling"):
        return
    now        = datetime.now()
    start_date = _period_bounds(view, anchor)[0]
    end_date   = start_date + timedelta(days=num_days)
    if not (start_date <= now.date() < end_date):
        return
    now_min = now.hour * 60 + now.minute
    if not (GRID_START * 60 <= now_min < GRID_END * 60):
        return

    grid_hours = GRID_END - GRID_START
    col_w      = (grid_w - LABEL_W) // num_days
    hdr_h      = 24
    grid_top   = grid_y + hdr_h + allday_h
    usable_h   = grid_h - hdr_h - allday_h
    row_h      = usable_h / grid_hours

    ny = int(grid_top + (now_min - GRID_START * 60) / 60 * row_h)
    d  = (now.date() - start_date).days
    lx = grid_x + LABEL_W + d * col_w
    # Soft glow behind the now-line
    glow = pygame.Surface((col_w + 4, 8), pygame.SRCALPHA)
    glow.fill((*C["now_line"], 48))
    screen.blit(glow, (lx - 2, ny - 4))
    # Main line + dot
    pygame.draw.line(screen, C["now_line"], (lx, ny), (lx + col_w, ny), 2)
    pygame.draw.circle(screen, C["now_line"], (lx, ny), 5)


# ════════════════════════════════════════════════════════════════════════════
# MONTH / CARD GRID
# ════════════════════════════════════════════════════════════════════════════
def _draw_cardgrid(surf: pygame.Surface, C: dict, events_raw: list,
                   start: date, num_weeks: int,
                   x: int, y: int, w: int, h: int) -> None:
    today  = date.today()
    cell_w = w // 7
    hdr_h  = 20
    cell_h = (h - hdr_h) // num_weeks

    for d in range(7):
        _blit(surf, DAYS_S[d], 13, C["subtext"],
              x + d * cell_w + cell_w // 2, y + 2, anchor="midtop")

    end_date = start + timedelta(weeks=num_weeks)
    all_day_evs, timed_evs = _parse_events(events_raw, start, end_date)
    all_evs = all_day_evs + timed_evs

    for wk in range(num_weeks):
        for d in range(7):
            dt  = start + timedelta(weeks=wk, days=d)
            cx  = x + d * cell_w
            cy  = y + hdr_h + wk * cell_h
            if dt == today:
                pygame.draw.rect(surf, C["today_bg"], (cx, cy, cell_w, cell_h))
            pygame.draw.rect(surf, C["border"], (cx, cy, cell_w, cell_h), 1)
            col = (C["accent"] if dt == today
                   else C["text"] if dt.month == today.month else C["subtext"])
            _blit(surf, str(dt.day), 13, col, cx + 4, cy + 3, bold=(dt == today))
            day_evs = [e for e in all_evs if e["start"].date() == dt]
            for i, ev in enumerate(day_evs[:3]):
                ey  = cy + 18 + i * 15
                ew  = cell_w - 4
                clr = _hex_rgb(ev["color"])
                _rrect(surf, clr, pygame.Rect(cx + 2, ey, ew, 13), 2)
                fnt = _font(10)
                surf.blit(fnt.render(_trunc(ev["title"], fnt, ew - 4), True, (255, 255, 255)),
                          (cx + 4, ey + 1))
            if len(day_evs) > 3:
                _blit(surf, f"+{len(day_evs)-3}", 10, C["subtext"],
                      cx + cell_w - 3, cy + cell_h - 14, anchor="topright")


# ════════════════════════════════════════════════════════════════════════════
# FOOTER  (baked into grid_surf; y coords relative to grid_surf top)
# ════════════════════════════════════════════════════════════════════════════
def _draw_footer(surf: pygame.Surface, C: dict, W: int, surf_H: int,
                 label: str, ip: str, host: str, sysinfo: dict) -> None:
    fy = surf_H - FOOTER_H
    pygame.draw.rect(surf, C["footer"], (0, fy, W, FOOTER_H))
    pygame.draw.line(surf, C["border"], (0, fy), (W, fy))
    my = fy + FOOTER_H // 2
    _blit(surf, f"{ip}  {host}", 12, C["subtext"], 8, my, anchor="midleft")
    _blit(surf, label, 13, C["subtext"], W // 2, my, anchor="center")
    cpu = sysinfo.get("cpu", "--")
    ram = sysinfo.get("ram", "--")
    _blit(surf, f"CPU {cpu}  RAM {ram}", 12, C["subtext"], W - 8, my, anchor="midright")


# ════════════════════════════════════════════════════════════════════════════
# VIEW / PERIOD HELPERS
# ════════════════════════════════════════════════════════════════════════════
def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _period_bounds(view: str, anchor: date) -> tuple[date, int]:
    if view == "day":
        return anchor, 1
    if view == "week":
        return _week_start(anchor), 7
    if view == "2week":
        return _week_start(anchor), 14
    if view == "rolling":
        return date.today(), 7
    first = anchor.replace(day=1)
    start = first - timedelta(days=first.weekday())
    return start, 42


def _period_label(view: str, anchor: date) -> str:
    if view == "day":
        return anchor.strftime("%A, %B %-d %Y")
    start, n = _period_bounds(view, anchor)
    end = start + timedelta(days=n - 1)
    if view in ("week", "2week", "rolling"):
        return f"{start.strftime('%b %-d')} – {end.strftime('%b %-d, %Y')}"
    return anchor.strftime("%B %Y")


def _advance(view: str, anchor: date, delta: int) -> date:
    if view == "day":
        return anchor + timedelta(days=delta)
    if view == "week":
        return anchor + timedelta(weeks=delta)
    if view == "2week":
        return anchor + timedelta(weeks=2 * delta)
    if view == "rolling":
        return anchor  # rolling view is always anchored to today
    m = anchor.month + delta
    y = anchor.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    return anchor.replace(year=y, month=m, day=1)


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════
def main() -> None:
    ap = argparse.ArgumentParser(description="Family dashboard kiosk")
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("--width",  type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--theme",  choices=["auto", "light", "dark"], default="auto",
                    help="Colour theme: auto (sunrise/sunset), light, or dark")
    args = ap.parse_args()

    pygame.init()
    pygame.mouse.set_visible(False)

    if args.fullscreen:
        flags = pygame.FULLSCREEN | pygame.NOFRAME
        try:
            screen = pygame.display.set_mode((0, 0), flags)
            W, H = screen.get_size()
            if W == 0 or H == 0:
                raise RuntimeError(f"display returned zero size {W}x{H}")
        except Exception as e:
            print(f"DISPLAY INIT FAILED: {e}", file=sys.stderr)
            print(f"SDL_VIDEODRIVER={os.environ.get('SDL_VIDEODRIVER','(not set)')}", file=sys.stderr)
            pygame.quit()
            sys.exit(1)
    else:
        W, H   = args.width, args.height
        screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("Family Dashboard")
    clock = pygame.time.Clock()

    # ── Setup / welcome screen ────────────────────────────────────────────────
    if not _is_configured():
        wifi_qr    = _make_qr_surface(f"WIFI:T:nopass;S:{HOTSPOT_SSID};;")
        url_qr     = _make_qr_surface(SETUP_URL)
        last_check = 0.0
        setup_running = True
        while setup_running:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    pygame.quit(); return
                elif ev.type == pygame.KEYDOWN and ev.key in (pygame.K_q, pygame.K_ESCAPE):
                    pygame.quit(); return
            now = time.time()
            if now - last_check >= 5:
                if _is_configured():
                    break
                last_check = now
            _draw_setup_screen(screen, W, H, wifi_qr, url_qr)
            pygame.display.flip()
            clock.tick(10)

    # ── Pre-compute static values ─────────────────────────────────────────────
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        cached_ip = s.getsockname()[0]
        s.close()
    except Exception:
        cached_ip = "?.?.?.?"

    cached_host = socket.gethostname()
    if "." not in cached_host:
        cached_host += ".local"

    # ── Load display settings synchronously before entering the loop ─────────
    try:
        _initial_settings = _get_json(f"{API_BASE}/settings/display")
        with _lock:
            _state["settings"]    = _initial_settings
            _state["settings_ts"] = time.monotonic()
    except Exception:
        _initial_settings = {}

    # CLI --theme flag overrides API setting for this session
    kb_theme_override = args.theme != "auto"
    kb_view_override  = False
    theme_mode = args.theme if kb_theme_override else _initial_settings.get("theme", "auto")
    view       = _initial_settings.get("view", "week")

    # ── Initial data fetch (all sources) ─────────────────────────────────────
    _schedule_fetches(force_all=True)

    ticker = _Ticker()
    anchor = date.today()
    cur_allday_h = 26  # kept in sync with _draw_timegrid return value

    # ── Cached surfaces (None = dirty, must be re-rendered) ───────────────────
    topbar_surf: Optional[pygame.Surface] = None
    grid_surf:   Optional[pygame.Surface] = None

    # Previous render state — used to detect what changed
    prev: dict = {
        "theme":       None,   # "light" or "dark"
        "view":        None,
        "anchor":      None,
        "minute":      None,   # (hour, minute)
        "day":         None,   # date — full grid rebuild at midnight
        "weather_ts":  -1.0,
        "calendar_ts": -1.0,
        "rss_ts":      -1.0,
        "rss_idx":     -1,
        "settings_ts": -1.0,
        "sysinfo_ts":  -1.0,
    }

    # Grid area geometry (constant after init)
    GRID_Y  = 0                           # y within grid_surf
    GRID_H  = H - TOPBAR_H - FOOTER_H    # calendar usable height
    SURF_H  = H - TOPBAR_H               # total height of grid_surf

    running = True
    while running:
        # ── Input ────────────────────────────────────────────────────────────
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                k = ev.key
                if k in (pygame.K_q, pygame.K_ESCAPE):
                    running = False
                elif k == pygame.K_LEFT:
                    anchor = _advance(view, anchor, -1)
                elif k == pygame.K_RIGHT:
                    anchor = _advance(view, anchor, +1)
                elif k == pygame.K_t:
                    anchor = date.today()
                elif k == pygame.K_d:
                    view = "day";   anchor = date.today(); kb_view_override = True
                elif k == pygame.K_w:
                    view = "week";  anchor = date.today(); kb_view_override = True
                elif k == pygame.K_2:
                    view = "2week"; anchor = date.today(); kb_view_override = True
                elif k == pygame.K_m:
                    view = "month"; anchor = date.today(); kb_view_override = True
                elif k == pygame.K_BACKQUOTE:
                    theme_mode = {"auto": "light", "light": "dark", "dark": "auto"}[theme_mode]
                    kb_theme_override = True

        # ── Trigger stale fetches ─────────────────────────────────────────────
        _schedule_fetches()

        # ── Snapshot shared state ─────────────────────────────────────────────
        with _lock:
            snap_weather     = _state["weather"]
            snap_forecast    = list(_state["forecast"])
            snap_rss         = list(_state["rss"])
            snap_events      = list(_state["events"])
            snap_weather_ts  = _state["weather_ts"]
            snap_cal_ts      = _state["calendar_ts"]
            snap_rss_ts      = _state["rss_ts"]
            snap_settings    = dict(_state["settings"])
            snap_settings_ts = _state["settings_ts"]
            snap_sysinfo     = dict(_state["sysinfo"])
            snap_sysinfo_ts  = _state["sysinfo_ts"]

        # ── Apply admin-configured display settings (unless keyboard overrode) ─
        if not kb_theme_override and snap_settings.get("theme"):
            theme_mode = snap_settings["theme"]
        if not kb_view_override and snap_settings.get("view"):
            new_api_view = snap_settings["view"]
            if new_api_view != view:
                view   = new_api_view
                anchor = date.today()

        # ── Sysinfo: read /proc every SYSINFO_INTERVAL seconds (fast, no thread) ─
        now_mono = time.monotonic()
        if now_mono - snap_sysinfo_ts >= SYSINFO_INTERVAL:
            new_si = _read_sysinfo()
            with _lock:
                _state["sysinfo"]    = new_si
                _state["sysinfo_ts"] = now_mono
            snap_sysinfo    = new_si
            snap_sysinfo_ts = now_mono

        now        = datetime.now()
        cur_minute = (now.hour, now.minute)
        cur_day    = now.date()
        cur_theme  = _theme_name(snap_weather, theme_mode)
        C          = LIGHT if cur_theme == "light" else DARK

        # Advance ticker and record its current index for dirty detection
        ticker.current(snap_rss)
        cur_rss_idx = ticker.idx

        # ── Dirty flags ───────────────────────────────────────────────────────
        grid_dirty = (
            topbar_surf is None                        or  # first frame
            grid_surf is None                          or
            cur_theme      != prev["theme"]            or
            view           != prev["view"]             or
            anchor         != prev["anchor"]           or
            snap_cal_ts    != prev["calendar_ts"]      or
            cur_day        != prev["day"]              or  # midnight: headers change
            snap_sysinfo_ts != prev["sysinfo_ts"]          # footer CPU/RAM changed
        )
        topbar_dirty = (
            topbar_surf is None                        or
            cur_theme      != prev["theme"]            or
            cur_minute     != prev["minute"]           or  # clock update
            snap_weather_ts != prev["weather_ts"]      or
            snap_rss_ts    != prev["rss_ts"]           or
            cur_rss_idx    != prev["rss_idx"]              # ticker advanced
        )

        needs_flip = grid_dirty or topbar_dirty

        # ── Re-render dirty surfaces ──────────────────────────────────────────
        if grid_dirty:
            grid_surf = pygame.Surface((W, SURF_H))
            grid_surf.fill(C["bg"])
            start_date, span = _period_bounds(view, anchor)
            label = _period_label(view, anchor)
            if view in ("day", "week", "2week", "rolling"):
                cur_allday_h = _draw_timegrid(grid_surf, C, snap_events,
                                              start_date, span, 0, GRID_Y, W, GRID_H)
            else:
                _draw_cardgrid(grid_surf, C, snap_events,
                               start_date, 6, 0, GRID_Y, W, GRID_H)
            _draw_footer(grid_surf, C, W, SURF_H, label, cached_ip, cached_host, snap_sysinfo)

        if topbar_dirty:
            topbar_surf = pygame.Surface((W, TOPBAR_H))
            _draw_topbar(topbar_surf, C, W, snap_weather, snap_forecast, snap_rss, ticker)

        # ── Composite + flip ──────────────────────────────────────────────────
        if needs_flip:
            screen.blit(grid_surf,   (0, TOPBAR_H))
            screen.blit(topbar_surf, (0, 0))
            # now-line is an overlay: grid blit erased last frame's line,
            # then we paint the current position on top.
            start_date, span = _period_bounds(view, anchor)
            _draw_nowline(screen, C, view, anchor,
                          0, TOPBAR_H + GRID_Y, W, GRID_H, span, cur_allday_h)
            pygame.display.flip()

        # ── Update previous-render state ──────────────────────────────────────
        prev.update({
            "theme":       cur_theme,
            "view":        view,
            "anchor":      anchor,
            "minute":      cur_minute,
            "day":         cur_day,
            "weather_ts":  snap_weather_ts,
            "calendar_ts": snap_cal_ts,
            "rss_ts":      snap_rss_ts,
            "rss_idx":     cur_rss_idx,
            "settings_ts": snap_settings_ts,
            "sysinfo_ts":  snap_sysinfo_ts,
        })

        clock.tick(2)   # 2 FPS — responsive enough for keyboard; CPU near-zero between dirty events

    pygame.quit()


if __name__ == "__main__":
    main()
