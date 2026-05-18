import { useState, useEffect } from "react";
import {
  Box, Typography, Paper, TextField, Button, Divider,
  ToggleButton, ToggleButtonGroup, IconButton, Alert,
  CircularProgress, Tooltip, InputAdornment, List, ListItem,
} from "@mui/material";
import AddIcon           from "@mui/icons-material/Add";
import DeleteIcon        from "@mui/icons-material/Delete";
import SaveIcon          from "@mui/icons-material/Save";
import VisibilityIcon    from "@mui/icons-material/Visibility";
import VisibilityOffIcon from "@mui/icons-material/VisibilityOff";
import LockIcon          from "@mui/icons-material/Lock";
import CloudIcon         from "@mui/icons-material/Cloud";
import RssFeedIcon       from "@mui/icons-material/RssFeed";
import DashboardIcon     from "@mui/icons-material/Dashboard";
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

function RssSettings() {
  const [feeds,  setFeeds]  = useState([{ url: "", label: "" }]);
  const [saving, setSaving] = useState(false);
  const [msg,    setMsg]    = useState(null);
  const [error,  setError]  = useState(null);

  useEffect(() => {
    api.get("/api/settings/rss").then(res => {
      const loaded = res.data.feeds || [];
      setFeeds(loaded.length > 0 ? loaded : [{ url: "", label: "" }]);
    }).catch(() => {});
  }, []);

  const updateFeed = (i, field, value) =>
    setFeeds(prev => prev.map((f, idx) => idx === i ? { ...f, [field]: value } : f));
  const addFeed    = () => setFeeds(prev => [...prev, { url: "", label: "" }]);
  const removeFeed = (i) =>
    setFeeds(prev => prev.length === 1 ? [{ url: "", label: "" }] : prev.filter((_, idx) => idx !== i));

  const handleSave = async () => {
    setSaving(true); setMsg(null); setError(null);
    try {
      const res = await api.put("/api/settings/rss", { feeds });
      setMsg(`Saved ${res.data.count} feed${res.data.count !== 1 ? "s" : ""}.`);
    } catch {
      setError("Failed to save.");
    } finally { setSaving(false); }
  };

  return (
    <Section icon={<RssFeedIcon />} title="RSS News Feeds">
      <Typography variant="body2" color="text.secondary">
        Headlines rotate in the news ticker at the top of the dashboard.
        The label is optional and appears as a source tag.
      </Typography>
      <List disablePadding sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        {feeds.map((feed, i) => (
          <ListItem key={i} disableGutters disablePadding>
            <Box sx={{ display: "flex", gap: 1, width: "100%", alignItems: "flex-start" }}>
              <TextField
                label="Feed URL" size="small" sx={{ flex: 3 }}
                value={feed.url} onChange={e => updateFeed(i, "url", e.target.value)}
                placeholder="https://feeds.example.com/rss"
              />
              <TextField
                label="Label (optional)" size="small" sx={{ flex: 1 }}
                value={feed.label} onChange={e => updateFeed(i, "label", e.target.value)}
                placeholder="e.g. AP News"
              />
              <Tooltip title="Remove">
                <IconButton size="small" color="error" onClick={() => removeFeed(i)} sx={{ mt: 0.5 }}>
                  <DeleteIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            </Box>
          </ListItem>
        ))}
      </List>
      <Box>
        <Button size="small" startIcon={<AddIcon />} onClick={addFeed} variant="outlined">
          Add Feed
        </Button>
      </Box>
      {msg   && <Alert severity="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {error && <Alert severity="error"   onClose={() => setError(null)}>{error}</Alert>}
      <Box>
        <Button variant="contained"
          startIcon={saving ? <CircularProgress size={16} color="inherit" /> : <SaveIcon />}
          onClick={handleSave} disabled={saving}>
          Save RSS Feeds
        </Button>
      </Box>
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
        <RssSettings />

      </Box>
    </Box>
  );
}
