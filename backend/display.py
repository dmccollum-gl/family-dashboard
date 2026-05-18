#!/usr/bin/env python3
"""
display.py — Pygame kiosk renderer for the family dashboard.

Usage:
    python3 display.py                # windowed 1280×720
    python3 display.py --fullscreen   # fullscreen
    SDL_VIDEODRIVER=kmsdrm python3 display.py --fullscreen  # Pi framebuffer

Keyboard:
    ← / →   previous / next period
    T        jump to today
    D        day view
    W        week view (default)
    2        2-week view
    M        month view
    Q / ESC  quit
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
    sys.exit("pygame is required:  pip install pygame")

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

# ── constants ────────────────────────────────────────────────────────────────
API_BASE    = "http://localhost:8001/api"
REFRESH_SEC = 60
GRID_START  = 8
GRID_END    = 21
TOPBAR_H    = 118
FOOTER_H    = 26
LABEL_W     = 52   # left gutter for hour labels
ICON_CACHE  = Path("/tmp/dash_icons")
ICON_CACHE.mkdir(exist_ok=True)

DAYS_S  = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS_S = ["Jan","Feb","Mar","Apr","May","Jun",
            "Jul","Aug","Sep","Oct","Nov","Dec"]

# ── colour palettes ──────────────────────────────────────────────────────────
LIGHT = dict(
    bg       = (245, 245, 245),
    surface  = (255, 255, 255),
    border   = (218, 218, 218),
    text     = (28,  28,  28 ),
    subtext  = (110, 110, 110),
    accent   = (25,  118, 210),
    today_bg = (227, 242, 253),
    now_line = (211,  47,  47),
    footer   = (240, 240, 240),
    cal_red  = (211,  47,  47),
)
DARK = dict(
    bg       = (18,  18,  18 ),
    surface  = (30,  30,  30 ),
    border   = (52,  52,  52 ),
    text     = (228, 228, 228),
    subtext  = (148, 148, 148),
    accent   = (100, 181, 246),
    today_bg = (13,  47,  77 ),
    now_line = (239,  83,  80),
    footer   = (24,  24,  24 ),
    cal_red  = (239,  83,  80),
)

# ════════════════════════════════════════════════════════════════════════════
# SHARED STATE  (background-thread safe)
# ════════════════════════════════════════════════════════════════════════════
_lock  = threading.Lock()
_state: dict = {
    "weather":    None,
    "forecast":   [],
    "rss":        [],
    "events":     [],
    "last_fetch": 0.0,
}


def _fetch_all() -> None:
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
        raw = _get_json(f"{API_BASE}/rss/feed")
        items = raw if isinstance(raw, list) else raw.get("items", [])
        patch["rss"] = [
            {"title": i.get("title", ""), "source": i.get("feed_label", "")}
            for i in items[:80]
        ]
    except Exception:
        patch["rss"] = []
    try:
        raw = _get_json(f"{API_BASE}/calendar/events")
        patch["events"] = raw if isinstance(raw, list) else raw.get("events", [])
    except Exception:
        patch["events"] = []
    patch["last_fetch"] = time.time()
    with _lock:
        _state.update(patch)


def _refresh_if_needed(force: bool = False) -> None:
    with _lock:
        last = _state["last_fetch"]
    if force or (time.time() - last) >= REFRESH_SEC:
        threading.Thread(target=_fetch_all, daemon=True).start()


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
                f"https://openweathermap.org/img/wn/{code}@2x.png", path)
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


# ════════════════════════════════════════════════════════════════════════════
# THEME
# ════════════════════════════════════════════════════════════════════════════
def _theme(weather) -> dict:
    now = time.time()
    if weather:
        sr, ss = weather.get("sunrise", 0), weather.get("sunset", 0)
        if sr and ss:
            return LIGHT if sr <= now < ss else DARK
    h = datetime.now().hour
    return LIGHT if 6 <= h < 20 else DARK


# ════════════════════════════════════════════════════════════════════════════
# EVENT PARSING + LAYOUT
# ════════════════════════════════════════════════════════════════════════════
def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
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
        s_node = ev.get("start", {})
        e_node = ev.get("end",   {})
        s_raw  = s_node.get("dateTime") or s_node.get("date", "")
        e_raw  = e_node.get("dateTime") or e_node.get("date", "")
        ev_start = _parse_dt(s_raw)
        ev_end   = _parse_dt(e_raw)
        if not ev_start:
            continue
        if not ev_end:
            ev_end = ev_start + timedelta(hours=1)
        is_allday = ("date" in s_node and "dateTime" not in s_node)
        ev_date = ev_start.date()
        if not (start <= ev_date < end):
            continue
        # ensure datetime type
        if not isinstance(ev_start, datetime):
            ev_start = datetime.combine(ev_start, datetime.min.time()).astimezone()
        if not isinstance(ev_end, datetime):
            ev_end   = datetime.combine(ev_end,   datetime.min.time()).astimezone()
        rec = {
            "title":  ev.get("summary", "Untitled"),
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
        self.dwell = 8.0   # seconds per item

    def current(self, items: list) -> dict:
        if not items:
            return {}
        elapsed = time.time() - self.t0
        if elapsed >= self.dwell:
            self.idx = (self.idx + 1) % max(1, len(items))
            self.t0  = time.time()
        return items[self.idx % len(items)]

    def draw(self, surf: pygame.Surface, rect: pygame.Rect,
             items: list, size: int, color: tuple) -> None:
        item = self.current(items)
        if not item:
            return
        label  = f"[{item['source']}]  {item['title']}"
        fnt    = _font(size)
        ts     = _txt(label, size, color)
        max_w  = rect.width - 8
        if ts.get_width() > max_w:
            ts = _txt(_trunc(label, fnt, max_w), size, color)
        r = ts.get_rect(center=rect.center)
        old_clip = surf.get_clip()
        surf.set_clip(rect)
        surf.blit(ts, r)
        surf.set_clip(old_clip)


# ════════════════════════════════════════════════════════════════════════════
# TOP BAR
# ════════════════════════════════════════════════════════════════════════════
def _draw_topbar(surf: pygame.Surface, C: dict, W: int,
                 weather, forecast: list, rss: list, ticker: _Ticker) -> None:
    pygame.draw.rect(surf, C["surface"], (0, 0, W, TOPBAR_H))
    pygame.draw.line(surf, C["border"], (0, TOPBAR_H - 1), (W, TOPBAR_H - 1))

    now = datetime.now()

    # ── Calendar page icon ───────────────────────────────────────────────
    CAL_X, CAL_Y, CAL_W, CAL_H = 10, 8, 70, 80
    pygame.draw.rect(surf, C["surface"], (CAL_X, CAL_Y, CAL_W, CAL_H), border_radius=6)
    pygame.draw.rect(surf, C["border"],  (CAL_X, CAL_Y, CAL_W, CAL_H), 1, border_radius=6)
    # red header
    pygame.draw.rect(surf, C["cal_red"], (CAL_X, CAL_Y, CAL_W, 22), border_radius=6)
    pygame.draw.rect(surf, C["cal_red"], (CAL_X, CAL_Y + 16, CAL_W, 6))
    # month
    surf.blit(_txt(MONTHS_S[now.month - 1], 13, (255, 255, 255), bold=True),
              _txt(MONTHS_S[now.month - 1], 13, (255,255,255), bold=True)
              .get_rect(center=(CAL_X + CAL_W // 2, CAL_Y + 11)))
    # day number
    surf.blit(_txt(str(now.day), 30, C["text"], bold=True),
              _txt(str(now.day), 30, C["text"], bold=True)
              .get_rect(center=(CAL_X + CAL_W // 2, CAL_Y + 44)))
    # day-of-week
    surf.blit(_txt(DAYS_S[now.weekday()], 13, C["subtext"]),
              _txt(DAYS_S[now.weekday()], 13, C["subtext"])
              .get_rect(center=(CAL_X + CAL_W // 2, CAL_Y + 68)))

    # ── Clock ────────────────────────────────────────────────────────────
    clock_str = now.strftime("%-I:%M %p")
    clk_s  = _txt(clock_str, 62, C["text"], bold=True)
    clk_r  = clk_s.get_rect(midleft=(CAL_X + CAL_W + 14, TOPBAR_H // 2))
    surf.blit(clk_s, clk_r)
    clock_right = clk_r.right + 12

    # ── Weather (right side) ─────────────────────────────────────────────
    wx_left = W  # will shrink leftward as we add weather widgets
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

        # forecast strip (right-most)
        fc_days = (forecast or [])
        # skip today (index 0), show next 4
        fc_strip = fc_days[1:5]
        fc_w = len(fc_strip) * 62
        fx   = W - fc_w - 6
        for fd in fc_strip:
            fi = _load_icon(fd.get("icon", ""), 36)
            if fi:
                surf.blit(fi, (fx, 8))
            _blit(surf, f"{fd.get('high','')}{unit}", 13, C["text"],
                  fx + 18, 46, bold=True, anchor="midtop")
            _blit(surf, str(fd.get("low", "")),       12, C["subtext"],
                  fx + 18, 60, anchor="midtop")
            try:
                dlbl = DAYS_S[datetime.strptime(fd["date"], "%Y-%m-%d").weekday()]
            except Exception:
                dlbl = ""
            _blit(surf, dlbl, 12, C["subtext"], fx + 18, 75, anchor="midtop")
            fx += 62

        # current weather block (left of forecast strip)
        ic_size = 64
        ic  = _load_icon(icon, ic_size) if icon else None
        cw_x = W - fc_w - ic_size - 84
        if ic:
            surf.blit(ic, (cw_x, (TOPBAR_H - ic_size) // 2))
        tx = cw_x + ic_size + 4
        _blit(surf, f"{temp}{unit}", 38, C["text"], tx, 10, bold=True)
        _blit(surf, f"H:{hi}{unit}  L:{lo}{unit}", 13, C["subtext"], tx, 52)
        _blit(surf, f"Feels {feels}{unit}  {hum}%  {wind} {wunit}", 12, C["subtext"], tx, 68)
        _blit(surf, desc, 12, C["subtext"], tx, 84)
        wx_left = cw_x - 10

    # ── RSS ticker (center gap) ──────────────────────────────────────────
    gap_x1 = clock_right
    gap_x2 = wx_left - 8
    if gap_x2 - gap_x1 > 60:
        trect = pygame.Rect(gap_x1, 0, gap_x2 - gap_x1, TOPBAR_H)
        ticker.draw(surf, trect, rss, 20, C["text"])


# ════════════════════════════════════════════════════════════════════════════
# TIME GRID
# ════════════════════════════════════════════════════════════════════════════
def _draw_timegrid(surf: pygame.Surface, C: dict, events_raw: list,
                   start: date, num_days: int,
                   x: int, y: int, w: int, h: int) -> None:
    today       = date.today()
    grid_hours  = GRID_END - GRID_START
    col_w       = (w - LABEL_W) // num_days
    hdr_h       = 24
    allday_h    = 26
    grid_top    = y + hdr_h + allday_h
    grid_h      = h - hdr_h - allday_h
    row_h       = grid_h / grid_hours

    # background
    pygame.draw.rect(surf, C["surface"], (x, y, w, h))

    # day columns
    for d in range(num_days):
        dt   = start + timedelta(days=d)
        cx   = x + LABEL_W + d * col_w
        if dt == today:
            pygame.draw.rect(surf, C["today_bg"], (cx, y, col_w, h))
        pygame.draw.rect(surf, C["border"], (cx, y, col_w, hdr_h), 1)
        col  = C["accent"] if dt == today else C["text"]
        lbl  = f"{DAYS_S[dt.weekday()]} {dt.day}"
        _blit(surf, lbl, 14, col, cx + col_w // 2, y + hdr_h // 2,
              bold=(dt == today), anchor="center")

    # hour lines + labels
    for hr_idx in range(grid_hours + 1):
        yy = int(grid_top + hr_idx * row_h)
        pygame.draw.line(surf, C["border"], (x + LABEL_W, yy), (x + w, yy))
        if hr_idx < grid_hours:
            hr   = GRID_START + hr_idx
            ap   = "am" if hr < 12 else "pm"
            disp = hr if hr <= 12 else hr - 12
            _blit(surf, f"{disp}{ap}", 11, C["subtext"],
                  x + LABEL_W - 4, yy + 2, anchor="topright")

    # vertical dividers
    for d in range(1, num_days):
        cx = x + LABEL_W + d * col_w
        pygame.draw.line(surf, C["border"], (cx, y + hdr_h), (cx, y + h))

    # events
    end_date = start + timedelta(days=num_days)
    all_day_evs, timed_evs = _parse_events(events_raw, start, end_date)

    # all-day band
    for ev in all_day_evs:
        d = (ev["start"].date() - start).days
        if not (0 <= d < num_days):
            continue
        cx  = x + LABEL_W + d * col_w + 2
        cw  = col_w - 4
        clr = _hex_rgb(ev["color"])
        _rrect(surf, clr, pygame.Rect(cx, y + hdr_h + 2, cw, allday_h - 4), 3)
        fnt = _font(11)
        surf.blit(fnt.render(_trunc(ev["title"], fnt, cw - 4), True, (255,255,255)),
                  (cx + 2, y + hdr_h + (allday_h - fnt.get_height()) // 2))

    # timed events
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
        _rrect(surf, clr, pygame.Rect(int(ev_x), int(ev_y), ev_w, int(ev_h)), 4, 215)
        fnt = _font(11 if ev_h < 28 else 12)
        surf.blit(fnt.render(_trunc(ev["title"], fnt, ev_w - 4), True, (255,255,255)),
                  (ev_x + 2, ev_y + 2))
        if ev_h >= 26:
            t2 = _font(10).render(ev["start"].strftime("%-I:%M %p"), True, (220,220,220))
            surf.blit(t2, (ev_x + 2, ev_y + 14))

    # now line
    now = datetime.now()
    if start <= now.date() < end_date:
        now_min = now.hour * 60 + now.minute
        if GRID_START * 60 <= now_min < GRID_END * 60:
            ny  = int(grid_top + (now_min - GRID_START * 60) / 60 * row_h)
            d   = (now.date() - start).days
            lx  = x + LABEL_W + d * col_w
            pygame.draw.line(surf, C["now_line"], (lx, ny), (lx + col_w, ny), 2)
            pygame.draw.circle(surf, C["now_line"], (lx, ny), 4)


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
                surf.blit(fnt.render(_trunc(ev["title"], fnt, ew - 4), True, (255,255,255)),
                          (cx + 4, ey + 1))
            if len(day_evs) > 3:
                _blit(surf, f"+{len(day_evs)-3}", 10, C["subtext"],
                      cx + cell_w - 3, cy + cell_h - 14, anchor="topright")


# ════════════════════════════════════════════════════════════════════════════
# FOOTER
# ════════════════════════════════════════════════════════════════════════════
def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "?.?.?.?"


def _draw_footer(surf: pygame.Surface, C: dict, W: int, H: int, label: str) -> None:
    fy = H - FOOTER_H
    pygame.draw.rect(surf, C["footer"], (0, fy, W, FOOTER_H))
    pygame.draw.line(surf, C["border"], (0, fy), (W, fy))
    my = fy + FOOTER_H // 2
    _blit(surf, f"{_local_ip()}  {socket.gethostname()}", 12, C["subtext"],
          8, my, anchor="midleft")
    _blit(surf, label, 13, C["subtext"], W // 2, my, anchor="center")
    _blit(surf, "← →  navigate  │  D W 2 M  view  │  T today  │  Q quit",
          11, C["subtext"], W - 8, my, anchor="midright")


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
    # month — calendar grid (up to 6 weeks)
    first = anchor.replace(day=1)
    start = first - timedelta(days=first.weekday())
    return start, 42


def _period_label(view: str, anchor: date) -> str:
    if view == "day":
        return anchor.strftime("%A, %B %-d %Y")
    start, n = _period_bounds(view, anchor)
    end = start + timedelta(days=n - 1)
    if view in ("week", "2week"):
        return f"{start.strftime('%b %-d')} – {end.strftime('%b %-d, %Y')}"
    return anchor.strftime("%B %Y")


def _advance(view: str, anchor: date, delta: int) -> date:
    if view == "day":
        return anchor + timedelta(days=delta)
    if view == "week":
        return anchor + timedelta(weeks=delta)
    if view == "2week":
        return anchor + timedelta(weeks=2 * delta)
    # month
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
    args = ap.parse_args()

    pygame.init()
    pygame.mouse.set_visible(False)

    if args.fullscreen:
        info  = pygame.display.Info()
        W, H  = info.current_w, info.current_h
        flags = pygame.FULLSCREEN | pygame.NOFRAME
    else:
        W, H  = args.width, args.height
        flags = 0

    screen = pygame.display.set_mode((W, H), flags)
    pygame.display.set_caption("Family Dashboard")
    clock  = pygame.time.Clock()
    ticker = _Ticker()

    view   = "week"
    anchor = date.today()

    _refresh_if_needed(force=True)

    running = True
    while running:
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
                    view = "day";   anchor = date.today()
                elif k == pygame.K_w:
                    view = "week";  anchor = date.today()
                elif k == pygame.K_2:
                    view = "2week"; anchor = date.today()
                elif k == pygame.K_m:
                    view = "month"; anchor = date.today()

        _refresh_if_needed()

        with _lock:
            weather  = _state["weather"]
            forecast = list(_state["forecast"])
            rss      = list(_state["rss"])
            events   = list(_state["events"])

        C = _theme(weather)
        screen.fill(C["bg"])

        _draw_topbar(screen, C, W, weather, forecast, rss, ticker)

        cy = TOPBAR_H + 2
        ch = H - TOPBAR_H - FOOTER_H - 4
        start_date, span = _period_bounds(view, anchor)
        label = _period_label(view, anchor)

        if view in ("day", "week", "2week"):
            _draw_timegrid(screen, C, events, start_date, span, 0, cy, W, ch)
        else:
            _draw_cardgrid(screen, C, events, start_date, 6, 0, cy, W, ch)

        _draw_footer(screen, C, W, H, label)

        pygame.display.flip()
        clock.tick(30)

    pygame.quit()


if __name__ == "__main__":
    main()
