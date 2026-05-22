import { useState, useEffect } from "react";
import {
  Box, Typography, Paper, TextField, Button, Divider,
  ToggleButton, ToggleButtonGroup, IconButton, Alert,
  CircularProgress, Tooltip, InputAdornment, List, ListItem,
  Dialog, DialogTitle, DialogContent, DialogContentText, DialogActions,
} from "@mui/material";
import SaveIcon          from "@mui/icons-material/Save";
import VisibilityIcon    from "@mui/icons-material/Visibility";
import VisibilityOffIcon from "@mui/icons-material/VisibilityOff";
import LockIcon          from "@mui/icons-material/Lock";
import CloudIcon         from "@mui/icons-material/Cloud";
import DashboardIcon     from "@mui/icons-material/Dashboard";
import RestartAltIcon    from "@mui/icons-material/RestartAlt";
import TvIcon            from "@mui/icons-material/Tv";
import ReplayIcon        from "@mui/icons-material/Replay";
import { useNavigate }   from "react-router-dom";
import api               from "../../api/client";

function Section({ icon, title, children }) {
  return (
    <Paper variant="outlined" sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <Box sx={{ color: "primary.main", display: "flex" }}>{icon}</Box>
        <Typography variant="h6" fontWeight={600}>{title}</Typography>
      </Box>
      <Divider />
      {children}
    </Paper>
  );
}

function OAuthSettings() {
  const [clientId,     setClientId]     = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [showSecret,   setShowSecret]   = useState(false);
  const [saving,       setSaving]       = useState(false);
  const [msg,          setMsg]          = useState(null);
  const [error,        setError]        = useState(null);

  useEffect(() => {
    api.get("/api/settings/oauth").then(res => {
      setClientId(res.data.client_id || "");
      setClientSecret(res.data.client_secret || "");
    }).catch(() => {});
  }, []);

  const handleSave = async () => {
    setSaving(true); setMsg(null); setError(null);
    try {
      await api.put("/api/settings/oauth", { client_id: clientId, client_secret: clientSecret });
      setMsg("Saved. Refresh the page for changes to take effect.");
      if (clientSecret && clientSecret !== "••••••••") setClientSecret("••••••••");
    } catch {
      setError("Failed to save.");
    } finally { setSaving(false); }
  };

  return (
    <Section icon={<LockIcon />} title="Google OAuth Credentials">
      <Typography variant="body2" color="text.secondary">
        Both values are found in <strong>Google Cloud Console → APIs &amp; Services → Credentials</strong> —
        click your OAuth 2.0 Client ID to see them. The Client Secret is needed for persistent sign-ins
        (refresh tokens). This is a one-time setup — family members never see this page.
      </Typography>
      <TextField
        label="Client ID" size="small" fullWidth
        value={clientId} onChange={e => setClientId(e.target.value)}
        placeholder="xxxxxxxxxxxx-xxxxxxxx.apps.googleusercontent.com"
      />
      <TextField
        label="Client Secret" size="small" fullWidth
        type={showSecret ? "text" : "password"}
        value={clientSecret} onChange={e => setClientSecret(e.target.value)}
        placeholder="GOCSPX-xxxxxxxxxxxxxxxxxxxx"
        InputProps={{
          endAdornment: (
            <InputAdornment position="end">
              <Tooltip title={showSecret ? "Hide" : "Show"}>
                <IconButton size="small" onClick={() => setShowSecret(v => !v)}>
                  {showSecret ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
                </IconButton>
              </Tooltip>
            </InputAdornment>
          ),
        }}
      />
      {msg   && <Alert severity="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {error && <Alert severity="error"   onClose={() => setError(null)}>{error}</Alert>}
      <Box>
        <Button variant="contained"
          startIcon={saving ? <CircularProgress size={16} color="inherit" /> : <SaveIcon />}
          onClick={handleSave} disabled={saving || !clientId.trim() || !clientSecret.trim()}>
          Save Credentials
        </Button>
      </Box>
    </Section>
  );
}

function WeatherSettings() {
  const [apiKey,   setApiKey]   = useState("");
  const [location, setLocation] = useState("");
  const [units,    setUnits]    = useState("imperial");
  const [showKey,  setShowKey]  = useState(false);
  const [saving,   setSaving]   = useState(false);
  const [msg,      setMsg]      = useState(null);
  const [error,    setError]    = useState(null);

  useEffect(() => {
    api.get("/api/settings/weather").then(res => {
      setApiKey(res.data.api_key || "");
      setLocation(res.data.location || "");
      setUnits(res.data.units || "imperial");
    }).catch(() => {});
  }, []);

  const handleSave = async () => {
    setSaving(true); setMsg(null); setError(null);
    try {
      await api.put("/api/settings/weather", { api_key: apiKey, location, units });
      setMsg("Weather settings saved.");
      if (apiKey && apiKey !== "••••••••") setApiKey("••••••••");
    } catch {
      setError("Failed to save.");
    } finally { setSaving(false); }
  };

  return (
    <Section icon={<CloudIcon />} title="OpenWeatherMap">
      <Typography variant="body2" color="text.secondary">
        Free API key at <a href="https://openweathermap.org/api" target="_blank" rel="noreferrer">openweathermap.org</a>.
      </Typography>
      <TextField
        label="API Key" size="small" fullWidth
        type={showKey ? "text" : "password"}
        value={apiKey} onChange={e => setApiKey(e.target.value)}
        placeholder="Paste your OWM API key"
        InputProps={{
          endAdornment: (
            <InputAdornment position="end">
              <Tooltip title={showKey ? "Hide" : "Show"}>
                <IconButton size="small" onClick={() => setShowKey(v => !v)}>
                  {showKey ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
                </IconButton>
              </Tooltip>
            </InputAdornment>
          ),
        }}
      />
      <TextField
        label="Location" size="small" fullWidth
        value={location} onChange={e => setLocation(e.target.value)}
        placeholder="e.g. Nashville, TN  or  37201,US"
      />
      <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
        <Typography variant="body2" color="text.secondary">Units:</Typography>
        <ToggleButtonGroup size="small" exclusive value={units}
          onChange={(_, v) => { if (v) setUnits(v); }}>
          <ToggleButton value="imperial">Imperial (°F)</ToggleButton>
          <ToggleButton value="metric">Metric (°C)</ToggleButton>
        </ToggleButtonGroup>
      </Box>
      {msg   && <Alert severity="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {error && <Alert severity="error"   onClose={() => setError(null)}>{error}</Alert>}
      <Box>
        <Button variant="contained"
          startIcon={saving ? <CircularProgress size={16} color="inherit" /> : <SaveIcon />}
          onClick={handleSave} disabled={saving}>
          Save Weather Settings
        </Button>
      </Box>
    </Section>
  );
}

function DisplaySettings() {
  const [theme,  setTheme]  = useState("auto");
  const [view,   setView]   = useState("week");
  const [saving, setSaving] = useState(false);
  const [msg,    setMsg]    = useState(null);
  const [error,  setError]  = useState(null);

  useEffect(() => {
    api.get("/api/settings/display").then(res => {
      setTheme(res.data.theme || "auto");
      setView(res.data.view  || "week");
    }).catch(() => {});
  }, []);

  const handleSave = async () => {
    setSaving(true); setMsg(null); setError(null);
    try {
      await api.put("/api/settings/display", { theme, view });
      setMsg("Display settings saved. The Pi screen updates within 30 seconds.");
    } catch {
      setError("Failed to save.");
    } finally { setSaving(false); }
  };

  return (
    <Section icon={<TvIcon />} title="Pi Display">
      <Typography variant="body2" color="text.secondary">
        Controls the default colour theme and calendar view shown on the physical display.
        Changes are picked up automatically — no restart needed.
      </Typography>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
        <Typography variant="body2" color="text.secondary" sx={{ minWidth: 50 }}>Theme:</Typography>
        <ToggleButtonGroup size="small" exclusive value={theme}
          onChange={(_, v) => { if (v) setTheme(v); }}>
          <ToggleButton value="auto">Auto (sunrise/sunset)</ToggleButton>
          <ToggleButton value="light">Light</ToggleButton>
          <ToggleButton value="dark">Dark</ToggleButton>
        </ToggleButtonGroup>
      </Box>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
        <Typography variant="body2" color="text.secondary" sx={{ minWidth: 50 }}>View:</Typography>
        <ToggleButtonGroup size="small" exclusive value={view}
          onChange={(_, v) => { if (v) setView(v); }}>
          <ToggleButton value="day">Day</ToggleButton>
          <ToggleButton value="week">Week</ToggleButton>
          <ToggleButton value="2week">2 Week</ToggleButton>
          <ToggleButton value="month">Month</ToggleButton>
        </ToggleButtonGroup>
      </Box>
      {msg   && <Alert severity="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {error && <Alert severity="error"   onClose={() => setError(null)}>{error}</Alert>}
      <Box>
        <Button variant="contained"
          startIcon={saving ? <CircularProgress size={16} color="inherit" /> : <SaveIcon />}
          onClick={handleSave} disabled={saving}>
          Save Display Settings
        </Button>
      </Box>
    </Section>
  );
}

function RestartSection() {
  const [backendBusy, setBackendBusy] = useState(false);
  const [displayBusy, setDisplayBusy] = useState(false);
  const [msg,         setMsg]         = useState(null);
  const [error,       setError]       = useState(null);

  const restart = async (target) => {
    const setB = target === "backend" ? setBackendBusy : setDisplayBusy;
    setB(true); setMsg(null); setError(null);
    try {
      await api.post(`/api/settings/restart/${target}`);
      setMsg(
        target === "backend"
          ? "Backend restarting — this page will reconnect in a few seconds."
          : "Pi display restarting."
      );
    } catch {
      setError("Restart command failed.");
    } finally {
      setB(false);
    }
  };

  return (
    <Section icon={<ReplayIcon />} title="Restart Services">
      <Typography variant="body2" color="text.secondary">
        Restart the backend API or the Pi display process without rebooting the Pi.
        The backend briefly drops and reconnects automatically.
      </Typography>
      {msg   && <Alert severity="info"  onClose={() => setMsg(null)}>{msg}</Alert>}
      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
      <Box sx={{ display: "flex", gap: 2, flexWrap: "wrap" }}>
        <Button
          variant="outlined"
          startIcon={backendBusy ? <CircularProgress size={16} color="inherit" /> : <ReplayIcon />}
          onClick={() => restart("backend")}
          disabled={backendBusy || displayBusy}
        >
          Restart Backend
        </Button>
        <Button
          variant="outlined"
          startIcon={displayBusy ? <CircularProgress size={16} color="inherit" /> : <TvIcon />}
          onClick={() => restart("display")}
          disabled={backendBusy || displayBusy}
        >
          Restart Display
        </Button>
      </Box>
    </Section>
  );
}

function ResetSection() {
  const [open,    setOpen]    = useState(false);
  const [busy,    setBusy]    = useState(false);
  const [success, setSuccess] = useState(false);
  const [error,   setError]   = useState(null);

  const handleReset = async () => {
    setBusy(true); setError(null);
    try {
      await api.post("/api/setup/reset");
      setSuccess(true);
    } catch {
      setError("Reset failed — try again.");
    } finally {
      setBusy(false);
      setOpen(false);
    }
  };

  return (
    <Section icon={<RestartAltIcon />} title="Reset Install">
      <Typography variant="body2" color="text.secondary">
        Removes all signed-in calendar users and clears all RSS feeds.
        OAuth and weather credentials are kept. Use this to hand the
        dashboard off to a new family or start fresh.
      </Typography>
      {success && (
        <Alert severity="success" onClose={() => setSuccess(false)}>
          Reset complete — all users and RSS feeds have been removed.
        </Alert>
      )}
      {error && (
        <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>
      )}
      <Box>
        <Button
          variant="outlined"
          color="error"
          startIcon={<RestartAltIcon />}
          onClick={() => setOpen(true)}
        >
          Reset Install
        </Button>
      </Box>

      <Dialog open={open} onClose={() => !busy && setOpen(false)}>
        <DialogTitle>Reset this install?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This will remove <strong>all signed-in users</strong> and <strong>all RSS feeds</strong>.
            OAuth and weather credentials will be kept.
            This cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)} disabled={busy}>Cancel</Button>
          <Button
            color="error"
            variant="contained"
            onClick={handleReset}
            disabled={busy}
            startIcon={busy ? <CircularProgress size={16} color="inherit" /> : <RestartAltIcon />}
          >
            {busy ? "Resetting…" : "Reset"}
          </Button>
        </DialogActions>
      </Dialog>
    </Section>
  );
}

export default function Admin() {
  const navigate = useNavigate();

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "grey.100", py: 4, px: 3 }}>
      <Box sx={{ maxWidth: 760, mx: "auto", display: "flex", flexDirection: "column", gap: 3 }}>

        <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <Box>
            <Typography variant="h4" fontWeight={700}>Admin Setup</Typography>
            <Typography variant="body2" color="text.secondary">
              One-time configuration — family members never see this page.
            </Typography>
          </Box>
          <Button variant="outlined" startIcon={<DashboardIcon />} onClick={() => navigate("/")}>
            Back to Dashboard
          </Button>
        </Box>

        <OAuthSettings />
        <WeatherSettings />
        <DisplaySettings />
        <RestartSection />
        <ResetSection />

      </Box>
    </Box>
  );
}
