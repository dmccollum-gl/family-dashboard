import { createContext, useContext, useState, useMemo, useEffect } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { ThemeProvider, createTheme, CssBaseline } from "@mui/material";
import Dashboard from "./pages/Dashboard";
import Settings  from "./pages/Settings";
import Admin     from "./pages/Admin";
import Setup     from "./pages/Setup";
import Mobile    from "./pages/Mobile";

export const ColorModeContext = createContext({ toggle: () => {}, mode: "light", effectiveMode: "light" });

export function useColorMode() {
  return useContext(ColorModeContext);
}

function computeAutoMode(sunTimes) {
  const nowSec = Date.now() / 1000;
  if (sunTimes) {
    return nowSec >= sunTimes.sunrise && nowSec < sunTimes.sunset ? "light" : "dark";
  }
  // Fallback when weather not configured: 6 am – 8 pm = light
  const h = new Date().getHours();
  return h >= 6 && h < 20 ? "light" : "dark";
}

// Detect mobile: phone/tablet UA or a narrow touch screen (excludes the Pi kiosk).
const isMobile =
  /Mobi|Android|iPhone|iPad|iPod/i.test(navigator.userAgent) ||
  (navigator.maxTouchPoints > 1 && window.innerWidth < 1024);

export default function App() {
  const [needsSetup, setNeedsSetup] = useState(false);

  useEffect(() => {
    fetch("/api/setup/status")
      .then(r => r.json())
      // Only show setup if the device has never been configured.
      // Recovery hotspot mode on an already-configured Pi must NOT redirect here —
      // that's what was silently wiping user data on router restarts.
      .then(d => setNeedsSetup(!d.configured))
      .catch(() => setNeedsSetup(false)); // dev environment — skip setup
  }, []);

  const [mode, setMode] = useState(
    () => localStorage.getItem("dashboard_theme") || "light"
  );
  const [sunTimes,     setSunTimes]     = useState(null);
  const [effectiveMode, setEffectiveMode] = useState(() => {
    const stored = localStorage.getItem("dashboard_theme") || "light";
    return stored === "auto" ? computeAutoMode(null) : stored;
  });

  // Fetch sunrise/sunset whenever in auto mode
  useEffect(() => {
    if (mode !== "auto") return;
    const load = async () => {
      try {
        const res = await fetch("/api/weather/current");
        if (res.ok) {
          const data = await res.json();
          if (data.sunrise && data.sunset) setSunTimes({ sunrise: data.sunrise, sunset: data.sunset });
        }
      } catch {}
    };
    load();
    const id = setInterval(load, 10 * 60 * 1000);
    return () => clearInterval(id);
  }, [mode]);

  // Recompute effectiveMode every minute in auto mode, immediately otherwise
  useEffect(() => {
    if (mode !== "auto") { setEffectiveMode(mode); return; }
    const recompute = () => setEffectiveMode(computeAutoMode(sunTimes));
    recompute();
    const id = setInterval(recompute, 60 * 1000);
    return () => clearInterval(id);
  }, [mode, sunTimes]);

  // Cycle: light → dark → auto
  const toggle = () => {
    setMode(prev => {
      const next = prev === "light" ? "dark" : prev === "dark" ? "auto" : "light";
      localStorage.setItem("dashboard_theme", next);
      return next;
    });
  };

  const theme = useMemo(() => createTheme({
    palette: {
      mode: effectiveMode,
      ...(effectiveMode === "dark" ? {
        background: { default: "#121212", paper: "#1e1e1e" },
      } : {}),
    },
  }), [effectiveMode]);

  return (
    <ColorModeContext.Provider value={{ toggle, mode, effectiveMode }}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        <Routes>
          <Route path="/setup" element={<Setup />} />
          {needsSetup ? (
            <Route path="*" element={<Navigate to="/setup" replace />} />
          ) : (
            <>
              <Route path="/"         element={isMobile ? <Navigate to="/mobile" replace /> : <Dashboard />} />
              <Route path="/mobile"   element={<Mobile />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/admin"    element={<Admin />} />
              <Route path="*"         element={<Navigate to="/" replace />} />
            </>
          )}
        </Routes>
      </ThemeProvider>
    </ColorModeContext.Provider>
  );
}
