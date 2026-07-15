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
from collections import defaultdict
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

_FONT_SCALE: float = 1.0

def _s(px: int) -> int:
    """Scale a 1280×720 design-space pixel value to the actual display resolution."""
    return max(1, int(px * _FONT_SCALE))

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
SYSINFO_INTERVAL  = 0.5    # 500 ms — CPU / RAM stats (fast /proc reads)

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
    "hourly":        [],
    "rss":           [],
    "events":        [],
    "expired_users": [],   # names of users whose Google tokens couldn't be refreshed
    "settings":      {"theme": "auto", "view": "week", "weather_view": "daily"},
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
    try:
        hr = _get_json(f"{API_BASE}/weather/hourly")
        patch["hourly"] = hr.get("hours", []) if isinstance(hr, dict) else []
    except Exception:
        patch["hourly"] = []
    patch["weather_ts"]   = time.monotonic()
    patch["weather_busy"] = False
    with _lock:
        _state.update(patch)


def _fetch_calendar() -> None:
    patch: dict = {"calendar_ts": time.monotonic(), "calendar_busy": False}
    try:
        raw = _get_json(f"{API_BASE}/calendar/events")
        if isinstance(raw, list):
            patch["events"] = raw
        else:
            patch["events"]        = raw.get("events", [])
            patch["expired_users"] = raw.get("expired_users", [])
    except Exception:
        pass  # keep existing _state["events"] / expired_users — prevents blank calendar on network loss
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
    scaled = max(8, int(size * _FONT_SCALE))
    key = (scaled, bold)
    if key not in _fonts:
        try:
            variant = "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"
            _fonts[key] = pygame.font.Font(
                f"/usr/share/fonts/truetype/liberation/{variant}", scaled)
        except Exception:
            for name in ("DejaVu Sans", "Liberation Sans", "FreeSans", "Arial", None):
                try:
                    _fonts[key] = pygame.font.SysFont(name, scaled, bold=bold)
                    break
                except Exception:
                    continue
            else:
                _fonts[key] = pygame.font.Font(None, scaled)
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


def _format_time_range(start: datetime, end: datetime) -> str:
    """Compact start-end range, e.g. '9:45 - 11:30am' or '11:45am - 12:15pm' —
    only shown once when both ends share the same am/pm. Spaced around the
    dash (rather than tight) so a narrow event has a clean word-wrap point
    instead of getting truncated mid-string."""
    s_ap = start.strftime("%p").lower()
    e_ap = end.strftime("%p").lower()
    s = start.strftime("%-I:%M")
    e = end.strftime("%-I:%M")
    if s_ap == e_ap:
        return f"{s} - {e}{e_ap}"
    return f"{s}{s_ap} - {e}{e_ap}"


def _trunc(text: str, fnt: pygame.font.Font, max_w: int) -> str:
    if fnt.size(text)[0] <= max_w:
        return text
    while text and fnt.size(text + "…")[0] > max_w:
        text = text[:-1]
    return text + "…"


def _wrap_to_width(text: str, fnt: pygame.font.Font, max_w: int) -> list:
    """Greedy word-wrap to max_w. Hard-breaks any single word wider than the box
    so long unbroken titles still fill the line instead of overflowing."""
    lines: list = []
    cur = ""
    for word in str(text).split():
        test = (cur + " " + word).strip()
        if fnt.size(test)[0] <= max_w:
            cur = test
            continue
        if cur:
            lines.append(cur)
            cur = ""
        while fnt.size(word)[0] > max_w and len(word) > 1:
            n = 1
            while n < len(word) and fnt.size(word[:n + 1])[0] <= max_w:
                n += 1
            lines.append(word[:n])
            word = word[n:]
        cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _ellipsize_last(s: str, fnt: pygame.font.Font, max_w: int) -> str:
    """Force a trailing ellipsis onto a line, trimming until it fits."""
    s = s.rstrip()
    while s and fnt.size(s + "…")[0] > max_w:
        s = s[:-1]
    return (s + "…") if s else "…"


def _fit_time_lines(start: datetime, end: datetime, max_w: int, max_lines: int,
                    base_size: int = 11, min_size: int = 8):
    """Pick the largest font (down to min_size) that wraps the start-end time
    range to at most max_lines without needing a mid-word hard break, so the
    range shrinks before it ever gets truncated. If even min_size can't fit
    the full range (an extremely narrow box), fall back to the start time
    alone — still useful — rather than an awkward mid-range truncation."""
    full = _format_time_range(start, end)
    for size in range(base_size, min_size - 1, -1):
        fnt   = _font(size)
        lines = _wrap_to_width(full, fnt, max_w)
        if len(lines) <= max_lines:
            return fnt, lines
    start_only = start.strftime("%-I:%M%p").lower()
    for size in range(base_size, min_size - 1, -1):
        fnt = _font(size)
        if fnt.size(start_only)[0] <= max_w:
            return fnt, [start_only]
    fnt = _font(min_size)
    return fnt, [_trunc(start_only, fnt, max_w)]


def _draw_event_label(surf: pygame.Surface, text: str, x: float, y: float,
                      max_w: float, max_h: float, color: tuple,
                      base_size: int = 16, min_size: int = 11,
                      valign: str = "top") -> None:
    """Render an event title wrapped to fill the (max_w × max_h) block, at the
    largest font that fits. Shows as much of the title as possible instead of
    clipping it to one line; only ellipsises the final visible line if the whole
    title can't fit even at min_size — so titles are never cut mid-word silently.
    """
    text = str(text).strip()
    max_w = int(max_w); max_h = int(max_h)
    if not text or max_w < 4 or max_h < 4:
        return

    chosen = None
    for size in range(int(base_size), int(min_size) - 1, -1):
        fnt    = _font(size)
        line_h = fnt.get_linesize()
        n_fit  = max(1, max_h // line_h)
        lines  = _wrap_to_width(text, fnt, max_w)
        if len(lines) <= n_fit:
            chosen = (fnt, lines, line_h)
            break
    if chosen is None:
        fnt    = _font(int(min_size))
        line_h = fnt.get_linesize()
        n_fit  = max(1, max_h // line_h)
        full   = _wrap_to_width(text, fnt, max_w)
        lines  = full[:n_fit]
        if len(full) > n_fit and lines:
            lines[-1] = _ellipsize_last(lines[-1], fnt, max_w)
        chosen = (fnt, lines, line_h)

    fnt, lines, line_h = chosen
    block_h = len(lines) * line_h
    ty = y + max(0, (max_h - block_h) // 2) if valign == "center" else y
    for i, ln in enumerate(lines):
        surf.blit(fnt.render(ln, True, color), (int(x), int(ty + i * line_h)))


def _rrect(surf: pygame.Surface, color: tuple, rect: pygame.Rect,
           radius: int = 5, alpha: int = 255) -> None:
    if alpha < 255:
        tmp = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
        pygame.draw.rect(tmp, (*color, alpha), tmp.get_rect(), border_radius=radius)
        surf.blit(tmp, rect.topleft)
    else:
        pygame.draw.rect(surf, color, rect, border_radius=radius)


def _gradient_rrect(surf: pygame.Surface, colors: list, rect: pygame.Rect,
                    radius: int = 5, alpha: int = 235) -> None:
    """Horizontal gradient rounded rect across any number of colour stops."""
    w, h = rect.width, max(1, rect.height)
    if not colors or w <= 0:
        return
    if len(colors) == 1:
        _rrect(surf, colors[0], rect, radius, alpha)
        return
    gs = pygame.Surface((w, h), pygame.SRCALPHA)
    n  = len(colors) - 1
    for xi in range(w):
        t   = xi / max(w - 1, 1) * n
        seg = min(int(t), n - 1)
        ft  = t - seg
        c1, c2 = colors[seg], colors[seg + 1]
        gs.set_at((xi, 0), (
            int(c1[0] + ft * (c2[0] - c1[0])),
            int(c1[1] + ft * (c2[1] - c1[1])),
            int(c1[2] + ft * (c2[2] - c1[2])),
            alpha,
        ))
    # Scale the single-row gradient to full height (fast blit-based stretch)
    gs = pygame.transform.scale(gs, (w, h))
    # Mask to rounded corners: BLEND_RGBA_MIN clips alpha outside the rect shape
    mask = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, alpha), (0, 0, w, h), border_radius=radius)
    gs.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    surf.blit(gs, rect.topleft)


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


def _calendar_key(ev: dict) -> str:
    """Identifies 'the same calendar' for column grouping — a person can have
    several calendars, so name alone isn't enough."""
    return f"{ev.get('userName', '')}\x1f{ev.get('calendarName') or ev.get('title', '')}"


def _greedy_columns(evs: list) -> None:
    """Greedy interval-graph coloring — assigns _subcol/_subtotal in place."""
    evs = sorted(evs, key=lambda e: e["start"])
    ends: list[float] = []
    for ev in evs:
        placed = False
        for i, ce in enumerate(ends):
            if ce <= ev["start"].timestamp():
                ev["_subcol"] = i
                ends[i] = ev["end"].timestamp()
                placed = True
                break
        if not placed:
            ev["_subcol"] = len(ends)
            ends.append(ev["end"].timestamp())
    for ev in evs:
        mx = ev["_subcol"]
        for ov in evs:
            if ov is not ev and (ev["start"].timestamp() < ov["end"].timestamp() and
                                  ev["end"].timestamp() > ov["start"].timestamp()):
                mx = max(mx, ov["_subcol"])
        ev["_subtotal"] = mx + 1


_MAX_COLS = 3  # cap on side-by-side columns within one tier — see _assign_macro_columns


def _assign_macro_columns(peers: list, order: dict) -> None:
    """Assign _col/_total/_subcol/_subtotal among a list of same-tier "peer"
    events (either all the day's root events, or all the children sharing one
    direct container). Must be called per-tier, after containment is resolved
    — a same-calendar double-booking that turns out to be contained inside
    another peer's tier needs its sub-columns computed among only that tier's
    siblings, not the calendar's events for the whole day.

    Width is NOT "how many calendars are in this peer group" — a calendar
    whose events never actually overlap another peer gets full width, same as
    plain side-by-side would if there were no conflict. Only calendars with a
    genuine time conflict share a narrower slot: events are grouped into
    connected components by real cross-calendar time overlap, and each
    component gets its own compact, alphabetically-ordered set of columns.
    Same-calendar double-bookings within a tier still sub-divide within their
    own column via _greedy_columns.
    """
    cal_groups: dict = defaultdict(list)
    for ev in peers:
        cal_groups[_calendar_key(ev)].append(ev)
    for grp in cal_groups.values():
        _greedy_columns(grp)

    n   = len(peers)
    adj = [[] for _ in range(n)]
    for i in range(n):
        a = peers[i]
        for j in range(i + 1, n):
            b = peers[j]
            if (_calendar_key(a) != _calendar_key(b) and
                    a["start"] < b["end"] and a["end"] > b["start"]):
                adj[i].append(j)
                adj[j].append(i)

    seen = [False] * n
    for i in range(n):
        if seen[i]:
            continue
        stack, comp = [i], []
        seen[i] = True
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for nb in adj[cur]:
                if not seen[nb]:
                    seen[nb] = True
                    stack.append(nb)
        comp_keys = sorted({_calendar_key(peers[idx]) for idx in comp},
                            key=lambda k: order.get(k, 0))

        if len(comp_keys) <= _MAX_COLS:
            key_to_col = {k: c for c, k in enumerate(comp_keys)}
            total = len(comp_keys)
            for idx in comp:
                ev = peers[idx]
                ev["_col"]   = key_to_col[_calendar_key(ev)]
                ev["_total"] = total
        else:
            # More distinct calendars are genuinely conflicting than can each
            # get a readable column — giving every one its own sliver just
            # makes all of them unreadable. Keep the first _MAX_COLS-1 (by the
            # stable order) in their own lane; pool everyone else into one
            # shared lane, sub-divided only among themselves by real overlap.
            # Most overflow events won't actually overlap each other, so they
            # mostly get the full shared lane one at a time instead of every
            # calendar splitting evenly.
            primary = set(comp_keys[:_MAX_COLS - 1])
            key_to_col = {k: c for c, k in enumerate(comp_keys[:_MAX_COLS - 1])}
            overflow_evs = [peers[idx] for idx in comp if _calendar_key(peers[idx]) not in primary]
            _greedy_columns(overflow_evs)  # repurposes _subcol/_subtotal for shared-lane packing
            for idx in comp:
                ev  = peers[idx]
                key = _calendar_key(ev)
                ev["_col"]   = key_to_col[key] if key in primary else _MAX_COLS - 1
                ev["_total"] = _MAX_COLS


_SPAN_RATIO = 2.0  # container must be at least this many times longer to "span across"

# Fraction of a spanning event's own width permanently reserved, left-aligned,
# for its own time/title — children rendered on top of it are confined to the
# remaining width, so neither event's title is ever covered by the other.
_SPINE_FRACTION = 0.34


def _find_container(ev: dict, day_evs: list):
    """The tightest OTHER event in day_evs that genuinely spans across ev, or
    None. Full geometric containment alone isn't enough — two comparable-length
    events often share a start or end time by coincidence, and those should
    still read as simultaneous siblings (side by side), not one "spanning"
    the other. Only count it when the container is markedly longer
    (_SPAN_RATIO), which is what actually distinguishes an all-day-ish
    "Block" from a same-length overlapping meeting."""
    ev_dur = ev["end"] - ev["start"]
    candidates = [
        o for o in day_evs
        if o is not ev and o["start"] <= ev["start"] and o["end"] >= ev["end"]
        and (o["start"], o["end"]) != (ev["start"], ev["end"])
        and (o["end"] - o["start"]) >= ev_dur * _SPAN_RATIO
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda o: o["end"] - o["start"])


def _layout_timed(evs: list) -> list:
    """Lay out timed events in stable per-calendar columns instead of raw greedy
    packing by start time — so a given person/calendar always lands in the same
    lane relative to whoever it's actually double-booked against, instead of
    shuffling day to day.

    Two different relationships are treated differently, per how people
    actually read a calendar:
      • Events genuinely at the same time (comparable, overlapping durations)
        render side by side, split via _assign_macro_columns.
      • An event that spans across (fully contains) a shorter one — e.g. an
        all-day "Block" with a specific meeting inside it — doesn't fight the
        shorter event for width. The long event renders as a full-width
        background layer; events it contains render on top of it, sized and
        split among only their own siblings (other events with the same
        direct container). This nests arbitrarily deep via ev["_container"].
    Same-calendar double-bookings still sub-divide within their own column
    via _greedy_columns, at whichever tier they land in.
    """
    if not evs:
        return evs

    # Stable order across the whole visible range (not just one day).
    order = {k: i for i, k in enumerate(sorted({_calendar_key(e) for e in evs}))}

    by_day: dict = defaultdict(list)
    for ev in evs:
        by_day[ev["start"].date()].append(ev)

    for day_evs in by_day.values():
        for ev in day_evs:
            ev["_container"] = _find_container(ev, day_evs)

        # Flatten to at most one level of nesting: a "child" always attaches
        # directly to its outermost background ancestor, never to another
        # child. Without this, a chain of comparably-sized overlapping events
        # (A spans B spans C spans D...) nests recursively, insetting further
        # at each level until events shrink into unreadable slivers.
        for ev in day_evs:
            root = ev["_container"]
            while root is not None and root.get("_container") is not None:
                root = root["_container"]
            ev["_container"] = root

        children_of: dict = defaultdict(list)
        for ev in day_evs:
            if ev["_container"] is not None:
                children_of[id(ev["_container"])].append(ev)

        def _layout_tier(peers: list) -> None:
            _assign_macro_columns(peers, order)
            for peer in peers:
                kids = children_of.get(id(peer))
                if kids:
                    _layout_tier(kids)

        roots = [e for e in day_evs if e["_container"] is None]
        _layout_tier(roots)

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
            "title":        ev.get("title") or ev.get("summary") or "Untitled",
            "start":        ev_start,
            "end":          ev_end,
            "color":        ev.get("color") or "#1976d2",
            "userName":     ev.get("userName") or "",
            "calendarName": ev.get("calendarName") or "",
            "_col":       0,
            "_total":     1,
            "_subcol":    0,
            "_subtotal":  1,
            "_container": None,
        }
        (all_day if is_allday else timed).append(rec)

    # Merge duplicate events shared across calendars into one with multiple colors.
    def _merge(evs: list, key_fn) -> list:
        groups: dict = defaultdict(list)
        for ev in evs:
            groups[key_fn(ev)].append(ev)
        out = []
        for grp in groups.values():
            if len(grp) == 1:
                out.append(grp[0])
            else:
                base = dict(grp[0])
                base["color_list"] = [e["color"] for e in grp]
                out.append(base)
        return out

    # Merge on title + start-minute only; dropping end-time tolerates sync drift
    # where the same invite lands with slightly different durations on two calendars.
    _nt = lambda s: " ".join(s.strip().lower().split())
    timed   = _merge(timed,   lambda e: (_nt(e["title"]),
                                          int(e["start"].timestamp()) // 60))
    all_day = _merge(all_day, lambda e: (_nt(e["title"]),
                                          e["start"].date(), e["end"].date()))

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
             items: list, C: dict) -> None:
        item = self.current(items)
        if not item:
            return

        source = item.get("source", "")
        title  = item.get("title", "")

        old_clip = surf.get_clip()
        surf.set_clip(rect)

        # ── Source bubble — pill tag on far left ──────────────────────
        bubble_right = rect.x
        if source:
            src_fnt  = _font(11, bold=True)
            src_surf = src_fnt.render(source, True, C["surface"])
            bpad_x   = _s(7)
            bpad_y   = _s(4)
            bw       = src_surf.get_width()  + bpad_x * 2
            bh       = src_surf.get_height() + bpad_y * 2
            bx       = rect.x + _s(6)
            by       = rect.centery - bh // 2
            pygame.draw.rect(surf, C["accent"],
                             (bx, by, bw, bh), border_radius=_s(5))
            surf.blit(src_surf, src_surf.get_rect(
                center=(bx + bw // 2, by + bh // 2)))
            bubble_right = bx + bw

        # ── Headline — largest font size that fits the full text ──────
        text_x = bubble_right + _s(10)
        text_w = rect.right - text_x - _s(8)

        best_size:  int       = 14
        best_lines: list[str] = [title]
        for try_size in range(38, 13, -1):
            fnt = _font(try_size)
            if fnt.size(title)[0] <= text_w:
                best_size  = try_size
                best_lines = [title]
                break
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
            if cur:
                lines.append(cur)
            if len(lines) <= 2 and all(fnt.size(l)[0] <= text_w for l in lines):
                best_size  = try_size
                best_lines = lines
                break

        fnt     = _font(best_size)
        line_h  = fnt.get_linesize()
        total_h = len(best_lines) * line_h
        y0      = rect.centery - total_h // 2
        for i, line in enumerate(best_lines):
            surf.blit(fnt.render(line, True, C["text"]), (text_x, y0 + i * line_h))

        surf.set_clip(old_clip)


# ════════════════════════════════════════════════════════════════════════════
# TOP BAR  (renders into its own cached surface)
# ════════════════════════════════════════════════════════════════════════════
def _draw_topbar(surf: pygame.Surface, C: dict, W: int,
                 weather, forecast: list, hourly: list,
                 rss: list, ticker: _Ticker, weather_view: str = "daily") -> None:
    surf.fill(C["surface"])
    grad = pygame.Surface((W, _s(40)), pygame.SRCALPHA)
    grad.fill((0, 0, 0, 18))
    surf.blit(grad, (0, 0))
    pygame.draw.line(surf, C["accent"], (0, TOPBAR_H - 2), (W, TOPBAR_H - 2), 2)

    now = datetime.now()

    # ── Calendar page icon ───────────────────────────────────────────────
    CAL_X, CAL_Y, CAL_W, CAL_H = _s(10), _s(8), _s(70), _s(80)
    pygame.draw.rect(surf, C["surface"], (CAL_X, CAL_Y, CAL_W, CAL_H), border_radius=_s(6))
    pygame.draw.rect(surf, C["border"],  (CAL_X, CAL_Y, CAL_W, CAL_H), 1, border_radius=_s(6))
    pygame.draw.rect(surf, C["cal_red"], (CAL_X, CAL_Y, CAL_W, _s(22)), border_radius=_s(6))
    pygame.draw.rect(surf, C["cal_red"], (CAL_X, CAL_Y + _s(16), CAL_W, _s(6)))

    t = _txt(MONTHS_S[now.month - 1], 13, (255, 255, 255), bold=True)
    surf.blit(t, t.get_rect(center=(CAL_X + CAL_W // 2, CAL_Y + _s(11))))

    t = _txt(str(now.day), 30, C["text"], bold=True)
    surf.blit(t, t.get_rect(center=(CAL_X + CAL_W // 2, CAL_Y + _s(44))))

    t = _txt(DAYS_S[now.weekday()], 13, C["subtext"])
    surf.blit(t, t.get_rect(center=(CAL_X + CAL_W // 2, CAL_Y + _s(68))))

    # ── Clock ────────────────────────────────────────────────────────────
    clk_s = _txt(now.strftime("%-I:%M"), 71, C["text"], bold=True)
    clk_r = clk_s.get_rect(midleft=(CAL_X + CAL_W + _s(14), TOPBAR_H // 2))
    surf.blit(clk_s, clk_r)
    ampm_s = _txt(now.strftime("%p"), 18, C["text"])
    ampm_r = ampm_s.get_rect(topleft=(clk_r.right + _s(3), clk_r.top + _s(6)))
    surf.blit(ampm_s, ampm_r)
    clock_right = ampm_r.right + _s(12)

    # ── Weather (right side) ─────────────────────────────────────────────
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

        # ── Forecast strip (daily or hourly, rightmost) ───────────────────
        FC_COL = _s(62)

        if weather_view == "hourly":
            hr_strip = (hourly or [])[:6]
            fc_total = len(hr_strip) * FC_COL
            fc_x0    = W - fc_total - _s(6)
            for i, hr in enumerate(hr_strip):
                cx = fc_x0 + i * FC_COL + FC_COL // 2
                fi = _load_icon(hr.get("icon", ""), _s(44))
                if fi:
                    surf.blit(fi, fi.get_rect(midtop=(cx, _s(4))))
                _blit(surf, hr.get("time", ""),              11, C["subtext"], cx, _s(52), anchor="midtop")
                _blit(surf, f"{hr.get('temp','')}{unit}",   13, C["text"],    cx, _s(67), bold=True, anchor="midtop")
                pct = hr.get("precip", 0)
                if pct:
                    _blit(surf, f"{pct}%",                  11, C["subtext"], cx, _s(84), anchor="midtop")
        else:
            fc_strip = (forecast or [])[1:5]
            fc_total = len(fc_strip) * FC_COL
            fc_x0    = W - fc_total - _s(6)
            for i, fd in enumerate(fc_strip):
                cx = fc_x0 + i * FC_COL + FC_COL // 2
                fi = _load_icon(fd.get("icon", ""), _s(44))
                if fi:
                    surf.blit(fi, fi.get_rect(midtop=(cx, _s(4))))
                try:
                    dlbl = DAYS_S[datetime.strptime(fd["date"], "%Y-%m-%d").weekday()]
                except Exception:
                    dlbl = ""
                _blit(surf, dlbl,                            11, C["subtext"], cx, _s(52), anchor="midtop")
                _blit(surf, f"{fd.get('high', '')}{unit}",  13, C["text"],    cx, _s(67), bold=True, anchor="midtop")
                _blit(surf, f"{fd.get('low',  '')}",        11, C["subtext"], cx, _s(84), anchor="midtop")

        # ── Current weather block (icon + text, left of forecast) ─────────
        CUR_ICON_SZ = _s(54)
        TEXT_W      = _s(120)
        INNER_GAP   = _s(8)
        block_right = fc_x0 - _s(2)
        block_left  = block_right - CUR_ICON_SZ - INNER_GAP - TEXT_W

        ic = _load_icon(icon, CUR_ICON_SZ) if icon else None
        if ic:
            surf.blit(ic, ic.get_rect(midleft=(block_left, TOPBAR_H // 2)))

        tx = block_left + CUR_ICON_SZ + INNER_GAP
        _blit(surf, f"{temp}{unit}",               38, C["accent"],  tx, _s(4),  bold=True)
        _blit(surf, desc,                          12, C["subtext"], tx, _s(51))
        _blit(surf, f"H:{hi}{unit}  L:{lo}{unit}", 12, C["subtext"], tx, _s(67))
        _blit(surf, f"{feels}{unit}  {hum}%  {wind} {wunit}", 12, C["subtext"], tx, _s(82))
        if loc:
            _blit(surf, loc,                       11, C["subtext"], tx, _s(98))

        wx_left = block_left - _s(10)

    # ── RSS ticker (center gap) ──────────────────────────────────────────
    gap_x1 = clock_right
    gap_x2 = wx_left - _s(8)
    if gap_x2 - gap_x1 > _s(60):
        trect = pygame.Rect(gap_x1, 0, gap_x2 - gap_x1, TOPBAR_H)
        ticker.draw(surf, trect, rss, C)


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
    hdr_h      = _s(24)

    # Parse events first — needed to compute allday_h before laying out the grid.
    end_date = start + timedelta(days=num_days)
    all_day_evs, timed_evs = _parse_events(events_raw, start, end_date)

    # Assign all-day events to rows using a greedy span-aware algorithm.
    AD_ROW_H   = _s(26)
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
    allday_h = rows_needed * AD_ROW_H + _s(4) if rows_needed else _s(4)

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
            pill_w = min(col_w - _s(10), _s(74))
            pill_r = pygame.Rect(cx + (col_w - pill_w) // 2, y + _s(3), pill_w, hdr_h - _s(6))
            pygame.draw.rect(surf, C.get("today_hdr", C["accent"]), pill_r, border_radius=_s(10))
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
                  x + LABEL_W - _s(4), yy + _s(2), anchor="topright")

    for d in range(1, num_days):
        cx = x + LABEL_W + d * col_w
        pygame.draw.line(surf, C["border"], (cx, y + hdr_h), (cx, y + h))

    # All-day event bars — spanning multiple columns; split vertically for shared events.
    for ev, col_s, col_e, row in ad_placed:
        ex  = x + LABEL_W + col_s * col_w + _s(2)
        ew  = (col_e - col_s) * col_w - _s(4)
        ey  = y + hdr_h + _s(2) + row * AD_ROW_H
        eh  = AD_ROW_H - _s(2)
        color_list = ev.get("color_list")
        if color_list and len(color_list) > 1:
            clrs = [_hex_rgb(c) for c in color_list]
            n    = len(clrs)
            sw   = max(1, ew // n)
            for ci, c in enumerate(clrs):
                sx   = ex + ci * sw
                sw_i = sw if ci < n - 1 else (ex + ew - sx)
                _rrect(surf, c, pygame.Rect(sx, ey, sw_i, eh), _s(3), 240)
            avg     = tuple(sum(c[i] for c in clrs) // n for i in range(3))
            txt_clr = _event_text_color(avg)
        else:
            clr = _hex_rgb(ev["color"])
            _rrect(surf, clr, pygame.Rect(ex, ey, ew, eh), _s(3), 240)
            sh_h = max(_s(2), eh // 2)
            sh   = pygame.Surface((ew - _s(2), sh_h), pygame.SRCALPHA)
            sh.fill((255, 255, 255, 30))
            surf.blit(sh, (ex + _s(1), ey + _s(1)))
            txt_clr = _event_text_color(clr)
        _draw_event_label(surf, ev["title"], ex + _s(3), ey + _s(1),
                          ew - _s(6), eh - _s(2), txt_clr,
                          base_size=15, min_size=11, valign="center")

    def _clamp(v, lo, hi):
        return max(lo, min(hi, v))

    def _depth(ev: dict) -> int:
        """0 for a background/root event, +1 per level of containment — used
        purely to pick a draw order so contained events paint on top of
        whatever spans across them."""
        n, cur = 0, ev.get("_container")
        while cur is not None:
            n += 1
            cur = cur.get("_container")
        return n

    bounds_memo: dict = {}

    def _calc_bounds(ev: dict, day_bx: float):
        """Pixel (x, width) for ev, inset within its container's own bounds
        (recursively) if it spans across another event, or within the day
        column's macro/sub-column slot if it's a background/root event.

        A container's own time/title live in a reserved left "spine" (see
        _SPINE_FRACTION) that this never encroaches on — children only ever
        get the width to the right of it, so a child can't be positioned
        (by its own start time) directly over the container's label."""
        key = id(ev)
        if key in bounds_memo:
            return bounds_memo[key]
        col    = ev["_col"]
        tot    = ev["_total"]
        subcol = ev.get("_subcol", 0)
        subtot = ev.get("_subtotal", 1)
        container = ev.get("_container")
        if container is None:
            slot_x  = day_bx + _s(2)
            slot_w  = col_w - _s(4)
        else:
            parent_x, parent_w = _calc_bounds(container, day_bx)
            spine_w = parent_w * _SPINE_FRACTION
            avail_x = parent_x + spine_w
            avail_w = max(_s(10), parent_w - spine_w)
            inset   = min(_s(6), avail_w * 0.15)
            slot_x  = avail_x + inset
            slot_w  = max(_s(10), avail_w - 2 * inset)
        macro_w = slot_w / max(tot, 1)
        sub_w   = macro_w / max(subtot, 1)
        px = slot_x + col * macro_w + subcol * sub_w
        result = (px, sub_w)
        bounds_memo[key] = result
        return result

    has_children_ids = {
        id(ev["_container"]) for ev in timed_evs if ev.get("_container") is not None
    }

    visible_evs = []
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
        visible_evs.append((ev, d, s_min, e_min))

    # Draw background (root) events before whatever spans across them, so
    # contained events always paint on top of their container.
    visible_evs.sort(key=lambda t: _depth(t[0]))

    for ev, d, s_min, e_min in visible_evs:
        ev_y = grid_top + (s_min - GRID_START * 60) / 60 * row_h
        ev_h = max(16, (e_min - s_min) / 60 * row_h - 2)
        bx   = x + LABEL_W + d * col_w
        slot_x, slot_w = _calc_bounds(ev, bx)
        ev_w   = max(_s(6), int(slot_w) - _s(2))
        ev_x   = int(slot_x)
        nested = ev.get("_container") is not None
        if nested and int(ev_h) >= _s(4):
            # Drop shadow so an event layered on top of a spanning one reads
            # as "in front of it" rather than just another adjacent block.
            shadow = pygame.Surface((ev_w, int(ev_h)), pygame.SRCALPHA)
            pygame.draw.rect(shadow, (0, 0, 0, 70), shadow.get_rect(), border_radius=_s(4))
            surf.blit(shadow, (ev_x + _s(2), int(ev_y) + _s(2)))
        color_list = ev.get("color_list")
        if color_list and len(color_list) > 1:
            clrs     = [_hex_rgb(c) for c in color_list]
            n        = len(clrs)
            avg      = tuple(sum(c[i] for c in clrs) // n for i in range(3))
            ev_txt_c = _event_text_color(avg)
            _gradient_rrect(surf, clrs,
                            pygame.Rect(int(ev_x), int(ev_y), ev_w, int(ev_h)),
                            _s(4), 235)
        else:
            clr = _hex_rgb(ev["color"])
            _rrect(surf, clr, pygame.Rect(int(ev_x), int(ev_y), ev_w, int(ev_h)), _s(4), 235)
            stripe_clr = tuple(min(255, c + 65) for c in clr)
            _rrect(surf, stripe_clr, pygame.Rect(int(ev_x), int(ev_y), _s(4), int(ev_h)), _s(4))
            ev_txt_c = _event_text_color(clr)
        if nested:
            pygame.draw.rect(surf, (255, 255, 255),
                              pygame.Rect(int(ev_x), int(ev_y), ev_w, int(ev_h)),
                              width=2, border_radius=_s(4))
        if int(ev_h) >= _s(8):
            sh_h  = max(_s(3), int(ev_h * 0.38))
            sheen = pygame.Surface((ev_w - _s(2), sh_h), pygame.SRCALPHA)
            sheen.fill((255, 255, 255, 22))
            surf.blit(sheen, (int(ev_x) + _s(1), int(ev_y) + _s(1)))
        # Time range + title are one label block, time first — never a separate
        # element anchored to the bottom of the box, so they always read
        # together regardless of how tall the event is.
        # A spanning event with children only labels its own reserved spine —
        # children are already confined (via _calc_bounds) to the width to the
        # right of it, so this label can never end up covered by one of them.
        pad_x   = _s(6)
        label_w = ev_w * _SPINE_FRACTION if id(ev) in has_children_ids else ev_w
        lbl_x   = ev_x + pad_x
        lbl_y   = ev_y + _s(2)
        lbl_w   = label_w - pad_x - _s(3)
        lbl_h   = ev_h - _s(4)
        # Clip to the label area so a run of narrow same-calendar
        # double-bookings — or a container's children — can never bleed
        # text into a neighboring slot.
        prev_clip = surf.get_clip()
        surf.set_clip(pygame.Rect(int(ev_x), int(ev_y), int(label_w), int(ev_h)))
        min_time_h = _font(8).get_linesize()
        if lbl_h >= min_time_h + _s(10):
            # Shrink the font, then wrap onto a second line, before ever
            # truncating with "…" — the end time matters as much as the start
            # time, so it must stay legible rather than getting clipped off a
            # narrow column.
            max_lines = 2 if lbl_h >= min_time_h * 2 + _s(10) else 1
            time_fnt, time_lines = _fit_time_lines(ev["start"], ev["end"], int(lbl_w), max_lines)
            time_h = time_fnt.get_linesize()
            for ln in time_lines:
                t1 = time_fnt.render(ln, True, ev_txt_c)
                surf.blit(t1, (int(lbl_x), int(lbl_y)))
                lbl_y += time_h
                lbl_h -= time_h
        _draw_event_label(surf, ev["title"], lbl_x, lbl_y, lbl_w, lbl_h, ev_txt_c,
                          base_size=16, min_size=11, valign="top")
        surf.set_clip(prev_clip)

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
    hdr_h      = _s(24)
    grid_top   = grid_y + hdr_h + allday_h
    usable_h   = grid_h - hdr_h - allday_h
    row_h      = usable_h / grid_hours

    ny = int(grid_top + (now_min - GRID_START * 60) / 60 * row_h)
    d  = (now.date() - start_date).days
    lx = grid_x + LABEL_W + d * col_w
    glow = pygame.Surface((col_w + _s(4), _s(8)), pygame.SRCALPHA)
    glow.fill((*C["now_line"], 48))
    screen.blit(glow, (lx - _s(2), ny - _s(4)))
    pygame.draw.line(screen, C["now_line"], (lx, ny), (lx + col_w, ny), max(1, _s(2)))
    pygame.draw.circle(screen, C["now_line"], (lx, ny), _s(5))


# ════════════════════════════════════════════════════════════════════════════
# MONTH / CARD GRID
# ════════════════════════════════════════════════════════════════════════════
def _draw_cardgrid(surf: pygame.Surface, C: dict, events_raw: list,
                   start: date, num_weeks: int,
                   x: int, y: int, w: int, h: int) -> None:
    today  = date.today()
    cell_w = w // 7
    hdr_h  = _s(20)
    cell_h = (h - hdr_h) // num_weeks

    for d in range(7):
        _blit(surf, DAYS_S[d], 13, C["subtext"],
              x + d * cell_w + cell_w // 2, y + _s(2), anchor="midtop")

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
                ey  = cy + _s(18) + i * _s(15)
                ew  = cell_w - _s(4)
                clr = _hex_rgb(ev["color"])
                _rrect(surf, clr, pygame.Rect(cx + _s(2), ey, ew, _s(13)), _s(2))
                fnt = _font(10)
                surf.blit(fnt.render(_trunc(ev["title"], fnt, ew - _s(4)), True, (255, 255, 255)),
                          (cx + _s(4), ey + _s(1)))
            if len(day_evs) > 3:
                _blit(surf, f"+{len(day_evs)-3}", 10, C["subtext"],
                      cx + cell_w - _s(3), cy + cell_h - _s(14), anchor="topright")


# ════════════════════════════════════════════════════════════════════════════
# FOOTER  (baked into grid_surf; y coords relative to grid_surf top)
# ════════════════════════════════════════════════════════════════════════════
def _draw_footer(surf: pygame.Surface, C: dict, W: int,
                 label: str, ip: str, host: str, sysinfo: dict) -> None:
    """Renders into a dedicated FOOTER_H-tall surface so CPU/RAM updates
    don't require a full calendar grid redraw."""
    surf.fill(C["footer"])
    pygame.draw.line(surf, C["border"], (0, 0), (W, 0))
    my = FOOTER_H // 2
    _blit(surf, f"{host} ({ip})", 12, C["subtext"], _s(8), my, anchor="midleft")
    _blit(surf, label, 13, C["subtext"], W // 2, my, anchor="center")
    cpu = sysinfo.get("cpu", "--")
    ram = sysinfo.get("ram", "--")
    _blit(surf, f"CPU {cpu}  RAM {ram}", 12, C["subtext"], W - _s(8), my, anchor="midright")


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
            # Query native resolution before creating window — more reliable
            # than (0,0) with kmsdrm, which doesn't always honour the hint.
            info = pygame.display.Info()
            W, H = info.current_w, info.current_h
            if W <= 0 or H <= 0:
                # Info() failed (common on some kmsdrm setups before a window
                # exists); fall back to listing modes and picking the largest.
                modes = pygame.display.list_modes(flags=flags)
                if modes and modes != -1:
                    W, H = modes[0]
                else:
                    raise RuntimeError("Cannot determine display resolution")
            screen = pygame.display.set_mode((W, H), flags)
            actual_w, actual_h = screen.get_size()
            if actual_w > 0 and actual_h > 0:
                W, H = actual_w, actual_h  # use what SDL actually gave us
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

    # Scale all layout constants to the actual display resolution.
    global TOPBAR_H, FOOTER_H, LABEL_W, _FONT_SCALE, _fonts
    _FONT_SCALE = W / 1280
    TOPBAR_H = int(118 * _FONT_SCALE)
    FOOTER_H = int(26  * _FONT_SCALE)
    LABEL_W  = int(52  * _FONT_SCALE)
    _fonts   = {}  # clear any fonts cached before scale was set

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
                elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_x and (ev.mod & pygame.KMOD_CTRL):
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
    cur_allday_h = _s(26)  # kept in sync with _draw_timegrid return value

    # ── Cached surfaces (None = dirty, must be re-rendered) ───────────────────
    topbar_surf:  Optional[pygame.Surface] = None
    grid_surf:    Optional[pygame.Surface] = None
    footer_surf:  Optional[pygame.Surface] = None

    # Previous render state — used to detect what changed
    prev: dict = {
        "theme":        None,   # "light" or "dark"
        "view":         None,
        "anchor":       None,
        "minute":       None,   # (hour, minute)
        "day":          None,   # date — full grid rebuild at midnight
        "weather_ts":   -1.0,
        "calendar_ts":  -1.0,
        "rss_ts":       -1.0,
        "rss_idx":      -1,
        "settings_ts":  -1.0,
        "sysinfo_ts":   -1.0,
        "weather_view": None,
        "expired":      [],     # list of expired user names
    }

    # Grid area geometry (constant after init)
    GRID_Y  = 0                           # y within grid_surf
    GRID_H  = H - TOPBAR_H - FOOTER_H    # calendar usable height
    SURF_H  = GRID_H                      # grid_surf covers only the calendar area; footer is separate

    # Flag file written by the backend to blank the display without vcgencmd.
    # Polled every frame so the response is near-instant (≤0.5 s at 2 FPS).
    _DISPLAY_OFF_FLAG = Path("/opt/dashboard/.display_off")
    _was_blanked = False   # track previous state so we force a full redraw on resume

    running = True
    while running:
        # ── Input ────────────────────────────────────────────────────────────
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                k = ev.key
                if k == pygame.K_x and (ev.mod & pygame.KMOD_CTRL):
                    running = False

        # ── Display-off flag: go black while the flag file exists ─────────────
        if _DISPLAY_OFF_FLAG.exists():
            if not _was_blanked:
                screen.fill((0, 0, 0))
                pygame.display.flip()
                _was_blanked = True
                # Invalidate caches so the first frame after wake is fresh.
                grid_surf = topbar_surf = footer_surf = None
            clock.tick(2)
            continue   # skip all fetch / render logic while blanked

        if _was_blanked:
            # Just woke up — force all surfaces to be redrawn this frame.
            grid_surf = topbar_surf = footer_surf = None
            _was_blanked = False

        # ── Trigger stale fetches ─────────────────────────────────────────────
        _schedule_fetches()

        # ── Snapshot shared state ─────────────────────────────────────────────
        with _lock:
            snap_weather      = _state["weather"]
            snap_forecast     = list(_state["forecast"])
            snap_hourly       = list(_state["hourly"])
            snap_rss          = list(_state["rss"])
            snap_events       = list(_state["events"])
            snap_expired      = list(_state["expired_users"])
            snap_weather_ts   = _state["weather_ts"]
            snap_cal_ts       = _state["calendar_ts"]
            snap_rss_ts       = _state["rss_ts"]
            snap_settings     = dict(_state["settings"])
            snap_settings_ts  = _state["settings_ts"]
            snap_sysinfo      = dict(_state["sysinfo"])
            snap_sysinfo_ts   = _state["sysinfo_ts"]

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

        # ── Period label (needed by both grid and footer dirty checks) ──────────
        start_date, span = _period_bounds(view, anchor)
        label = _period_label(view, anchor)

        # ── Dirty flags ───────────────────────────────────────────────────────
        grid_dirty = (
            grid_surf is None                                  or
            cur_theme      != prev["theme"]                    or
            view           != prev["view"]                     or
            anchor         != prev["anchor"]                   or
            snap_cal_ts    != prev["calendar_ts"]              or
            cur_day        != prev["day"]                      or  # midnight: headers change
            frozenset(snap_expired) != frozenset(prev["expired"])  # token warning appeared/cleared
        )
        # Footer has its own surface — CPU/RAM changes only trigger a footer redraw,
        # not a full calendar grid redraw.
        footer_dirty = (
            footer_surf is None                        or
            cur_theme        != prev["theme"]          or
            view             != prev["view"]           or
            anchor           != prev["anchor"]         or
            cur_day          != prev["day"]            or
            snap_settings_ts != prev["settings_ts"]    or  # custom_fqdn may have changed
            snap_sysinfo_ts  != prev["sysinfo_ts"]         # CPU/RAM changed
        )
        cur_weather_view = snap_settings.get("weather_view", "daily")
        topbar_dirty = (
            topbar_surf is None                              or
            cur_theme         != prev["theme"]              or
            cur_minute        != prev["minute"]             or  # clock update
            snap_weather_ts   != prev["weather_ts"]         or
            snap_rss_ts       != prev["rss_ts"]             or
            cur_rss_idx       != prev["rss_idx"]            or  # ticker advanced
            cur_weather_view  != prev["weather_view"]           # forecast mode changed
        )

        needs_flip = grid_dirty or topbar_dirty or footer_dirty

        # ── Re-render dirty surfaces ──────────────────────────────────────────
        if grid_dirty:
            grid_surf = pygame.Surface((W, SURF_H))
            grid_surf.fill(C["bg"])
            if view in ("day", "week", "2week", "rolling"):
                cur_allday_h = _draw_timegrid(grid_surf, C, snap_events,
                                              start_date, span, 0, GRID_Y, W, GRID_H)
            else:
                _draw_cardgrid(grid_surf, C, snap_events,
                               start_date, 6, 0, GRID_Y, W, GRID_H)

            # ── Expired-token warning banner ──────────────────────────────────
            if snap_expired:
                WARN_H   = _s(22)
                names    = ", ".join(snap_expired)
                msg_text = f"⚠  Google token expired: {names}  —  visit Settings to re-sign in"
                warn     = pygame.Surface((W, WARN_H), pygame.SRCALPHA)
                warn.fill((200, 90, 0, 220))          # amber, semi-transparent
                fnt = _font(12, bold=True)
                ts  = fnt.render(msg_text, True, (255, 255, 255))
                warn.blit(ts, ts.get_rect(midleft=(_s(12), WARN_H // 2)))
                grid_surf.blit(warn, (0, SURF_H - WARN_H))

        if footer_dirty:
            footer_surf = pygame.Surface((W, FOOTER_H))
            display_host = snap_settings.get("custom_fqdn") or cached_host
            _draw_footer(footer_surf, C, W, label, cached_ip, display_host, snap_sysinfo)

        if topbar_dirty:
            topbar_surf = pygame.Surface((W, TOPBAR_H))
            _draw_topbar(topbar_surf, C, W, snap_weather, snap_forecast, snap_hourly,
                         snap_rss, ticker, snap_settings.get("weather_view", "daily"))

        # ── Composite + flip ──────────────────────────────────────────────────
        if needs_flip:
            screen.blit(grid_surf,   (0, TOPBAR_H))
            screen.blit(footer_surf, (0, H - FOOTER_H))
            screen.blit(topbar_surf, (0, 0))
            # now-line overlay — grid blit cleared the previous frame's line
            _draw_nowline(screen, C, view, anchor,
                          0, TOPBAR_H + GRID_Y, W, GRID_H, span, cur_allday_h)
            pygame.display.flip()

        # ── Update previous-render state ──────────────────────────────────────
        prev.update({
            "theme":        cur_theme,
            "view":         view,
            "anchor":       anchor,
            "minute":       cur_minute,
            "day":          cur_day,
            "weather_ts":   snap_weather_ts,
            "calendar_ts":  snap_cal_ts,
            "rss_ts":       snap_rss_ts,
            "rss_idx":      cur_rss_idx,
            "settings_ts":  snap_settings_ts,
            "sysinfo_ts":   snap_sysinfo_ts,
            "weather_view": cur_weather_view,
            "expired":      snap_expired,
        })

        clock.tick(2)   # 2 FPS — responsive enough for keyboard; CPU near-zero between dirty events

    pygame.quit()


if __name__ == "__main__":
    main()
