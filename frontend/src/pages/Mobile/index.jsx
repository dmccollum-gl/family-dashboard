import { useState, useEffect, useCallback, useRef } from "react";
import { Box, Typography, Paper, Chip, IconButton, Button } from "@mui/material";
import ChevronLeftIcon  from "@mui/icons-material/ChevronLeft";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import WaterDropIcon    from "@mui/icons-material/WaterDrop";
import AirIcon          from "@mui/icons-material/Air";
import api from "../../api/client";

// ── helpers ────────────────────────────────────────────────────────────────────

const DAY_NAMES   = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
const MONTH_NAMES = ["January","February","March","April","May","June",
                     "July","August","September","October","November","December"];

function startOfDay(d) { const r = new Date(d); r.setHours(0,0,0,0); return r; }
function addDays(d, n)  { const r = new Date(d); r.setDate(r.getDate() + n); return r; }

// Shared in-memory event cache — keyed by "YYYY-MM-DD/YYYY-MM-DD", survives day navigation
const eventCache = new Map();
function cacheKey(start, end) {
  return `${start.toISOString().slice(0,10)}/${end.toISOString().slice(0,10)}`;
}
function parseEvents(raw) {
  return (raw || []).map(ev => ({
    ...ev,
    start: ev.allDay ? new Date(ev.start + "T00:00:00") : new Date(ev.start),
    end:   ev.allDay ? new Date(ev.end   + "T00:00:00") : new Date(ev.end),
  }));
}
async function fetchEvents(start, end) {
  const res = await api.get("/api/calendar/events", {
    params: { start: start.toISOString(), end: end.toISOString() },
  });
  return parseEvents(res.data.events);
}
function prefetch(start, end) {
  const k = cacheKey(start, end);
  if (eventCache.has(k)) return;
  fetchEvents(start, end).then(evs => eventCache.set(k, evs)).catch(() => {});
}

function formatTime12(date) {
  let h = date.getHours(), m = date.getMinutes();
  const ampm = h >= 12 ? "pm" : "am";
  h = h % 12 || 12;
  return m === 0 ? `${h}${ampm}` : `${h}:${String(m).padStart(2,"0")}${ampm}`;
}

const GRID_START = 7;
const GRID_END   = 22;
const GRID_HOURS = GRID_END - GRID_START;

function toGridPct(date) {
  const h = date.getHours() + date.getMinutes() / 60;
  return Math.max(0, Math.min(100, (h - GRID_START) / GRID_HOURS * 100));
}

function hourLabel(h) {
  if (h === 12) return "12p";
  return h > 12 ? `${h-12}p` : `${h}a`;
}

function layoutTimedEvents(events) {
  const sorted = [...events].sort((a, b) => a.start - b.start);
  const colEnds = [];
  for (const ev of sorted) {
    let placed = false;
    for (let c = 0; c < colEnds.length; c++) {
      if (ev.start.getTime() >= colEnds[c]) { ev._col = c; colEnds[c] = ev.end.getTime(); placed = true; break; }
    }
    if (!placed) { ev._col = colEnds.length; colEnds.push(ev.end.getTime()); }
  }
  for (const ev of sorted) {
    let maxCol = ev._col;
    for (const o of sorted)
      if (o !== ev && ev.start < o.end && ev.end > o.start) maxCol = Math.max(maxCol, o._col);
    ev._total = maxCol + 1;
  }
  return sorted;
}

// ── hooks ──────────────────────────────────────────────────────────────────────

function useOrientation() {
  const [landscape, setLandscape] = useState(
    () => window.matchMedia("(orientation: landscape)").matches
  );
  useEffect(() => {
    const mq = window.matchMedia("(orientation: landscape)");
    const handler = e => setLandscape(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);
  return landscape;
}

// Returns touch event handlers that fire onSwipeLeft / onSwipeRight
// only when the horizontal movement clearly dominates (avoids eating vertical scrolls).
function useSwipe(onSwipeLeft, onSwipeRight) {
  const start = useRef(null);

  const onTouchStart = useCallback(e => {
    start.current = { x: e.touches[0].clientX, y: e.touches[0].clientY };
  }, []);

  const onTouchEnd = useCallback(e => {
    if (!start.current) return;
    const dx = e.changedTouches[0].clientX - start.current.x;
    const dy = e.changedTouches[0].clientY - start.current.y;
    start.current = null;
    if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
    dx < 0 ? onSwipeLeft() : onSwipeRight();
  }, [onSwipeLeft, onSwipeRight]);

  return { onTouchStart, onTouchEnd };
}

// ── weather ────────────────────────────────────────────────────────────────────

function MobileWeather() {
  const [current,  setCurrent]  = useState(null);
  const [forecast, setForecast] = useState([]);

  useEffect(() => {
    const load = () => Promise.allSettled([
      api.get("/api/weather/current"),
      api.get("/api/weather/forecast"),
    ]).then(([cur, fc]) => {
      if (cur.status === "fulfilled") setCurrent(cur.value.data);
      if (fc.status  === "fulfilled") setForecast(fc.value.data.days || []);
    });
    load();
    const id = setInterval(load, 10 * 60 * 1000);
    return () => clearInterval(id);
  }, []);

  if (!current) return null;
  const high = forecast[0]?.high ?? current.temp_max;
  const low  = forecast[0]?.low  ?? current.temp_min;

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
      <img src={`https://openweathermap.org/img/wn/${current.icon}.png`}
        alt={current.description} style={{ width: 28, height: 28 }} />
      <Box>
        <Typography sx={{ fontSize: "1.05rem", fontWeight: 700, lineHeight: 1, color: "primary.main" }}>
          {current.temp}{current.unit_symbol}
        </Typography>
        <Typography sx={{ fontSize: "0.6rem" }} color="text.secondary">H:{high}° L:{low}°</Typography>
      </Box>
      <Box sx={{ display: "flex", flexDirection: "column" }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
          <WaterDropIcon sx={{ fontSize: 9, color: "text.disabled" }} />
          <Typography sx={{ fontSize: "0.55rem" }} color="text.disabled">{current.humidity}%</Typography>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
          <AirIcon sx={{ fontSize: 9, color: "text.disabled" }} />
          <Typography sx={{ fontSize: "0.55rem" }} color="text.disabled">{current.wind_speed} {current.wind_unit}</Typography>
        </Box>
      </Box>
    </Box>
  );
}

// ── shared time grid ───────────────────────────────────────────────────────────

// Renders the scrollable hour-ruled grid for one or more day columns.
// `days` array + `eventsByDay` map keep this reusable for both portrait/landscape.
function TimeGrid({ days, eventsByDay, now, scrollRef }) {
  const today  = startOfDay(new Date());
  const nowH   = now.getHours() + now.getMinutes() / 60;
  const isToday = d => d.getTime() === today.getTime();

  return (
    <Box ref={scrollRef} sx={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
    <Box sx={{ minHeight: `${GRID_HOURS * 60}px`, display: "flex" }}>

      {/* Hour labels */}
      <Box sx={{ width: 32, flexShrink: 0, position: "relative", userSelect: "none" }}>
        {Array.from({ length: GRID_HOURS }, (_, i) => (
          <Box key={i} sx={{ position: "absolute", top: `${(i + 1) / GRID_HOURS * 100}%`, right: 3, transform: "translateY(-50%)" }}>
            <Typography sx={{ fontSize: "0.55rem", color: "text.disabled", whiteSpace: "nowrap" }}>
              {hourLabel(GRID_START + i + 1)}
            </Typography>
          </Box>
        ))}
      </Box>

      {/* Columns */}
      <Box sx={{ flex: 1, display: "flex", gap: "1px", position: "relative" }}>

        {/* Hour lines (behind all columns) */}
        {Array.from({ length: GRID_HOURS + 1 }, (_, i) => (
          <Box key={i} sx={{
            position: "absolute", top: `${i / GRID_HOURS * 100}%`,
            left: 0, right: 0, height: "1px", bgcolor: "divider", opacity: 0.5, pointerEvents: "none",
          }} />
        ))}

        {days.map((day, di) => {
          const timed = layoutTimedEvents((eventsByDay[day.getTime()] || []).filter(e => !e.allDay));
          return (
            <Box key={di} sx={{ flex: 1, position: "relative" }}>
              {/* Current time line — only in today's column */}
              {isToday(day) && nowH >= GRID_START && nowH <= GRID_END && (
                <Box sx={{ position: "absolute", top: `${toGridPct(now)}%`, left: 0, right: 0, height: "2px", bgcolor: "error.main", zIndex: 10 }}>
                  <Box sx={{ position: "absolute", left: -3, top: "50%", transform: "translateY(-50%)", width: 6, height: 6, borderRadius: "50%", bgcolor: "error.main" }} />
                </Box>
              )}

              {timed.map(ev => {
                const clampS   = Math.max(ev.start.getHours() + ev.start.getMinutes() / 60, GRID_START);
                const clampE   = Math.min(ev.end.getHours()   + ev.end.getMinutes()   / 60, GRID_END);
                const hPct     = Math.max(100 / GRID_HOURS * 0.4, (clampE - clampS) / GRID_HOURS * 100);
                const leftPct  = (ev._col  / ev._total) * 100;
                const widthPct = (1        / ev._total) * 100;
                return (
                  <Box key={ev.id} sx={{
                    position: "absolute",
                    top:    `${toGridPct(ev.start)}%`,
                    height: `max(24px, ${hPct.toFixed(2)}%)`,
                    left:   `calc(${leftPct.toFixed(1)}% + 1px)`,
                    width:  `calc(${widthPct.toFixed(1)}% - 2px)`,
                    bgcolor: ev.color, color: "#fff",
                    borderRadius: 0.5, px: 0.5, py: 0.15,
                    overflow: "hidden", zIndex: 2,
                  }}>
                    <Typography sx={{ fontSize: "0.55rem", fontWeight: 700, lineHeight: 1.2, opacity: 0.9 }}>
                      {formatTime12(ev.start)}
                    </Typography>
                    <Typography sx={{
                      fontSize: "0.6rem", fontWeight: 600, lineHeight: 1.2,
                      overflow: "hidden", display: "-webkit-box",
                      WebkitLineClamp: 3, WebkitBoxOrient: "vertical",
                    }}>
                      {ev.title}
                    </Typography>
                  </Box>
                );
              })}
            </Box>
          );
        })}
      </Box>
    </Box>
    </Box>
  );
}

// ── day view (portrait) ────────────────────────────────────────────────────────

function DayView({ day }) {
  const end = addDays(day, 1);
  const key = cacheKey(day, end);

  const [events,  setEvents]  = useState(() => eventCache.get(key) || []);
  const [loading, setLoading] = useState(!eventCache.has(key));
  const [now,     setNow]     = useState(() => new Date());
  const gridRef   = useRef(null);
  const scrolled  = useRef(false);

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 60000);
    return () => clearInterval(id);
  }, []);

  const load = useCallback(async () => {
    try {
      const evs = await fetchEvents(day, end);
      eventCache.set(key, evs);
      setEvents(evs);
    } catch {}
    finally { setLoading(false); }
  }, [key]);

  useEffect(() => {
    scrolled.current = false;
    if (!eventCache.has(key)) setLoading(true);
    else setEvents(eventCache.get(key));
    load();
    const id = setInterval(load, 5 * 60 * 1000);
    return () => clearInterval(id);
  }, [load]);

  // Prefetch adjacent days after current load
  useEffect(() => {
    if (loading) return;
    prefetch(addDays(day, 1), addDays(day, 2));
    prefetch(addDays(day, -1), day);
  }, [loading, day]);

  // Scroll to current time once per day change
  useEffect(() => {
    if (loading || scrolled.current || !gridRef.current) return;
    scrolled.current = true;
    const pct = (now.getHours() + now.getMinutes() / 60 - GRID_START) / GRID_HOURS;
    const el  = gridRef.current;
    el.scrollTop = Math.max(0, pct * el.scrollHeight - el.clientHeight / 2);
  }, [loading]);

  const allDay   = events.filter(e => e.allDay);
  const eventMap = { [day.getTime()]: events };

  return (
    <Box sx={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
      {allDay.length > 0 && (
        <Box sx={{ px: 1, py: 0.5, display: "flex", flexWrap: "wrap", gap: 0.5, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
          {allDay.map(ev => (
            <Chip key={ev.id} label={ev.title} size="small"
              sx={{ bgcolor: ev.color, color: "#fff", fontSize: "0.7rem", height: 22 }} />
          ))}
        </Box>
      )}
      <TimeGrid days={[day]} eventsByDay={eventMap} now={now} scrollRef={gridRef} />
    </Box>
  );
}

// ── week view (landscape) ──────────────────────────────────────────────────────

function WeekView({ day }) {
  const days  = Array.from({ length: 5 }, (_, i) => addDays(day, i));
  const end   = addDays(day, 5);
  const key   = cacheKey(day, end);

  const [events,  setEvents]  = useState(() => eventCache.get(key) || []);
  const [loading, setLoading] = useState(!eventCache.has(key));
  const [now,     setNow]     = useState(() => new Date());
  const gridRef  = useRef(null);
  const scrolled = useRef(false);
  const today    = startOfDay(new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 60000);
    return () => clearInterval(id);
  }, []);

  const load = useCallback(async () => {
    try {
      const evs = await fetchEvents(day, end);
      eventCache.set(key, evs);
      setEvents(evs);
    } catch {}
    finally { setLoading(false); }
  }, [key]);

  useEffect(() => {
    scrolled.current = false;
    if (!eventCache.has(key)) setLoading(true);
    else setEvents(eventCache.get(key));
    load();
    const id = setInterval(load, 5 * 60 * 1000);
    return () => clearInterval(id);
  }, [load]);

  // Prefetch adjacent 5-day windows
  useEffect(() => {
    if (loading) return;
    prefetch(addDays(day, 5), addDays(day, 10));
    prefetch(addDays(day, -5), day);
  }, [loading, day]);

  useEffect(() => {
    if (loading || scrolled.current || !gridRef.current) return;
    scrolled.current = true;
    const pct = (now.getHours() + now.getMinutes() / 60 - GRID_START) / GRID_HOURS;
    const el  = gridRef.current;
    el.scrollTop = Math.max(0, pct * el.scrollHeight - el.clientHeight / 2);
  }, [loading]);

  // Group events by day
  const eventsByDay = {};
  for (const d of days) eventsByDay[d.getTime()] = [];
  for (const ev of events) {
    for (const d of days) {
      const s = d.getTime(), e = addDays(d, 1).getTime();
      if (ev.start.getTime() < e && ev.end.getTime() > s)
        eventsByDay[d.getTime()].push(ev);
    }
  }

  const allDayByDay = days.map(d => (eventsByDay[d.getTime()] || []).filter(e => e.allDay));
  const hasAllDay   = allDayByDay.some(a => a.length > 0);

  return (
    <Box sx={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>

      {/* Day headers */}
      <Box sx={{ display: "flex", flexShrink: 0, borderBottom: "1px solid", borderColor: "divider" }}>
        <Box sx={{ width: 32, flexShrink: 0 }} />
        {days.map((d, i) => {
          const isToday = d.getTime() === today.getTime();
          return (
            <Box key={i} sx={{ flex: 1, textAlign: "center", py: 0.25 }}>
              <Typography sx={{ fontSize: "0.55rem", color: "text.secondary", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                {DAY_NAMES[d.getDay()]}
              </Typography>
              <Typography sx={{
                fontSize: "0.9rem", fontWeight: 700, lineHeight: 1,
                color: isToday ? "error.main" : "text.primary",
                bgcolor: isToday ? "error.light" : "transparent",
                borderRadius: "50%", width: 22, height: 22,
                display: "flex", alignItems: "center", justifyContent: "center",
                mx: "auto", opacity: isToday ? 1 : 0.87,
              }}>
                {d.getDate()}
              </Typography>
            </Box>
          );
        })}
      </Box>

      {/* All-day row */}
      {hasAllDay && (
        <Box sx={{ display: "flex", flexShrink: 0, borderBottom: "1px solid", borderColor: "divider", py: 0.25 }}>
          <Box sx={{ width: 32, flexShrink: 0 }} />
          {allDayByDay.map((evs, i) => (
            <Box key={i} sx={{ flex: 1, px: 0.25, display: "flex", flexDirection: "column", gap: 0.25 }}>
              {evs.map(ev => (
                <Box key={ev.id} sx={{ bgcolor: ev.color, color: "#fff", borderRadius: 0.5, px: 0.5, fontSize: "0.55rem", fontWeight: 600, overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis" }}>
                  {ev.title}
                </Box>
              ))}
            </Box>
          ))}
        </Box>
      )}

      <TimeGrid days={days} eventsByDay={eventsByDay} now={now} scrollRef={gridRef} />
    </Box>
  );
}

// ── page ───────────────────────────────────────────────────────────────────────

export default function Mobile() {
  const today      = startOfDay(new Date());
  const [day, setDay] = useState(today);
  const isLandscape   = useOrientation();

  const goBack    = useCallback(() => setDay(d => addDays(d, isLandscape ? -5 : -1)), [isLandscape]);
  const goForward = useCallback(() => setDay(d => addDays(d, isLandscape ?  5 :  1)), [isLandscape]);
  const swipe     = useSwipe(goForward, goBack);

  const isToday   = day.getTime() === today.getTime();

  const rangeEnd   = addDays(day, 4);
  const sameMonth  = day.getMonth() === rangeEnd.getMonth();
  const weekLabel  = sameMonth
    ? `${MONTH_NAMES[day.getMonth()]} ${day.getDate()}–${rangeEnd.getDate()}`
    : `${MONTH_NAMES[day.getMonth()].slice(0,3)} ${day.getDate()} – ${MONTH_NAMES[rangeEnd.getMonth()].slice(0,3)} ${rangeEnd.getDate()}`;

  const dayLabel = `${DAY_NAMES[day.getDay()]}, ${MONTH_NAMES[day.getMonth()]} ${day.getDate()}`;

  return (
    <Box
      {...swipe}
      sx={{ height: "100dvh", display: "flex", flexDirection: "column", bgcolor: "background.default", overflow: "hidden" }}
    >
      {/* Header */}
      <Paper variant="outlined" sx={{
        px: 2, py: 0.5, flexShrink: 0, borderRadius: 0,
        display: "flex", alignItems: "center", justifyContent: "space-between", gap: 1,
      }}>
        <Typography sx={{
          fontSize: "0.75rem", fontWeight: 600, lineHeight: 1,
          color: isToday && !isLandscape ? "primary.main" : "text.primary",
        }}>
          {isLandscape ? weekLabel : dayLabel}
        </Typography>
        <MobileWeather />
      </Paper>

      {/* Calendar */}
      {isLandscape ? <WeekView day={day} /> : <DayView day={day} />}

      {/* Bottom navigation */}
      <Paper variant="outlined" sx={{
        px: 2, py: 0.5, flexShrink: 0, borderRadius: 0,
        display: "flex", alignItems: "center", justifyContent: "space-between",
      }}>
        <IconButton onClick={goBack} size="small">
          <ChevronLeftIcon />
        </IconButton>
        <Button
          variant={isToday ? "contained" : "outlined"}
          size="small"
          onClick={() => setDay(today)}
          disabled={isToday && !isLandscape}
          sx={{ minWidth: 80, fontSize: "0.75rem" }}
        >
          Today
        </Button>
        <IconButton onClick={goForward} size="small">
          <ChevronRightIcon />
        </IconButton>
      </Paper>
    </Box>
  );
}
