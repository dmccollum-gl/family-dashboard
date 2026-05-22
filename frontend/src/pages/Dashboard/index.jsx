import { useState, useEffect, useCallback } from "react";
import {
  Box, Typography, Paper, Chip, IconButton, CircularProgress,
  Tooltip, ToggleButtonGroup, ToggleButton, Button,
} from "@mui/material";
import SettingsIcon      from "@mui/icons-material/Settings";
import WaterDropIcon     from "@mui/icons-material/WaterDrop";
import AirIcon           from "@mui/icons-material/Air";
import ThermostatIcon    from "@mui/icons-material/Thermostat";
import WarningAmberIcon  from "@mui/icons-material/WarningAmber";
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

function NewsWidget() {
  const [items,   setItems]   = useState([]);
  const [errors,  setErrors]  = useState([]);
  const [idx,     setIdx]     = useState(0);
  const [visible, setVisible] = useState(true);

  const load = useCallback(() => {
    api.get("/api/rss/feed")
      .then(res => { setItems(res.data.items || []); setErrors(res.data.errors || []); })
      .catch(e  => setErrors([e?.message || "Could not reach RSS endpoint"]));
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 15 * 60 * 1000);
    return () => clearInterval(id);
  }, [load]);

  useEffect(() => {
    if (items.length === 0) return;
    const id = setInterval(() => {
      setVisible(false);
      setTimeout(() => { setIdx(i => (i + 1) % items.length); setVisible(true); }, 400);
    }, 7000);
    return () => clearInterval(id);
  }, [items.length]);

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

  const item = items[idx];
  return (
    <Box sx={{ flexGrow: 1, overflow: "hidden", display: "flex", alignItems: "flex-start", justifyContent: "center", gap: 1, px: 1 }}>
      {item.source && (
        <Chip label={item.source} size="small" color="primary" variant="outlined"
          sx={{ flexShrink: 0, fontSize: "0.7rem", height: 20, mt: "4px" }} />
      )}
      <Typography
        onClick={() => item.link && window.open(item.link, "_blank")}
        sx={{
          opacity: visible ? 1 : 0,
          transition: "opacity 0.35s ease",
          fontWeight: 500,
          fontSize: "1.55rem",
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

function layoutTimedEvents(events) {
  const sorted = [...events].sort((a, b) => a.start - b.start);
  const colEnds = [];
  for (const ev of sorted) {
    let placed = false;
    for (let i = 0; i < colEnds.length; i++) {
      if (colEnds[i] <= ev.start.getTime()) {
        ev._col = i;
        colEnds[i] = ev.end.getTime();
        placed = true;
        break;
      }
    }
    if (!placed) {
      ev._col = colEnds.length;
      colEnds.push(ev.end.getTime());
    }
  }
  for (const ev of sorted) {
    let maxCol = ev._col;
    for (const other of sorted) {
      if (other !== ev &&
          ev.start.getTime() < other.end.getTime() &&
          ev.end.getTime() > other.start.getTime()) {
        maxCol = Math.max(maxCol, other._col);
      }
    }
    ev._total = maxCol + 1;
  }
  return sorted;
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
                      <Box sx={{ bgcolor: ev.color, color: "#fff", borderRadius: 0.5, px: 0.5, mt: 0.25, overflow: "hidden" }}>
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
                const timedEvs = layoutTimedEvents(eventsForDay(day).filter(ev => !ev.allDay));
                return (
                  <Box key={i} sx={{ flex: 1, position: "relative", zIndex: 1 }}>
                    {timedEvs.map(ev => {
                      const topPct   = toGridPct(ev.start);
                      const clampS   = Math.max(ev.start.getHours() + ev.start.getMinutes() / 60, GRID_START);
                      const clampE   = Math.min(ev.end.getHours()   + ev.end.getMinutes()   / 60, GRID_END);
                      const hPct     = Math.max(100 / GRID_HOURS * 0.33, (clampE - clampS) / GRID_HOURS * 100);
                      const leftPct  = (ev._col  / ev._total) * 100;
                      const widthPct = (1        / ev._total) * 100;
                      return (
                        <Tooltip key={ev.id} title={`${ev.calendarName} · ${ev.userName} · ${formatTime12(ev.start)}–${formatTime12(ev.end)}`} placement="right">
                          <Box sx={{
                            position: "absolute",
                            top:    `${topPct}%`,
                            height: `max(20px, ${hPct.toFixed(2)}%)`,
                            left:   `calc(${leftPct.toFixed(1)}% + 1px)`,
                            width:  `calc(${widthPct.toFixed(1)}% - 2px)`,
                            bgcolor: ev.color, color: "#fff",
                            borderRadius: 0.75, px: 0.5, py: 0.25,
                            overflow: "hidden", cursor: "default", zIndex: 2,
                          }}>
                            <Typography sx={{ fontSize: "0.6rem", fontWeight: 700, lineHeight: 1.2, opacity: 0.9 }}>
                              {formatTime12(ev.start)}
                            </Typography>
                            <Typography sx={{
                              fontSize: "0.65rem", fontWeight: 600, lineHeight: 1.2,
                              overflow: "hidden", display: "-webkit-box",
                              WebkitLineClamp: 3, WebkitBoxOrient: "vertical",
                            }}>
                              {ev.title}
                            </Typography>
                            <Typography sx={{ fontSize: "0.5rem", opacity: 0.8, overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis" }}>
                              {ev.calendarName}
                            </Typography>
                          </Box>
                        </Tooltip>
                      );
                    })}
                  </Box>
                );
              })}

              {/* Current time line */}
              {todayInRange && nowH >= GRID_START && nowH <= GRID_END && (
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
                        bgcolor: ev.color, color: "#fff",
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
  const navigate         = useNavigate();
  const [info, setInfo]  = useState(null);

  useEffect(() => {
    api.get("/api/system").then(res => setInfo(res.data)).catch(() => {});
  }, []);

  return (
    <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", px: 1, flexShrink: 0, gap: 1 }}>

      {/* Left: IP + FQDN */}
      <Box sx={{ display: "flex", gap: 2, flex: 1 }}>
        {info?.ip && (
          <Typography variant="caption" color="text.disabled" sx={{ fontFamily: "monospace" }}>
            {info.ip}
          </Typography>
        )}
        {info?.fqdn && info.fqdn !== info.ip && (
          <Typography variant="caption" color="text.disabled" sx={{ fontFamily: "monospace" }}>
            {info.fqdn}
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

      {/* Right: theme toggle + admin */}
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, flex: 1, justifyContent: "flex-end" }}>
        <Tooltip title={mode === "light" ? "Switch to dark" : mode === "dark" ? "Switch to auto (sunrise/sunset)" : "Switch to light"}>
          <IconButton size="small" onClick={toggle} sx={{ color: "text.disabled" }}>
            {mode === "light" ? <DarkModeIcon fontSize="small" /> : mode === "dark" ? <BrightnessAutoIcon fontSize="small" /> : <LightModeIcon fontSize="small" />}
          </IconButton>
        </Tooltip>
        <Button size="small" variant="text"
          sx={{ py: 0.25, px: 1, fontSize: "0.7rem", minWidth: 0, color: "text.disabled" }}
          onClick={() => navigate("/admin")}>
          Admin
        </Button>
      </Box>
    </Box>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const navigate                   = useNavigate();
  const [view,     setView]        = useState("week");
  const [baseDate, setBaseDate]    = useState(() => startOfDay(new Date()));

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
    </Box>
  );
}
