import { useState, useEffect, useCallback, useRef, useMemo, useLayoutEffect } from "react";
import {
  Box, Typography, Paper, Chip, IconButton, CircularProgress,
  Tooltip, ToggleButtonGroup, ToggleButton, Button,
  Snackbar, Alert,
} from "@mui/material";
import SettingsIcon      from "@mui/icons-material/Settings";
import WaterDropIcon     from "@mui/icons-material/WaterDrop";
import AirIcon           from "@mui/icons-material/Air";
import ThermostatIcon    from "@mui/icons-material/Thermostat";
import WarningAmberIcon  from "@mui/icons-material/WarningAmber";
import CloseIcon         from "@mui/icons-material/Close";
import DarkModeIcon        from "@mui/icons-material/DarkMode";
import LightModeIcon       from "@mui/icons-material/LightMode";
import BrightnessAutoIcon  from "@mui/icons-material/BrightnessAuto";
import ChevronLeftIcon   from "@mui/icons-material/ChevronLeft";
import ChevronRightIcon  from "@mui/icons-material/ChevronRight";
import { useNavigate }   from "react-router-dom";
import api               from "../../api/client";
import { useColorMode }  from "../../App";

// ── date helpers ───────────────────────────────────────────────────────────────

function startOfDay(date) {
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  return d;
}

function addDays(date, n) {
  const d = new Date(date);
  d.setDate(d.getDate() + n);
  return d;
}

const DAY_NAMES   = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH_NAMES = ["January","February","March","April","May","June",
                     "July","August","September","October","November","December"];

function formatTime12(date) {
  let h = date.getHours(), m = date.getMinutes();
  const ampm = h >= 12 ? "pm" : "am";
  h = h % 12 || 12;
  return `${h}:${String(m).padStart(2, "0")}${ampm}`;
}

// Compact start-end range, e.g. "9:45 - 11:30am" or "11:45am - 12:15pm" — only
// shown once when both ends share the same am/pm. Spaced around the dash
// (rather than tight) so a narrow event has a clean word-wrap point instead
// of needing to be truncated mid-string.
function formatTimeRange12(start, end) {
  const sAmPm = start.getHours() >= 12 ? "pm" : "am";
  const eAmPm = end.getHours()   >= 12 ? "pm" : "am";
  const s = formatTime12(start).replace(/(am|pm)$/, "");
  const e = formatTime12(end);
  return sAmPm === eAmPm ? `${s} - ${e}` : `${s}${sAmPm} - ${e}`;
}

// Shows the full start-end range, but degrades to just the start time if the
// range doesn't actually fit in the rendered box — measured for real via the
// DOM (CSS alone can't reliably shrink-to-fit down to a few pixels of width,
// which is what a heavily double-booked column can end up with). Never wraps
// or truncates with "…"; nowrap + clipped overflow is the deliberate final
// fallback so at minimum the start time reads cleanly instead of character-
// by-character across multiple lines.
function EventTimeLabel({ start, end, sx }) {
  const ref = useRef(null);
  const [showRange, setShowRange] = useState(true);

  useLayoutEffect(() => { setShowRange(true); }, [start, end]);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !showRange) return;
    if (el.scrollWidth > el.clientWidth + 1) setShowRange(false);
  }, [showRange, start, end]);

  return (
    <Typography ref={ref} sx={{ whiteSpace: "nowrap", overflow: "hidden", ...sx }}>
      {showRange ? formatTimeRange12(start, end) : formatTime12(start)}
    </Typography>
  );
}

// Identifies "the same calendar" for column grouping — a person can have several
// calendars, so name alone isn't enough.
function calendarKey(ev) {
  return `${ev.userName || ""}␟${ev.calendarName || ev.title || ""}`;
}

// Perceived luminance (per ITU-R BT.601) decides whether white or dark text reads
// better on a given event color — light pastel calendar colors need dark text.
function getContrastText(hex) {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return "#fff";
  const full = m[1].length === 3 ? m[1].split("").map(c => c + c).join("") : m[1];
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return luminance > 0.62 ? "rgba(0,0,0,0.87)" : "#fff";
}

// ── time-grid helpers ──────────────────────────────────────────────────────────

const TIME_COL_W  = 36;
const GRID_START  = 8;   // 8 am
const GRID_END    = 21;  // 9 pm
const GRID_HOURS  = GRID_END - GRID_START;

function toGridPct(date) {
  const h = date.getHours() + date.getMinutes() / 60;
  return Math.max(0, Math.min(100, (h - GRID_START) / GRID_HOURS * 100));
}

function hourLabel(h) {
  if (h === 12) return "12pm";
  if (h === 0 || h === 24) return "12am";
  return h > 12 ? `${h - 12}pm` : `${h}am`;
}

// ── calendar view helpers ──────────────────────────────────────────────────────

function getDays(base, view) {
  if (view === "day")   return [base];
  if (view === "week")  return Array.from({ length: 7  }, (_, i) => addDays(base, i));
  if (view === "2week") return Array.from({ length: 14 }, (_, i) => addDays(base, i));
  const first = new Date(base.getFullYear(), base.getMonth(), 1);
  const last  = new Date(base.getFullYear(), base.getMonth() + 1, 0);
  const start = addDays(first, -first.getDay());
  const end   = addDays(last,  6 - last.getDay());
  const days  = [];
  for (let d = start; d.getTime() <= end.getTime(); d = addDays(d, 1)) days.push(d);
  return days;
}

function periodLabel(base, view) {
  if (view === "month") return `${MONTH_NAMES[base.getMonth()]} ${base.getFullYear()}`;
  const days = getDays(base, view);
  if (view === "day") {
    const d = days[0];
    return `${DAY_NAMES[d.getDay()]}, ${MONTH_NAMES[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
  }
  const [f, l] = [days[0], days[days.length - 1]];
  if (f.getFullYear() !== l.getFullYear())
    return `${MONTH_NAMES[f.getMonth()]} ${f.getDate()}, ${f.getFullYear()} – ${MONTH_NAMES[l.getMonth()]} ${l.getDate()}, ${l.getFullYear()}`;
  if (f.getMonth() !== l.getMonth())
    return `${MONTH_NAMES[f.getMonth()]} ${f.getDate()} – ${MONTH_NAMES[l.getMonth()]} ${l.getDate()}, ${f.getFullYear()}`;
  return `${MONTH_NAMES[f.getMonth()]} ${f.getDate()}–${l.getDate()}, ${f.getFullYear()}`;
}

function stepDate(base, view, dir) {
  if (view === "day")   return addDays(base, dir);
  if (view === "week")  return addDays(base, 7 * dir);
  if (view === "2week") return addDays(base, 14 * dir);
  return startOfDay(new Date(base.getFullYear(), base.getMonth() + dir, 1));
}

// ── Clock ──────────────────────────────────────────────────────────────────────

function ClockWidget() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const h    = now.getHours() % 12 || 12;
  const m    = String(now.getMinutes()).padStart(2, "0");
  const ampm = now.getHours() >= 12 ? "PM" : "AM";

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>

      {/* Calendar page icon */}
      <Box sx={{
        width: 52, flexShrink: 0, borderRadius: 1.5, overflow: "hidden",
        border: "1px solid", borderColor: "divider", userSelect: "none",
        boxShadow: 1,
      }}>
        <Box sx={{ bgcolor: "error.main", textAlign: "center", py: 0.3 }}>
          <Typography sx={{ fontSize: "0.6rem", fontWeight: 800, color: "#fff", letterSpacing: "0.12em", textTransform: "uppercase" }}>
            {MONTH_NAMES[now.getMonth()].slice(0, 3)}
          </Typography>
        </Box>
        <Box sx={{ bgcolor: "background.paper", textAlign: "center", pt: 0.25, pb: 0.5 }}>
          <Typography sx={{ fontSize: "1.8rem", fontWeight: 700, lineHeight: 1, color: "text.primary", fontVariantNumeric: "tabular-nums" }}>
            {now.getDate()}
          </Typography>
          <Typography sx={{ fontSize: "0.5rem", fontWeight: 600, color: "text.secondary", textTransform: "uppercase", letterSpacing: "0.08em" }}>
            {DAY_NAMES[now.getDay()]}
          </Typography>
        </Box>
      </Box>

      {/* Time */}
      <Box sx={{ display: "flex", alignItems: "baseline", gap: 0.5 }}>
        <Typography sx={{
          fontSize: { xs: "3.5rem", md: "5rem" },
          fontWeight: 700, lineHeight: 1,
          letterSpacing: "-0.03em", color: "primary.main",
          fontVariantNumeric: "tabular-nums",
        }}>
          {h}:{m}
        </Typography>
        <Typography sx={{ fontSize: "1.4rem", fontWeight: 400, color: "text.secondary", lineHeight: 1 }}>
          {ampm}
        </Typography>
      </Box>

    </Box>
  );
}

// ── Weather ────────────────────────────────────────────────────────────────────

function WeatherWidget() {
  const [current,  setCurrent]  = useState(null);
  const [forecast, setForecast] = useState([]);
  const [loading,  setLoading]  = useState(true);
  const [error,    setError]    = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError(false);
    try {
      const [curRes, fcRes] = await Promise.allSettled([
        api.get("/api/weather/current"),
        api.get("/api/weather/forecast"),
      ]);
      if (curRes.status === "fulfilled") setCurrent(curRes.value.data);
      else setError(true);
      if (fcRes.status === "fulfilled") setForecast(fcRes.value.data.days || []);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 10 * 60 * 1000);
    return () => clearInterval(id);
  }, [load]);

  if (loading) return <Box sx={{ display: "flex", justifyContent: "flex-end", alignItems: "center" }}><CircularProgress size={28} /></Box>;
  if (error || !current) return (
    <Box sx={{ display: "flex", justifyContent: "flex-end", alignItems: "center", color: "text.disabled" }}>
      <WarningAmberIcon fontSize="small" sx={{ mr: 0.5 }} />
      <Typography variant="caption">Weather unavailable</Typography>
    </Box>
  );

  // forecast[0] = today (better H/L than current endpoint), [1-4] = future days
  const todayFc   = forecast[0];
  const futureDays = forecast.slice(1, 5);
  const high = todayFc?.high ?? current.temp_max;
  const low  = todayFc?.low  ?? current.temp_min;

  const dayLabel = (dateStr) => {
    const d = new Date(dateStr + "T12:00:00");
    return DAY_NAMES[d.getDay()];
  };

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1.25 }}>

      {/* Current conditions */}
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
        <img
          src={`https://openweathermap.org/img/wn/${current.icon}@2x.png`}
          alt={current.description}
          style={{ width: 58, height: 58 }}
        />
        <Box>
          <Typography sx={{ fontSize: "2rem", fontWeight: 700, lineHeight: 1, color: "primary.main", fontVariantNumeric: "tabular-nums" }}>
            {current.temp}{current.unit_symbol}
          </Typography>
          <Typography sx={{ fontSize: "0.79rem" }} color="text.secondary" fontWeight={500}>{current.description}</Typography>
          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
            H: {high}{current.unit_symbol} · L: {low}{current.unit_symbol}
          </Typography>
          <Box sx={{ display: "flex", gap: 0.75, mt: 0.25 }}>
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
              <ThermostatIcon sx={{ fontSize: 11, color: "text.disabled" }} />
              <Typography variant="caption" color="text.disabled">Feels {current.feels_like}{current.unit_symbol}</Typography>
            </Box>
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
              <WaterDropIcon sx={{ fontSize: 11, color: "text.disabled" }} />
              <Typography variant="caption" color="text.disabled">{current.humidity}%</Typography>
            </Box>
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
              <AirIcon sx={{ fontSize: 11, color: "text.disabled" }} />
              <Typography variant="caption" color="text.disabled">{current.wind_speed} {current.wind_unit}</Typography>
            </Box>
          </Box>
        </Box>
      </Box>

      {/* 4-day forecast strip */}
      {futureDays.length > 0 && (
        <Box sx={{ display: "flex", gap: 0.5, borderLeft: "1px solid", borderColor: "divider", pl: 1.25 }}>
          {futureDays.map(day => (
            <Box key={day.date} sx={{ display: "flex", flexDirection: "column", alignItems: "center", minWidth: 40 }}>
              <Typography sx={{ fontSize: "0.59rem", fontWeight: 700, color: "text.secondary", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                {dayLabel(day.date)}
              </Typography>
              <img
                src={`https://openweathermap.org/img/wn/${day.icon}@2x.png`}
                alt={day.description}
                style={{ width: 36, height: 36 }}
              />
              <Typography sx={{ fontSize: "0.63rem", fontWeight: 700, lineHeight: 1.2, color: "text.primary", fontVariantNumeric: "tabular-nums" }}>
                {day.high}°
              </Typography>
              <Typography sx={{ fontSize: "0.59rem", color: "text.disabled", fontVariantNumeric: "tabular-nums" }}>
                {day.low}°
              </Typography>
            </Box>
          ))}
        </Box>
      )}

    </Box>
  );
}

// ── News ticker ────────────────────────────────────────────────────────────────

// Round-robin interleave: [A1,B1,C1, A2,B2,C2, ...]  — never two from the same feed in a row
function interleaveGroups(groups) {
  const result = [];
  const max = Math.max(...groups.map(g => g.length));
  for (let i = 0; i < max; i++)
    for (const g of groups)
      if (i < g.length) result.push(g[i]);
  return result;
}

function NewsWidget() {
  const [items,   setItems]   = useState([]);
  const [errors,  setErrors]  = useState([]);
  const [mode,    setMode]    = useState("shuffle");
  const [idx,     setIdx]     = useState(0);
  const [visible, setVisible] = useState(true);

  const groups = useMemo(() => {
    const map = {};
    for (const item of items) {
      const key = item.source || "";
      if (!map[key]) map[key] = [];
      map[key].push(item);
    }
    return Object.values(map).filter(g => g.length > 0);
  }, [items]);

  // Pre-build ordered display list so cycling is always a single index
  const displayItems = useMemo(() => {
    if (groups.length === 0) return items;
    return mode === "shuffle" ? interleaveGroups(groups) : groups.flat();
  }, [groups, mode, items]);

  const load = useCallback(() => {
    api.get("/api/rss/feed")
      .then(res => { setItems(res.data.items || []); setErrors(res.data.errors || []); })
      .catch(e  => setErrors([e?.message || "Could not reach RSS endpoint"]));
  }, []);

  useEffect(() => {
    api.get("/api/settings/rss")
      .then(res => setMode(res.data.mode || "shuffle"))
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 15 * 60 * 1000);
    return () => clearInterval(id);
  }, [load]);

  useEffect(() => { setIdx(0); }, [displayItems]);

  useEffect(() => {
    if (displayItems.length === 0) return;
    const id = setInterval(() => {
      setVisible(false);
      setTimeout(() => { setIdx(i => (i + 1) % displayItems.length); setVisible(true); }, 400);
    }, 7000);
    return () => clearInterval(id);
  }, [displayItems.length]);

  if (items.length === 0) {
    if (errors.length > 0) {
      return (
        <Box sx={{ flexGrow: 1, overflow: "hidden", display: "flex", alignItems: "center", px: 1 }}>
          <Typography noWrap sx={{ fontSize: "0.65rem", fontFamily: "monospace", color: "error.main" }}>
            RSS error: {errors[0]}
          </Typography>
        </Box>
      );
    }
    return <Box sx={{ flexGrow: 1 }} />;
  }

  const item = displayItems[idx] ?? displayItems[0];
  if (!item) return <Box sx={{ flexGrow: 1 }} />;

  return (
    <Box sx={{ flexGrow: 1, overflow: "hidden", display: "flex", alignItems: "flex-start", justifyContent: "center", gap: 1, px: 1 }}>
      {item.source && (
        <Box component="span" sx={{
          flexShrink: 0, whiteSpace: "nowrap", mt: "7px",
          fontSize: "0.45rem", color: "primary.main",
          border: "1px solid currentColor", borderRadius: "3px",
          px: "3px", lineHeight: "12px", opacity: 0.85,
        }}>
          {item.source}
        </Box>
      )}
      <Typography
        onClick={() => item.link && window.open(item.link, "_blank")}
        sx={{
          opacity: visible ? 1 : 0,
          transition: "opacity 0.35s ease",
          fontWeight: 500,
          fontSize: "1.085rem",
          lineHeight: 1.35,
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
          cursor: item.link ? "pointer" : "default",
          "&:hover": item.link ? { textDecoration: "underline" } : {},
        }}
      >
        {item.title}
      </Typography>
    </Box>
  );
}

// ── Overlap layout ────────────────────────────────────────────────────────────

// Greedy interval-graph coloring, scoped to a single list of events (used both
// for the top-level per-calendar grouping and for same-calendar double-bookings).
function greedyColumns(events, colField, totalField) {
  const sorted = [...events].sort((a, b) => a.start - b.start);
  const colEnds = [];
  for (const ev of sorted) {
    let placed = false;
    for (let i = 0; i < colEnds.length; i++) {
      if (colEnds[i] <= ev.start.getTime()) {
        ev[colField] = i;
        colEnds[i] = ev.end.getTime();
        placed = true;
        break;
      }
    }
    if (!placed) {
      ev[colField] = colEnds.length;
      colEnds.push(ev.end.getTime());
    }
  }
  for (const ev of sorted) {
    let maxCol = ev[colField];
    for (const other of sorted) {
      if (other !== ev &&
          ev.start.getTime() < other.end.getTime() &&
          ev.end.getTime() > other.start.getTime()) {
        maxCol = Math.max(maxCol, other[colField]);
      }
    }
    ev[totalField] = maxCol + 1;
  }
  return sorted;
}

// Cap on side-by-side columns within one tier — see assignMacroColumns.
const MAX_COLS = 3;

// Assigns _col/_total/_subCol/_subTotal among a list of same-tier "peer"
// events (either all of a day's root events, or all the children sharing one
// direct container). Must be called per-tier, after containment is resolved
// — a same-calendar double-booking that turns out to be contained inside
// another peer's tier needs its sub-columns computed among only that tier's
// siblings, not that calendar's events for the whole day.
//
// Width is NOT "how many calendars are in this peer group" — a calendar
// whose events never actually overlap another peer gets full width. Only
// calendars with a genuine time conflict share a narrower slot: events are
// grouped into connected components by real cross-calendar time overlap, and
// each component gets its own compact, stably-ordered set of columns.
// Same-calendar double-bookings within a tier still sub-divide via
// greedyColumns.
function assignMacroColumns(peers, calendarOrder) {
  const byCal = new Map();
  for (const ev of peers) {
    const key = calendarKey(ev);
    if (!byCal.has(key)) byCal.set(key, []);
    byCal.get(key).push(ev);
  }
  for (const groupEvents of byCal.values()) {
    greedyColumns(groupEvents, "_subCol", "_subTotal");
  }

  // Connect events that overlap in time AND belong to different calendars
  // (same-calendar overlap is already handled by greedyColumns above).
  const n   = peers.length;
  const adj = Array.from({ length: n }, () => []);
  for (let i = 0; i < n; i++) {
    const a = peers[i];
    for (let j = i + 1; j < n; j++) {
      const b = peers[j];
      if (calendarKey(a) !== calendarKey(b) &&
          a.start.getTime() < b.end.getTime() && a.end.getTime() > b.start.getTime()) {
        adj[i].push(j);
        adj[j].push(i);
      }
    }
  }

  const seen = new Array(n).fill(false);
  for (let i = 0; i < n; i++) {
    if (seen[i]) continue;
    const stack = [i];
    const comp  = [];
    seen[i] = true;
    while (stack.length) {
      const cur = stack.pop();
      comp.push(cur);
      for (const nb of adj[cur]) {
        if (!seen[nb]) { seen[nb] = true; stack.push(nb); }
      }
    }
    const compKeys = [...new Set(comp.map(idx => calendarKey(peers[idx])))].sort((a, b) => {
      const ia = calendarOrder.has(a) ? calendarOrder.get(a) : Infinity;
      const ib = calendarOrder.has(b) ? calendarOrder.get(b) : Infinity;
      return ia !== ib ? ia - ib : a.localeCompare(b);
    });

    if (compKeys.length <= MAX_COLS) {
      const keyToCol = new Map(compKeys.map((k, c) => [k, c]));
      const total    = compKeys.length;
      for (const idx of comp) {
        const ev = peers[idx];
        ev._col   = keyToCol.get(calendarKey(ev));
        ev._total = total;
      }
    } else {
      // More distinct calendars are genuinely conflicting than can each get a
      // readable column — giving every one its own sliver just makes all of
      // them unreadable. Keep the first MAX_COLS-1 (by the stable order) in
      // their own lane; pool everyone else into one shared lane, sub-divided
      // only among themselves by real overlap. Most overflow events won't
      // actually overlap each other, so they mostly get the full shared lane
      // one at a time instead of every calendar splitting evenly.
      const primary   = new Set(compKeys.slice(0, MAX_COLS - 1));
      const keyToCol  = new Map(compKeys.slice(0, MAX_COLS - 1).map((k, c) => [k, c]));
      const overflow  = comp.map(idx => peers[idx]).filter(ev => !primary.has(calendarKey(ev)));
      greedyColumns(overflow, "_subCol", "_subTotal"); // repurposed for shared-lane packing
      for (const idx of comp) {
        const ev  = peers[idx];
        const key = calendarKey(ev);
        ev._col   = primary.has(key) ? keyToCol.get(key) : MAX_COLS - 1;
        ev._total = MAX_COLS;
      }
    }
  }
}

// Container must be at least this many times longer to "span across" a
// shorter event — plain full containment alone is too eager: two
// comparable-length events often share a start or end time by coincidence,
// and those should still read as simultaneous siblings (side by side), not
// one "spanning" the other.
const SPAN_RATIO = 2;

// Fraction of a spanning event's own width permanently reserved, left-aligned,
// for its own time/title — children rendered on top of it are confined to the
// remaining width, so neither event's title is ever covered by the other.
const SPINE_FRACTION = 0.34;

// The tightest OTHER event in dayEvents that genuinely spans across ev, or
// null. This is what distinguishes an all-day-ish "Block" (renders as a
// full-width background, with events inside it layered on top) from a
// same-length overlapping meeting (renders side by side via
// assignMacroColumns instead).
function findContainer(ev, dayEvents) {
  const evDur = ev.end.getTime() - ev.start.getTime();
  let best = null;
  for (const o of dayEvents) {
    if (o === ev) continue;
    if (o.start.getTime() > ev.start.getTime() || o.end.getTime() < ev.end.getTime()) continue;
    if (o.start.getTime() === ev.start.getTime() && o.end.getTime() === ev.end.getTime()) continue;
    if (o.end.getTime() - o.start.getTime() < evDur * SPAN_RATIO) continue;
    if (!best || (o.end.getTime() - o.start.getTime()) < (best.end.getTime() - best.start.getTime())) best = o;
  }
  return best;
}

// Lays out a day's timed events in stable per-calendar columns (ordered by
// calendarOrder) instead of raw greedy packing by start time — so a given
// person/calendar always lands in the same slot relative to whoever it's
// actually double-booked against, instead of shuffling around day to day.
//
// Two different relationships are treated differently, per how people
// actually read a calendar:
//   • Events genuinely at the same time (comparable, overlapping durations)
//     render side by side, split via assignMacroColumns.
//   • An event that spans across (fully contains) a shorter one — e.g. an
//     all-day "Block" with a specific meeting inside it — doesn't fight the
//     shorter event for width. The long event renders as a full-width
//     background layer; events it contains render on top of it, sized and
//     split among only their own siblings (other events with the same
//     direct container). This nests arbitrarily deep via ev._container.
function layoutTimedEvents(events, calendarOrder) {
  for (const ev of events) {
    ev._container = findContainer(ev, events);
  }

  // Flatten to at most one level of nesting: a "child" always attaches
  // directly to its outermost background ancestor, never to another child.
  // Without this, a chain of comparably-sized overlapping events (A spans B
  // spans C spans D...) nests recursively, insetting further at each level
  // until events shrink into unreadable slivers.
  for (const ev of events) {
    let root = ev._container;
    while (root && root._container) root = root._container;
    ev._container = root;
  }

  const childrenOf = new Map();
  for (const ev of events) {
    if (ev._container) {
      const key = ev._container;
      if (!childrenOf.has(key)) childrenOf.set(key, []);
      childrenOf.get(key).push(ev);
    }
  }

  const layoutTier = (peers) => {
    assignMacroColumns(peers, calendarOrder);
    for (const peer of peers) {
      const kids = childrenOf.get(peer);
      if (kids) layoutTier(kids);
    }
  };

  const roots = events.filter(ev => !ev._container);
  layoutTier(roots);
  return events;
}

// ── Calendar grid ──────────────────────────────────────────────────────────────

function CalendarGrid({ view, baseDate }) {
  const [events,       setEvents]       = useState([]);
  const [loading,      setLoading]      = useState(true);
  const [expiredUsers, setExpiredUsers] = useState([]);
  const [now,          setNow]          = useState(() => new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 60000);
    return () => clearInterval(id);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const days = getDays(baseDate, view);
      const res  = await api.get("/api/calendar/events", {
        params: {
          start: days[0].toISOString(),
          end:   addDays(days[days.length - 1], 1).toISOString(),
        },
      });
      const raw = res.data.events || [];
      setExpiredUsers(res.data.expired_users || []);

      const parsed = raw.map(ev => {
        const allDay = ev.allDay;
        const start  = allDay ? new Date(ev.start + "T00:00:00") : new Date(ev.start);
        const end    = allDay ? new Date(ev.end   + "T00:00:00") : new Date(ev.end);
        return { ...ev, start, end };
      });
      const seen = new Set();
      setEvents(parsed.filter(ev => {
        const key = `${ev.title}__${ev.start.getTime()}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      }));
    } catch { /* keep stale */ } finally { setLoading(false); }
  }, [baseDate, view]);

  useEffect(() => {
    load();
    const id = setInterval(load, 5 * 60 * 1000);
    return () => clearInterval(id);
  }, [load]);

  const today        = startOfDay(new Date());
  const days         = getDays(baseDate, view);
  const isTimeGrid   = view === "day" || view === "week";
  const todayInRange = days.some(d => d.getTime() === today.getTime());
  const nowH         = now.getHours() + now.getMinutes() / 60;

  const eventsForDay = (day) => {
    const s = day.getTime(), e = addDays(day, 1).getTime();
    return events
      .filter(ev => ev.start.getTime() < e && ev.end.getTime() > s)
      .sort((a, b) => {
        if (a.allDay && !b.allDay) return -1;
        if (!a.allDay && b.allDay) return 1;
        return a.start - b.start;
      });
  };

  // Stable left-to-right column order for the whole view (not just one day) —
  // this is what keeps each person/calendar in the same lane across the week
  // instead of columns shuffling based on which events happen to start first.
  const calendarOrder = useMemo(() => {
    const keys = new Set();
    for (const ev of events) if (!ev.allDay) keys.add(calendarKey(ev));
    const map = new Map();
    [...keys].sort((a, b) => a.localeCompare(b)).forEach((k, i) => map.set(k, i));
    return map;
  }, [events]);

  return (
    <Box sx={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0, gap: 0.75 }}>

      {expiredUsers.length > 0 && (
        <Chip icon={<WarningAmberIcon fontSize="small" />}
          label={`Token expired: ${expiredUsers.join(", ")}`}
          size="small" color="warning" variant="outlined" sx={{ alignSelf: "flex-start" }} />
      )}

      {loading ? (
        <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", flex: 1 }}>
          <CircularProgress />
        </Box>

      ) : isTimeGrid ? (
        <>
          {/* Day header row */}
          <Box sx={{ display: "flex", flexShrink: 0, gap: 0.75 }}>
            <Box sx={{ width: TIME_COL_W, flexShrink: 0 }} />
            {days.map((day, i) => {
              const isToday = day.getTime() === today.getTime();
              return (
                <Paper key={i} variant="outlined" sx={{
                  flex: 1, p: 0.75, textAlign: "center",
                  bgcolor: isToday ? "primary.50" : "background.paper",
                  borderColor: isToday ? "primary.main" : "divider",
                  borderWidth: isToday ? 2 : 1,
                }}>
                  <Typography variant="caption" fontWeight={600} sx={{
                    display: "block", textTransform: "uppercase", letterSpacing: "0.05em",
                    color: isToday ? "primary.main" : "text.secondary",
                  }}>
                    {DAY_NAMES[day.getDay()]}
                  </Typography>
                  <Typography variant="h6" fontWeight={isToday ? 800 : 500}
                    sx={{ lineHeight: 1.1, color: isToday ? "primary.main" : "text.primary" }}>
                    {day.getDate()}
                  </Typography>
                  {eventsForDay(day).filter(ev => ev.allDay).map(ev => (
                    <Tooltip key={ev.id} title={`${ev.calendarName} · ${ev.userName}`}>
                      <Box sx={{ bgcolor: ev.color, color: getContrastText(ev.color), borderRadius: 0.5, px: 0.5, mt: 0.25, overflow: "hidden" }}>
                        <Typography sx={{ fontSize: "0.6rem", fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                          {ev.title}
                        </Typography>
                      </Box>
                    </Tooltip>
                  ))}
                </Paper>
              );
            })}
          </Box>

          {/* Time grid body */}
          <Box sx={{ flex: 1, display: "flex", minHeight: 0, gap: 0.75 }}>
            {/* Hour labels */}
            <Box sx={{ width: TIME_COL_W, flexShrink: 0, position: "relative" }}>
              {Array.from({ length: GRID_HOURS }, (_, i) => (
                <Typography key={i} sx={{
                  position: "absolute",
                  top: `${(i + 1) / GRID_HOURS * 100}%`,
                  right: 4,
                  transform: "translateY(-50%)",
                  fontSize: "0.6rem",
                  color: "text.disabled",
                  lineHeight: 1,
                  userSelect: "none",
                  whiteSpace: "nowrap",
                }}>
                  {hourLabel(GRID_START + i + 1)}
                </Typography>
              ))}
            </Box>

            {/* Day columns + hour lines + time line */}
            <Box sx={{ flex: 1, display: "flex", position: "relative", gap: 0.75 }}>
              {/* Hour grid lines */}
              {Array.from({ length: GRID_HOURS + 1 }, (_, i) => (
                <Box key={i} sx={{
                  position: "absolute",
                  top: `${i / GRID_HOURS * 100}%`,
                  left: 0, right: 0, height: "1px",
                  bgcolor: "divider", opacity: 0.6,
                  zIndex: 0, pointerEvents: "none",
                }} />
              ))}

              {/* Day columns */}
              {days.map((day, i) => {
                const isToday  = day.getTime() === today.getTime();
                const timedEvs = layoutTimedEvents(eventsForDay(day).filter(ev => !ev.allDay), calendarOrder);
                // Draw background (root) events before whatever spans across them, so
                // contained events always paint on top of their container.
                const depthOf = (ev) => {
                  let n = 0, cur = ev._container;
                  while (cur) { n++; cur = cur._container; }
                  return n;
                };
                const drawOrder = [...timedEvs].sort((a, b) => depthOf(a) - depthOf(b));
                // Events that have at least one other event layered on top of them —
                // their own label is confined to a reserved left "spine" (below) so
                // it can never end up covered by one of those children.
                const hasChildrenSet = new Set(timedEvs.filter(e => e._container).map(e => e._container));
                // Percentage-of-day-column (x, width) for ev, inset within its
                // container's own bounds (recursively) if it spans across another
                // event, or within the day's macro/sub-column slot otherwise.
                const boundsMemo = new Map();
                const calcBounds = (ev) => {
                  if (boundsMemo.has(ev)) return boundsMemo.get(ev);
                  let slotX, slotW;
                  if (!ev._container) {
                    slotX = 0;
                    slotW = 100;
                  } else {
                    const [px, pw] = calcBounds(ev._container);
                    // The container reserves a left spine (SPINE_FRACTION) for its own
                    // time/title — children only ever get the width to the right of it.
                    const spineW = pw * SPINE_FRACTION;
                    const availX = px + spineW;
                    const availW = Math.max(10, pw - spineW);
                    const inset  = availW * 0.07;
                    slotX = availX + inset;
                    slotW = Math.max(availW * 0.3, availW - 2 * inset);
                  }
                  const macroW = slotW / ev._total;
                  const subW   = macroW / ev._subTotal;
                  const result = [slotX + ev._col * macroW + ev._subCol * subW, subW];
                  boundsMemo.set(ev, result);
                  return result;
                };
                return (
                  <Box key={i} sx={{ flex: 1, position: "relative", zIndex: 1 }}>
                    {/* Current time line — scoped to today's column only */}
                    {isToday && nowH >= GRID_START && nowH <= GRID_END && (
                      <Box sx={{
                        position: "absolute",
                        top: `${toGridPct(now)}%`,
                        left: 0, right: 0, height: "2px",
                        bgcolor: "error.main", zIndex: 10, pointerEvents: "none",
                      }}>
                        <Box sx={{
                          position: "absolute", left: -4, top: "50%",
                          transform: "translateY(-50%)",
                          width: 8, height: 8, borderRadius: "50%", bgcolor: "error.main",
                        }} />
                      </Box>
                    )}
                    {drawOrder.map(ev => {
                      const topPct     = toGridPct(ev.start);
                      const clampS     = Math.max(ev.start.getHours() + ev.start.getMinutes() / 60, GRID_START);
                      const clampE     = Math.min(ev.end.getHours()   + ev.end.getMinutes()   / 60, GRID_END);
                      const durationHr = clampE - clampS;
                      const hPct       = Math.max(100 / GRID_HOURS * 0.33, durationHr / GRID_HOURS * 100);
                      const [leftPct, widthPct] = calcBounds(ev);
                      const nested      = !!ev._container;
                      const hasKids     = hasChildrenSet.has(ev);
                      // Duration tiers (not flex-shrink) decide what fits — a box this short
                      // is a fixed, small number of pixels regardless of screen size, so we
                      // pick a fixed number of text lines instead of letting content overflow
                      // and get squeezed/overlapped by the browser.
                      const isCramped  = ev._subTotal > 1 || nested || durationHr < 0.75;
                      const titleLines = durationHr < 0.5 ? 1 : 2;
                      const showCalName = !isCramped && durationHr >= 1.25 && !hasKids;
                      const textColor   = getContrastText(ev.color);
                      return (
                        <Tooltip key={ev.id} title={`${ev.calendarName} · ${ev.userName} · ${formatTime12(ev.start)}–${formatTime12(ev.end)}`} placement="right">
                          <Box sx={{
                            position: "absolute",
                            top:    `${topPct}%`,
                            height: `max(22px, ${hPct.toFixed(2)}%)`,
                            left:   `calc(${leftPct.toFixed(2)}% + 1px)`,
                            width:  `calc(${widthPct.toFixed(2)}% - 2px)`,
                            bgcolor: ev.color, color: textColor,
                            borderRadius: 0.75, px: 0.5, py: 0.25,
                            overflow: "hidden", cursor: "default",
                            zIndex: 2 + (nested ? 1 : 0),
                            // A spanning event (e.g. an all-day "Block") renders as a plain
                            // background; anything it contains renders on top of it, so it
                            // needs a visible border/shadow to read as "in front of" it.
                            ...(nested ? {
                              border: "2px solid #fff",
                              boxShadow: "1px 2px 5px rgba(0,0,0,0.35)",
                            } : {}),
                          }}>
                            {/* Time range + title are one label block, time first — never a
                                separate element pinned to the bottom of the box, so they always
                                read together no matter how tall the event is.
                                A container's own label is confined to a reserved left "spine"
                                (matching the width calcBounds carves out for it above) so it can
                                never end up covered by a child rendered on top of it. */}
                            <Box sx={hasKids ? { width: `${SPINE_FRACTION * 100}%`, overflow: "hidden" } : undefined}>
                              <EventTimeLabel start={ev.start} end={ev.end} sx={{
                                fontSize: "0.58rem", fontWeight: 700, lineHeight: 1.2, opacity: 0.85,
                              }} />
                              <Typography sx={{
                                fontSize: "0.65rem", fontWeight: 600, lineHeight: 1.2,
                                overflow: "hidden", display: "-webkit-box",
                                WebkitLineClamp: titleLines, WebkitBoxOrient: "vertical",
                                wordBreak: "break-word",
                              }}>
                                {ev.title}
                              </Typography>
                              {showCalName && (
                                <Typography sx={{ fontSize: "0.5rem", opacity: 0.8, overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis" }}>
                                  {ev.calendarName}
                                </Typography>
                              )}
                            </Box>
                          </Box>
                        </Tooltip>
                      );
                    })}
                  </Box>
                );
              })}

            </Box>
          </Box>
        </>

      ) : (
        /* Card grid — 2week / month */
        <Box sx={{
          display: "grid",
          gridTemplateColumns: "repeat(7, 1fr)",
          gridTemplateRows: `repeat(${Math.ceil(days.length / 7)}, 1fr)`,
          gap: 0.75, flex: 1, minHeight: 0,
        }}>
          {days.map((day, i) => {
            const isToday      = day.getTime() === today.getTime();
            const isOtherMonth = view === "month" && day.getMonth() !== baseDate.getMonth();
            const dayEvents    = eventsForDay(day);
            return (
              <Paper key={i} variant="outlined" sx={{
                p: 1, display: "flex", flexDirection: "column", gap: 0.5, overflow: "hidden",
                bgcolor:     isToday ? "primary.50" : isOtherMonth ? "action.hover" : "background.paper",
                borderColor: isToday ? "primary.main" : "divider",
                borderWidth: isToday ? 2 : 1,
                opacity:     isOtherMonth ? 0.55 : 1,
              }}>
                <Box sx={{ textAlign: "center", pb: 0.5, borderBottom: "1px solid", borderColor: "divider" }}>
                  <Typography variant="caption" fontWeight={600} sx={{
                    color: isToday ? "primary.main" : "text.secondary",
                    textTransform: "uppercase", letterSpacing: "0.05em",
                  }}>
                    {DAY_NAMES[day.getDay()]}
                  </Typography>
                  <Typography variant="h6" fontWeight={isToday ? 800 : 500} sx={{
                    lineHeight: 1.1,
                    color: isToday ? "primary.main" : isOtherMonth ? "text.disabled" : "text.primary",
                  }}>
                    {day.getDate()}
                  </Typography>
                </Box>
                <Box sx={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column", gap: 0.4 }}>
                  {dayEvents.length === 0 ? (
                    <Typography variant="caption" color="text.disabled" sx={{ textAlign: "center", mt: 1 }}>—</Typography>
                  ) : dayEvents.map(ev => (
                    <Tooltip key={ev.id} title={`${ev.calendarName} · ${ev.userName}${ev.allDay ? "" : ` · ${formatTime12(ev.start)}`}`} placement="top">
                      <Box sx={{
                        bgcolor: ev.color, color: getContrastText(ev.color),
                        borderRadius: 0.75, px: 0.75, py: 0.25,
                        cursor: "default", overflow: "hidden",
                      }}>
                        <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 0.5 }}>
                          {!ev.allDay
                            ? <Typography sx={{ fontSize: "0.6rem", lineHeight: 1.2, fontWeight: 600, opacity: 0.9, flexShrink: 0 }}>{formatTime12(ev.start)}</Typography>
                            : <span />}
                          <Typography sx={{ fontSize: "0.55rem", lineHeight: 1.2, opacity: 0.8, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "60%" }}>
                            {ev.calendarName}
                          </Typography>
                        </Box>
                        <Typography sx={{
                          fontSize: "0.7rem", fontWeight: 600, lineHeight: 1.3,
                          display: "-webkit-box", WebkitLineClamp: 2,
                          WebkitBoxOrient: "vertical", overflow: "hidden",
                        }}>
                          {ev.title}
                        </Typography>
                      </Box>
                    </Tooltip>
                  ))}
                </Box>
              </Paper>
            );
          })}
        </Box>
      )}
    </Box>
  );
}

// ── Footer ─────────────────────────────────────────────────────────────────────

function Footer({ view, baseDate, setView, setBaseDate }) {
  const { toggle, mode } = useColorMode();
  const [info, setInfo]  = useState(null);

  useEffect(() => {
    // One-time fetch for IP / FQDN + initial CPU/RAM snapshot.
    api.get("/api/system").then(res => setInfo(res.data)).catch(() => {});

    // SSE stream pushes CPU/RAM every 500 ms — no polling overhead.
    const es = new EventSource("/api/system/live");
    es.onmessage = e => {
      try {
        const d = JSON.parse(e.data);
        setInfo(prev => prev ? { ...prev, ...d } : d);
      } catch {}
    };
    return () => es.close();
  }, []);

  return (
    <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", px: 1, flexShrink: 0, gap: 1 }}>

      {/* Left: FQDN (ip) */}
      <Box sx={{ flex: 1 }}>
        {info?.fqdn && (
          <Typography variant="caption" color="text.disabled" sx={{ fontFamily: "monospace" }}>
            {info.fqdn}{info?.ip ? ` (${info.ip})` : ""}
          </Typography>
        )}
      </Box>

      {/* Center: view selector + navigation */}
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
        <ToggleButtonGroup
          size="small" value={view} exclusive
          onChange={(_, v) => v && setView(v)}
          sx={{ "& .MuiToggleButton-root": { py: 0.25, px: 1, fontSize: "0.7rem" } }}
        >
          <ToggleButton value="day">Day</ToggleButton>
          <ToggleButton value="week">Week</ToggleButton>
          <ToggleButton value="2week">2 Wk</ToggleButton>
          <ToggleButton value="month">Month</ToggleButton>
        </ToggleButtonGroup>

        <IconButton size="small" onClick={() => setBaseDate(d => stepDate(d, view, -1))}>
          <ChevronLeftIcon fontSize="small" />
        </IconButton>
        <Typography variant="body2" fontWeight={600}
          sx={{ minWidth: 200, textAlign: "center", fontSize: "0.8rem" }}>
          {periodLabel(baseDate, view)}
        </Typography>
        <IconButton size="small" onClick={() => setBaseDate(d => stepDate(d, view, 1))}>
          <ChevronRightIcon fontSize="small" />
        </IconButton>

        <Button size="small" variant="text"
          sx={{ py: 0.25, px: 1, fontSize: "0.7rem", minWidth: 0, color: "text.secondary" }}
          onClick={() => setBaseDate(startOfDay(new Date()))}>
          Today
        </Button>
      </Box>

      {/* Right: CPU/RAM + theme toggle */}
      <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, flex: 1, justifyContent: "flex-end" }}>
        {info?.cpu != null && (
          <Typography variant="caption" sx={{ fontFamily: "monospace", color: info.cpu >= 80 ? "error.main" : info.cpu >= 50 ? "warning.main" : "text.disabled" }}>
            CPU {info.cpu}%
          </Typography>
        )}
        {info?.ram != null && (
          <Typography variant="caption" sx={{ fontFamily: "monospace", color: info.ram.pct >= 85 ? "error.main" : info.ram.pct >= 65 ? "warning.main" : "text.disabled" }}>
            RAM {info.ram.used}/{info.ram.total} MB
          </Typography>
        )}
        <Tooltip title={mode === "light" ? "Switch to dark" : mode === "dark" ? "Switch to auto (sunrise/sunset)" : "Switch to light"}>
          <IconButton size="small" onClick={toggle} sx={{ color: "text.disabled" }}>
            {mode === "light" ? <DarkModeIcon fontSize="small" /> : mode === "dark" ? <BrightnessAutoIcon fontSize="small" /> : <LightModeIcon fontSize="small" />}
          </IconButton>
        </Tooltip>
      </Box>
    </Box>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const navigate                        = useNavigate();
  const [view,          setView]        = useState("week");
  const [baseDate,      setBaseDate]    = useState(() => startOfDay(new Date()));
  const [oauthBanner,   setOauthBanner] = useState(false);

  // Show a banner the moment we detect OAuth credentials are missing.
  useEffect(() => {
    api.get("/api/settings/oauth")
      .then(res => { if (!res.data.configured) setOauthBanner(true); })
      .catch(() => {});
  }, []);

  return (
    <Box sx={{
      height: "100vh", display: "flex", flexDirection: "column",
      bgcolor: "background.default", p: 1.5, gap: 1.5, overflow: "hidden",
    }}>
      {/* Top bar — grid so RSS column cannot affect clock or weather columns */}
      <Paper variant="outlined" sx={{
        px: 2, height: 96, flexShrink: 0, overflow: "hidden",
        display: "grid",
        gridTemplateColumns: "auto 1fr auto auto",
        alignItems: "center",
        gap: 2,
      }}>
        <ClockWidget />
        <NewsWidget />
        <WeatherWidget />
        <Tooltip title="Settings">
          <IconButton size="small" onClick={() => navigate("/settings")}>
            <SettingsIcon />
          </IconButton>
        </Tooltip>
      </Paper>

      <CalendarGrid view={view} baseDate={baseDate} />
      <Footer view={view} baseDate={baseDate} setView={setView} setBaseDate={setBaseDate} />

      {/* OAuth not-configured banner */}
      <Snackbar
        open={oauthBanner}
        anchorOrigin={{ vertical: "top", horizontal: "center" }}
        sx={{ top: { xs: 8, sm: 16 }, zIndex: 1400 }}
      >
        <Alert
          severity="warning"
          variant="filled"
          icon={<WarningAmberIcon fontSize="inherit" />}
          action={
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
              <Button
                color="inherit"
                size="small"
                onClick={() => { setOauthBanner(false); navigate("/settings"); }}
                sx={{ fontWeight: 700, whiteSpace: "nowrap" }}
              >
                Set Up Now
              </Button>
              <IconButton
                color="inherit"
                size="small"
                onClick={() => setOauthBanner(false)}
                aria-label="dismiss"
              >
                <CloseIcon fontSize="small" />
              </IconButton>
            </Box>
          }
          sx={{ width: "100%", alignItems: "center", "& .MuiAlert-action": { alignItems: "center", pt: 0 } }}
        >
          Google sign-in is not configured — family members can&rsquo;t sign in until OAuth
          credentials are added.
        </Alert>
      </Snackbar>
    </Box>
  );
}
