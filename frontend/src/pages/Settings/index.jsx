import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  Box, Typography, Button, Divider, Alert, TextField,
  CircularProgress, List, ListItem, Avatar, Chip, Menu, MenuItem,
  Dialog, DialogTitle, DialogContent, DialogActions,
  Switch, ListItemText, ListItemIcon, IconButton, Tooltip,
  Checkbox, Radio, RadioGroup, FormControlLabel, FormControl, FormLabel,
  ToggleButton, ToggleButtonGroup, InputAdornment,
  Accordion, AccordionSummary, AccordionDetails,
} from "@mui/material";
import SaveIcon                  from "@mui/icons-material/Save";
import AccountCircleIcon         from "@mui/icons-material/AccountCircle";
import DashboardIcon             from "@mui/icons-material/Dashboard";
import LogoutIcon                from "@mui/icons-material/Logout";
import CalendarMonthIcon         from "@mui/icons-material/CalendarMonth";
import ContentCopyIcon           from "@mui/icons-material/ContentCopy";
import DeleteIcon                from "@mui/icons-material/Delete";
import RefreshIcon               from "@mui/icons-material/Refresh";
import MoreVertIcon              from "@mui/icons-material/MoreVert";
import GroupAddIcon              from "@mui/icons-material/GroupAdd";
import AddCircleOutlineIcon      from "@mui/icons-material/AddCircleOutline";
import AddIcon                   from "@mui/icons-material/Add";
import RssFeedIcon               from "@mui/icons-material/RssFeed";
import CloudIcon                 from "@mui/icons-material/Cloud";
import ClearIcon                 from "@mui/icons-material/Clear";
import TvIcon                    from "@mui/icons-material/Tv";
import ReplayIcon                from "@mui/icons-material/Replay";
import SecurityIcon              from "@mui/icons-material/Security";
import AdminPanelSettingsIcon    from "@mui/icons-material/AdminPanelSettings";
import LockIcon                  from "@mui/icons-material/Lock";
import BackupIcon                from "@mui/icons-material/Backup";
import RestartAltIcon            from "@mui/icons-material/RestartAlt";
import DownloadIcon              from "@mui/icons-material/Download";
import UploadIcon                from "@mui/icons-material/Upload";
import VisibilityIcon            from "@mui/icons-material/Visibility";
import VisibilityOffIcon         from "@mui/icons-material/VisibilityOff";
import OpenInNewIcon             from "@mui/icons-material/OpenInNew";
import ExpandMoreIcon            from "@mui/icons-material/ExpandMore";
import RouterIcon                from "@mui/icons-material/Router";
import TravelExploreIcon        from "@mui/icons-material/TravelExplore";
import SystemUpdateAltIcon       from "@mui/icons-material/SystemUpdateAlt";
import AccessTimeIcon            from "@mui/icons-material/AccessTime";
import PowerSettingsNewIcon      from "@mui/icons-material/PowerSettingsNew";
import { googleLogout, useGoogleLogin } from "@react-oauth/google";
import { useNavigate } from "react-router-dom";
import api from "../../api/client";

const CALENDAR_SCOPE = [
  "openid",
  "email",
  "profile",
  "https://www.googleapis.com/auth/calendar",
].join(" ");

// ── local user store (identity only — no tokens) ──────────────────────────────

function getStoredUser() {
  try { return JSON.parse(localStorage.getItem("dashboard_active_user") || "null"); }
  catch { return null; }
}
function storeUser(u)  { localStorage.setItem("dashboard_active_user", JSON.stringify(u)); }
function clearStoredUser() { localStorage.removeItem("dashboard_active_user"); }

function getStoredRole() { return localStorage.getItem("dashboard_role") || "user"; }
function storeRole(r)    { localStorage.setItem("dashboard_role", r); }
function clearStoredRole() { localStorage.removeItem("dashboard_role"); }

// ── Calendar URL / ID parser ───────────────────────────────────────────────────

function calShareUrl(calId) {
  const b64 = btoa(calId).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `https://calendar.google.com/calendar/r?cid=${b64}`;
}

// Google Calendar "Add from URL" deep-link — opens the subscribe confirmation dialog.
function googleCalSubscribeUrl(calId) {
  const icsUrl = `https://calendar.google.com/calendar/ical/${encodeURIComponent(calId)}/public/basic.ics`;
  return `https://calendar.google.com/calendar/render?cid=${encodeURIComponent(icsUrl)}`;
}

function extractCalendarId(raw) {
  const s = raw.trim();
  const srcMatch = s.match(/[?&]src=([^&\s]+)/);
  if (srcMatch) return decodeURIComponent(srcMatch[1]);
  const cidMatch = s.match(/[?&]cid=([^&\s]+)/);
  if (cidMatch) {
    try { return atob(cidMatch[1].replace(/-/g, "+").replace(/_/g, "/")); } catch {}
  }
  return s;
}

// ── Section content wrapper (used inside each tab pane) ──────────────────────

function Section({ icon, title, children }) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {(icon || title) && (
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, pb: 1.5, borderBottom: "1px solid", borderColor: "divider" }}>
          {icon && <Box sx={{ color: "primary.main", display: "flex" }}>{icon}</Box>}
          {title && <Typography variant="h6" fontWeight={600}>{title}</Typography>}
        </Box>
      )}
      {children}
    </Box>
  );
}

// ── Sign-in button ─────────────────────────────────────────────────────────────

function GoogleSignInButton({ onSuccess, onError, hasSecret, label = "Sign in with Google" }) {
  const login = useGoogleLogin(
    hasSecret
      ? { flow: "auth-code", scope: CALENDAR_SCOPE, onSuccess, onError: () => onError("Sign-in was cancelled or failed.") }
      : { scope: CALENDAR_SCOPE, onSuccess, onError: () => onError("Sign-in was cancelled or failed.") }
  );
  return <Button variant="contained" onClick={() => login()}>{label}</Button>;
}

// ── Family Sharing dialog ──────────────────────────────────────────────────────

function FamilySharingDialog({ cal, ownerEmail, open, onClose }) {
  const [members,    setMembers]    = useState([]);
  const [subscribed, setSubscribed] = useState({});
  const [loading,    setLoading]    = useState(false);
  const [busy,       setBusy]       = useState({});
  const [error,      setError]      = useState(null);

  useEffect(() => {
    if (!open || !cal) return;
    setLoading(true); setError(null); setBusy({});
    api.get("/api/user-prefs").then(async res => {
      const others = (res.data || []).filter(m => m.email !== ownerEmail);
      setMembers(others);
      const checks = await Promise.allSettled(
        others.map(m => api.get(`/api/calendar/list/${encodeURIComponent(m.email)}`))
      );
      const subs = {};
      others.forEach((m, i) => {
        subs[m.email] = checks[i].status === "fulfilled"
          ? (checks[i].value.data.calendars || []).some(c => c.id === cal.id)
          : false;
      });
      setSubscribed(subs);
    }).catch(() => setError("Could not load family members."))
      .finally(() => setLoading(false));
  }, [open, cal, ownerEmail]);

  const handleToggle = async (memberEmail) => {
    const was = !!subscribed[memberEmail];
    setBusy(p => ({ ...p, [memberEmail]: true }));
    try {
      if (was) {
        await api.delete(`/api/calendar/subscription/${encodeURIComponent(memberEmail)}/${encodeURIComponent(cal.id)}`);
      } else {
        await api.post(`/api/calendar/subscription/${encodeURIComponent(memberEmail)}`, { calendar_id: cal.id });
      }
      setSubscribed(p => ({ ...p, [memberEmail]: !was }));
    } catch (e) {
      const msg = e?.response?.data?.detail || "Action failed.";
      const name = members.find(m => m.email === memberEmail)?.display_name || memberEmail;
      setError(
        msg.includes("insufficient authentication scopes")
          ? `${name} needs to sign out and sign back in to grant calendar access.`
          : msg
      );
    } finally { setBusy(p => ({ ...p, [memberEmail]: false })); }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Family Sharing — {cal?.summary}</DialogTitle>
      <DialogContent>
        {loading ? (
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, py: 2 }}>
            <CircularProgress size={20} />
            <Typography variant="body2" color="text.secondary">Checking subscriptions…</Typography>
          </Box>
        ) : (
          <>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Checked members have this calendar in their Google Calendar.
            </Typography>
            {error && <Alert severity="warning" onClose={() => setError(null)} sx={{ mb: 1 }}>{error}</Alert>}
            {members.length === 0 ? (
              <Typography variant="body2" color="text.secondary">No other family members are signed in.</Typography>
            ) : (
              <List dense disablePadding>
                {members.map(m => (
                  <ListItem key={m.email} disableGutters sx={{ py: 0.5 }}>
                    <ListItemIcon sx={{ minWidth: 36 }}>
                      {busy[m.email]
                        ? <CircularProgress size={20} />
                        : <Checkbox size="small" edge="start"
                            checked={!!subscribed[m.email]}
                            onChange={() => handleToggle(m.email)} />
                      }
                    </ListItemIcon>
                    <Box sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: m.display_color, mr: 1.5, flexShrink: 0 }} />
                    <ListItemText
                      primary={m.display_name || m.email}
                      secondary={m.email}
                      primaryTypographyProps={{ variant: "body2", fontWeight: 500 }}
                      secondaryTypographyProps={{ variant: "caption" }}
                    />
                  </ListItem>
                ))}
              </List>
            )}
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}

// ── Assign Calendar dialog ─────────────────────────────────────────────────────

function AssignCalendarDialog({ cal, open, onClose, ownerEmail, onSaved }) {
  const [members,   setMembers]   = useState([]);
  const [primary,   setPrimary]   = useState("");
  const [secondary, setSecondary] = useState(new Set());
  const [busy,      setBusy]      = useState(false);
  const [msg,       setMsg]       = useState(null);
  const [error,     setError]     = useState(null);
  const [shareUrl,  setShareUrl]  = useState(null);

  useEffect(() => {
    if (!open || !cal) return;
    setMsg(null); setError(null); setSecondary(new Set()); setShareUrl(null);
    api.get("/api/user-prefs").then(res => {
      const m = res.data || [];
      setMembers(m);
      setPrimary(m.length ? m[0].email : "");
    }).catch(() => {});
  }, [open, cal]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleSecondary = (email) => {
    if (email === primary) return;
    setSecondary(prev => {
      const next = new Set(prev);
      if (next.has(email)) next.delete(email); else next.add(email);
      return next;
    });
  };

  const handlePrimaryChange = (email) => {
    setPrimary(email);
    setSecondary(prev => { const s = new Set(prev); s.delete(email); return s; });
  };

  const handleSave = async () => {
    if (!primary || !cal) return;
    setBusy(true); setMsg(null); setError(null); setShareUrl(null);
    let pendingShareUrl = null;
    try {
      // Subscribe primary (best effort — imported/external calendars return share_required)
      try {
        const subRes = await api.post(`/api/calendar/subscription/${encodeURIComponent(primary)}`, { calendar_id: cal.id, owner_email: ownerEmail });
        if (subRes.data?.status === "share_required") pendingShareUrl = subRes.data.share_url;
      } catch (_) {}

      // Turn ON for primary
      const prefsRes = await api.get(`/api/user-prefs/${encodeURIComponent(primary)}`);
      const existing = prefsRes.data.selected_calendars || [];
      if (!existing.some(c => c.id === cal.id)) {
        existing.push({ id: cal.id, color: null });
      }
      await api.put(`/api/user-prefs/${encodeURIComponent(primary)}`, { selected_calendars: existing });

      // All non-primary members: subscribe if secondary (best effort), always turn OFF for dashboard
      await Promise.allSettled(
        members.filter(m => m.email !== primary).map(async m => {
          if (secondary.has(m.email)) {
            try {
              const subRes = await api.post(`/api/calendar/subscription/${encodeURIComponent(m.email)}`, { calendar_id: cal.id, owner_email: ownerEmail });
              if (!pendingShareUrl && subRes.data?.status === "share_required") pendingShareUrl = subRes.data.share_url;
            } catch (_) {}
          }
          const secPrefs = await api.get(`/api/user-prefs/${encodeURIComponent(m.email)}`);
          const stripped = (secPrefs.data.selected_calendars || []).filter(c => c.id !== cal.id);
          await api.put(`/api/user-prefs/${encodeURIComponent(m.email)}`, { selected_calendars: stripped });
        })
      );

      const name = members.find(m => m.email === primary)?.display_name || primary;
      setMsg(`Assigned to ${name} — will appear on the dashboard.`);
      if (pendingShareUrl) setShareUrl(pendingShareUrl);
      if (onSaved) onSaved();
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to assign calendar.");
    } finally { setBusy(false); }
  };

  const secondaryOptions = members.filter(m => m.email !== primary);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Assign "{cal?.summary}"</DialogTitle>
      <DialogContent>
        {msg      && <Alert severity="success" sx={{ mb: 2 }}>{msg}</Alert>}
        {error    && <Alert severity="error"   sx={{ mb: 2 }}>{error}</Alert>}
        {shareUrl && (
          <Alert severity="info" onClose={() => setShareUrl(null)} sx={{ mb: 2 }}
            action={<Button size="small" onClick={() => window.open(shareUrl, "_blank")}>Open Link</Button>}>
            This calendar can't be shared automatically. Share this link with assigned members so they can subscribe.
          </Alert>
        )}
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          The primary user's copy will show on the dashboard. Secondary users get it
          added to their Google Calendar without dashboard visibility.
        </Typography>
        <FormControl component="fieldset" fullWidth>
          <FormLabel sx={{ fontSize: "0.875rem", fontWeight: 500, mb: 0.5 }}>
            Primary — shown on dashboard
          </FormLabel>
          <RadioGroup value={primary} onChange={e => handlePrimaryChange(e.target.value)}>
            {members.map(m => (
              <FormControlLabel key={m.email} value={m.email}
                control={<Radio size="small" />}
                label={
                  <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                    <Box sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: m.display_color }} />
                    <Typography variant="body2">{m.display_name || m.email}</Typography>
                  </Box>
                }
              />
            ))}
          </RadioGroup>
        </FormControl>
        {secondaryOptions.length > 0 && (
          <FormControl component="fieldset" sx={{ mt: 2 }} fullWidth>
            <FormLabel sx={{ fontSize: "0.875rem", fontWeight: 500, mb: 0.5 }}>
              Secondary — added to Google Calendar only
            </FormLabel>
            {secondaryOptions.map(m => (
              <FormControlLabel key={m.email}
                control={<Checkbox size="small" checked={secondary.has(m.email)} onChange={() => toggleSecondary(m.email)} />}
                label={
                  <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                    <Box sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: m.display_color }} />
                    <Typography variant="body2">{m.display_name || m.email}</Typography>
                  </Box>
                }
              />
            ))}
          </FormControl>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={handleSave} disabled={busy || !primary}>
          {busy ? <CircularProgress size={18} color="inherit" /> : "Assign"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

// ── Calendar picker ────────────────────────────────────────────────────────────

const SCOPE_ERROR = "Request had insufficient authentication scopes";
function isScopeError(msg) { return typeof msg === "string" && msg.includes("insufficient authentication scopes"); }

function CalendarPicker({ email, selected, onChange, calColors, onColorChange, userColor, onReauth, onCalendarAssigned }) {
  const [calendars,    setCalendars]    = useState([]);
  const [loading,      setLoading]      = useState(false);
  const [error,        setError]        = useState(null);
  const [members,      setMembers]      = useState([]);
  const [menuState,    setMenuState]    = useState(null);
  const [dialog,       setDialog]       = useState(null);
  const [confirm,      setConfirm]      = useState(null);
  const [shareDialog,  setShareDialog]  = useState(null);
  const [assignDialog, setAssignDialog] = useState(null);
  const [busy,         setBusy]         = useState(null);
  const [actionMsg,    setActionMsg]    = useState(null);
  const [copiedId,     setCopiedId]     = useState(null);

  const load = useCallback(async () => {
    if (!email) return;
    setLoading(true); setError(null);
    try {
      const res = await api.get(`/api/calendar/list/${encodeURIComponent(email)}`);
      setCalendars(res.data.calendars || []);
    } catch (e) {
      setError(e?.response?.data?.detail || "Could not load your calendars.");
    } finally { setLoading(false); }
  }, [email]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    api.get("/api/user-prefs")
      .then(res => setMembers((res.data || []).filter(m => m.email !== email)))
      .catch(() => {});
  }, [email]);

  const toggle = (id) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id); else next.add(id);
    onChange(next);
  };

  const toErr = (e, fallback) => {
    const msg = e?.response?.data?.detail || fallback;
    return isScopeError(msg) ? "__scope__" : msg;
  };
  const scopeOtherErr = (targetEmail) => {
    const name = members.find(m => m.email === targetEmail)?.display_name || targetEmail;
    return `__scope_other__${name}`;
  };

  const execUnsubscribe = async (cal) => {
    setBusy(cal.id);
    try {
      await api.delete(`/api/calendar/subscription/${encodeURIComponent(email)}/${encodeURIComponent(cal.id)}`);
      const next = new Set(selected); next.delete(cal.id); onChange(next);
      await load();
      setActionMsg(`Unsubscribed from "${cal.summary || cal.id}"`);
    } catch (e) { setError(toErr(e, "Unsubscribe failed.")); }
    finally { setBusy(null); }
  };

  const execCopy = async (cal, targetEmail) => {
    setBusy(cal.id);
    const name = members.find(m => m.email === targetEmail)?.display_name || targetEmail;
    try {
      const res = await api.post(`/api/calendar/subscription/${encodeURIComponent(targetEmail)}`, {
        calendar_id: cal.id,
        owner_email: email,
      });
      if (res.data?.status === "share_required") {
        window.open(res.data.share_url, "_blank");
        setActionMsg(`Opened subscription link for "${cal.summary || cal.id}" — share it with ${name}.`);
      } else {
        setActionMsg(`Copied "${cal.summary || cal.id}" to ${name}`);
      }
    } catch (e) {
      const msg = e?.response?.data?.detail || "Copy failed.";
      setError(isScopeError(msg) ? scopeOtherErr(targetEmail) : msg);
    } finally { setBusy(null); }
  };

  const execTransfer = async (cal, targetEmail) => {
    setBusy(cal.id);
    const name = members.find(m => m.email === targetEmail)?.display_name || targetEmail;
    try {
      await api.post(`/api/calendar/subscription/${encodeURIComponent(targetEmail)}`, { calendar_id: cal.id });
    } catch (e) {
      const msg = e?.response?.data?.detail || "Transfer failed.";
      setError(isScopeError(msg) ? scopeOtherErr(targetEmail) : msg);
      setBusy(null); return;
    }
    try {
      await api.delete(`/api/calendar/subscription/${encodeURIComponent(email)}/${encodeURIComponent(cal.id)}`);
      const next = new Set(selected); next.delete(cal.id); onChange(next);
      await load();
      setActionMsg(`Transferred "${cal.summary || cal.id}" to ${name}`);
    } catch (e) {
      setError(toErr(e, "Calendar was copied but could not be removed from your account."));
    } finally { setBusy(null); }
  };

  const handleUnsubscribe = (cal) => {
    setMenuState(null);
    setConfirm({
      title: "Unsubscribe from calendar?",
      body:  `"${cal.summary || cal.id}" will be removed from your Google Calendar.`,
      onConfirm: () => execUnsubscribe(cal),
    });
  };
  const handleCopy = (targetEmail) => {
    const cal = dialog.cal;
    const member = members.find(m => m.email === targetEmail);
    setDialog(null);
    setConfirm({
      title: "Copy calendar?",
      body:  `"${cal.summary || cal.id}" will be added to ${member?.display_name || targetEmail}'s Google Calendar.`,
      onConfirm: () => execCopy(cal, targetEmail),
    });
  };
  const handleTransfer = (targetEmail) => {
    const cal = dialog.cal;
    const member = members.find(m => m.email === targetEmail);
    setDialog(null);
    setConfirm({
      title: "Transfer calendar?",
      body:  `"${cal.summary || cal.id}" will move to ${member?.display_name || targetEmail} and be removed from yours.`,
      onConfirm: () => execTransfer(cal, targetEmail),
    });
  };

  if (loading) return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
      <CircularProgress size={20} />
      <Typography variant="body2" color="text.secondary">Loading your calendars…</Typography>
    </Box>
  );
  if (error && !calendars.length) return (
    <Alert severity="warning"
      action={<Button size="small" startIcon={<RefreshIcon fontSize="small" />} onClick={load}>Retry</Button>}>
      {error}
    </Alert>
  );
  if (!calendars.length) return null;

  return (
    <Box>
      <Typography variant="body2" fontWeight={500} gutterBottom>Your calendars</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
        Click a calendar name to assign it to dashboard users · Toggle to show/hide · Color swatch overrides your event color
      </Typography>

      {actionMsg && <Alert severity="success" onClose={() => setActionMsg(null)} sx={{ mb: 1 }}>{actionMsg}</Alert>}
      {error === "__scope__" ? (
        <Alert severity="warning" onClose={() => setError(null)} sx={{ mb: 1 }}
          action={<Button size="small" color="inherit" onClick={onReauth}>Sign out &amp; re-authorize</Button>}>
          Your sign-in needs to be renewed to manage calendars.
        </Alert>
      ) : error?.startsWith("__scope_other__") ? (
        <Alert severity="warning" onClose={() => setError(null)} sx={{ mb: 1 }}>
          <strong>{error.slice("__scope_other__".length)}</strong> needs to sign out and sign back in.
        </Alert>
      ) : error ? (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 1 }}>{error}</Alert>
      ) : null}

      <List dense disablePadding sx={{ maxHeight: 360, overflowY: "auto", border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
        {calendars.map(cal => {
          const hasOverride = calColors.has(cal.id);
          const swatchColor = calColors.get(cal.id) || userColor;
          return (
            <ListItem key={cal.id} disableGutters sx={{ px: 1.5, py: 0.5 }}>
              <Box
                sx={{ flex: 1, display: "flex", alignItems: "center", gap: 1, cursor: "pointer",
                      minWidth: 0, pr: 1, borderRadius: 1,
                      "&:hover": { bgcolor: "action.hover" } }}
                onClick={() => setAssignDialog(cal)}
              >
                <Box sx={{ width: 12, height: 12, borderRadius: "50%",
                           bgcolor: cal.backgroundColor || "#1976d2", flexShrink: 0 }} />
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="body2" noWrap>{cal.summary}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {cal.primary ? "Primary calendar" : cal.accessRole}
                  </Typography>
                </Box>
              </Box>
              <Box sx={{ display: "flex", alignItems: "center", flexShrink: 0, gap: 0.25 }}>
                <Tooltip title={hasOverride ? "Custom dashboard color — click to change" : "Click to set a custom dashboard color for this calendar"}>
                  <Box component="label" sx={{ display: "flex", alignItems: "center", cursor: "pointer", position: "relative" }}>
                    <Box sx={{
                      width: 18, height: 18, borderRadius: "3px",
                      bgcolor: swatchColor,
                      border: hasOverride ? "2px solid rgba(0,0,0,0.3)" : "2px dashed rgba(0,0,0,0.25)",
                      flexShrink: 0,
                    }} />
                    <input
                      type="color"
                      value={swatchColor}
                      onChange={e => onColorChange(cal.id, e.target.value)}
                      style={{ position: "absolute", opacity: 0, width: "100%", height: "100%", top: 0, left: 0, cursor: "pointer", padding: 0, border: "none" }}
                    />
                  </Box>
                </Tooltip>
                {hasOverride && (
                  <Tooltip title="Remove color override">
                    <IconButton size="small" sx={{ p: "1px" }} onClick={() => onColorChange(cal.id, null)}>
                      <ClearIcon sx={{ fontSize: 11 }} />
                    </IconButton>
                  </Tooltip>
                )}
                <Tooltip title={copiedId === cal.id ? "Copied!" : calShareUrl(cal.id)}>
                  <IconButton size="small" onClick={e => {
                    e.stopPropagation();
                    navigator.clipboard.writeText(calShareUrl(cal.id));
                    setCopiedId(cal.id);
                    setTimeout(() => setCopiedId(id => id === cal.id ? null : id), 2000);
                  }}>
                    <ContentCopyIcon sx={{ fontSize: 14, color: copiedId === cal.id ? "success.main" : "inherit" }} />
                  </IconButton>
                </Tooltip>
                <Tooltip title="Family sharing">
                  <IconButton size="small" onClick={() => setShareDialog(cal)}>
                    <GroupAddIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
                <Switch
                  size="small"
                  checked={selected.has(cal.id)}
                  onChange={() => toggle(cal.id)}
                  disabled={busy === cal.id}
                />
                <IconButton
                  size="small"
                  disabled={busy === cal.id}
                  onClick={e => setMenuState({ anchor: e.currentTarget, cal })}
                >
                  {busy === cal.id ? <CircularProgress size={16} /> : <MoreVertIcon fontSize="small" />}
                </IconButton>
              </Box>
            </ListItem>
          );
        })}
      </List>
      <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
        {selected.size} of {calendars.length} shown on dashboard
      </Typography>

      <Menu anchorEl={menuState?.anchor} open={!!menuState} onClose={() => setMenuState(null)}>
        <MenuItem onClick={() => { setAssignDialog(menuState.cal); setMenuState(null); }}>
          Assign to dashboard users…
        </MenuItem>
        <MenuItem disabled={!!menuState?.cal?.primary} onClick={() => handleUnsubscribe(menuState.cal)}>
          Unsubscribe from Google Calendar
        </MenuItem>
        {members.length > 0 && [
          <MenuItem key="copy" onClick={() => { setDialog({ type: "copy", cal: menuState.cal }); setMenuState(null); }}>
            Copy to family member…
          </MenuItem>,
          <MenuItem key="transfer" disabled={!!menuState?.cal?.primary} onClick={() => { setDialog({ type: "transfer", cal: menuState.cal }); setMenuState(null); }}>
            Transfer to family member…
          </MenuItem>,
        ]}
      </Menu>

      <Dialog open={!!dialog} onClose={() => setDialog(null)} maxWidth="xs" fullWidth>
        <DialogTitle>{dialog?.type === "copy" ? "Copy calendar to…" : "Transfer calendar to…"}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {dialog?.type === "copy"
              ? `"${dialog?.cal?.summary}" will be added to the selected member's Google Calendar.`
              : `"${dialog?.cal?.summary}" will be moved and removed from yours.`}
          </Typography>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
            {members.map(m => (
              <Button key={m.email} variant="outlined" fullWidth
                onClick={() => dialog?.type === "copy" ? handleCopy(m.email) : handleTransfer(m.email)}
                sx={{ justifyContent: "flex-start", textTransform: "none" }}>
                <Box sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: m.display_color, mr: 1.5, flexShrink: 0 }} />
                {m.display_name || m.email}
              </Button>
            ))}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialog(null)}>Cancel</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={!!confirm} onClose={() => setConfirm(null)} maxWidth="xs" fullWidth>
        <DialogTitle>{confirm?.title}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary">{confirm?.body}</Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirm(null)}>Cancel</Button>
          <Button variant="contained" color="error" onClick={() => { confirm.onConfirm(); setConfirm(null); }}>
            Confirm
          </Button>
        </DialogActions>
      </Dialog>

      <FamilySharingDialog
        cal={shareDialog}
        ownerEmail={email}
        open={!!shareDialog}
        onClose={() => setShareDialog(null)}
      />

      <AssignCalendarDialog
        cal={assignDialog}
        open={!!assignDialog}
        onClose={() => setAssignDialog(null)}
        ownerEmail={email}
        onSaved={onCalendarAssigned}
      />
    </Box>
  );
}

// ── My Account inner ───────────────────────────────────────────────────────────

function MyAccountInner({ hasSecret, onSignIn, onSignOut }) {
  const [user,         setUser]         = useState(getStoredUser);
  const [color,        setColor]        = useState("#1976d2");
  const [hexDraft,     setHexDraft]     = useState("#1976d2");
  const [selected,     setSelected]     = useState(new Set());
  const [calColors,    setCalColors]    = useState(new Map());
  const [saving,       setSaving]       = useState(false);
  const [signing,      setSigning]      = useState(false);
  const [removing,     setRemoving]     = useState(false);
  const [removeDialog, setRemoveDialog] = useState(false);
  const [msg,       setMsg]       = useState(null);
  const [error,     setError]     = useState(null);

  const loadPrefs = useCallback(async (email) => {
    try {
      const res = await api.get(`/api/user-prefs/${encodeURIComponent(email)}`);
      if (res.data.display_color) { setColor(res.data.display_color); setHexDraft(res.data.display_color); }
      const items = res.data.selected_calendars || [];
      setSelected(new Set(items.map(c => c.id)));
      setCalColors(new Map(items.filter(c => c.color).map(c => [c.id, c.color])));
    } catch {}
  }, []);

  useEffect(() => { if (user?.email) loadPrefs(user.email); }, [user, loadPrefs]);

  const handleCode = async (codeResponse) => {
    setSigning(true); setError(null);
    try {
      const res = await api.post("/api/auth/google", { code: codeResponse.code });
      const u = { email: res.data.email, name: res.data.name, picture: res.data.picture };
      setUser(u); storeUser(u); loadPrefs(u.email);
      if (onSignIn) onSignIn({ ...u, role: res.data.role || "user" });
    } catch (e) {
      setError(e?.response?.data?.detail || "Sign-in failed. Make sure the Client Secret is saved in Admin settings.");
    } finally { setSigning(false); }
  };

  const handleToken = async (tokenResponse) => {
    setSigning(true); setError(null);
    try {
      const infoRes = await fetch("https://www.googleapis.com/oauth2/v3/userinfo", {
        headers: { Authorization: `Bearer ${tokenResponse.access_token}` },
      });
      if (!infoRes.ok) throw new Error("Could not get user info from Google.");
      const info = await infoRes.json();
      const u = { email: info.email, name: info.name, picture: info.picture };
      const expiryMs = Date.now() + (tokenResponse.expires_in || 3600) * 1000;
      const calsList = [...selected].map(id => ({ id, color: calColors.get(id) || null }));
      await api.put(`/api/user-prefs/${encodeURIComponent(info.email)}`, {
        display_name: info.name, display_color: color,
        selected_calendars: calsList, access_token: tokenResponse.access_token, token_expiry: expiryMs,
      });
      setUser(u); storeUser(u); loadPrefs(u.email);
      try {
        const prefsRes = await api.get(`/api/user-prefs/${encodeURIComponent(info.email)}`);
        if (onSignIn) onSignIn({ ...u, role: prefsRes.data.role || "user" });
      } catch { if (onSignIn) onSignIn({ ...u, role: "user" }); }
    } catch (e) {
      setError(e?.response?.data?.detail || "Sign-in failed.");
    } finally { setSigning(false); }
  };

  const handleLogout = () => {
    googleLogout(); clearStoredUser();
    setUser(null); setColor("#1976d2"); setSelected(new Set()); setCalColors(new Map()); setMsg(null);
    if (onSignOut) onSignOut();
  };

  const handleRemoveSelf = async () => {
    if (!user) return;
    setRemoving(true); setError(null);
    try {
      await api.delete(`/api/user-prefs/${encodeURIComponent(user.email)}`);
      googleLogout();
      clearStoredUser();
      clearStoredRole();
      setUser(null); setColor("#1976d2"); setSelected(new Set()); setCalColors(new Map());
      if (onSignOut) onSignOut();
    } catch (e) {
      setError(e?.response?.data?.detail || "Could not remove your account. Please try again.");
      setRemoving(false);
    }
  };

  const handleSave = async () => {
    if (!user) return;
    setSaving(true); setMsg(null); setError(null);
    try {
      const calsList = [...selected].map(id => ({ id, color: calColors.get(id) || null }));
      await api.put(`/api/user-prefs/${encodeURIComponent(user.email)}`, {
        display_name: user.name, display_color: color, selected_calendars: calsList,
      });
      setMsg("Your settings have been saved!");
    } catch { setError("Could not save your settings. Please try again."); }
    finally { setSaving(false); }
  };

  if (!user) {
    return (
      <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <Typography variant="body2" color="text.secondary">
          Sign in with your Google account to connect your calendar.
        </Typography>
        {signing ? (
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <CircularProgress size={20} />
            <Typography variant="body2" color="text.secondary">Signing in…</Typography>
          </Box>
        ) : (
          <Box>
            <GoogleSignInButton
              onSuccess={hasSecret ? handleCode : handleToken}
              onError={setError}
              hasSecret={hasSecret}
            />
          </Box>
        )}
        {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
      </Box>
    );
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2.5 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
        <Avatar src={user.picture} sx={{ width: 44, height: 44 }} />
        <Box sx={{ flexGrow: 1 }}>
          <Typography variant="body1" fontWeight={600}>{user.name}</Typography>
          <Typography variant="caption" color="text.secondary">{user.email}</Typography>
        </Box>
        <Button size="small" variant="outlined" color="inherit" startIcon={<LogoutIcon />} onClick={handleLogout}>
          Sign out
        </Button>
      </Box>

      <Divider />

      <Box>
        <Typography variant="body2" fontWeight={500} gutterBottom>Your event color</Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1.5 }}>
          All your calendar events will show in this color on the dashboard.
        </Typography>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, flexWrap: "wrap" }}>
          <input type="color" value={color}
            onChange={e => { setColor(e.target.value); setHexDraft(e.target.value); }}
            style={{ width: 48, height: 36, border: "none", cursor: "pointer", borderRadius: 4 }} />
          <TextField size="small" value={hexDraft}
            onChange={e => {
              const v = e.target.value.startsWith("#") ? e.target.value : "#" + e.target.value;
              setHexDraft(v);
              if (/^#[0-9A-Fa-f]{6}$/.test(v)) setColor(v);
            }}
            inputProps={{ maxLength: 7, style: { fontFamily: "monospace" } }}
            sx={{ width: 100 }} placeholder="#1976d2" />
          <Chip label={`${user.name.split(" ")[0]}'s events`} size="small"
            sx={{ bgcolor: /^#[0-9A-Fa-f]{6}$/.test(color) ? color : "#1976d2", color: "#fff", fontWeight: 600 }} />
        </Box>
      </Box>

      <Divider />

      <CalendarPicker
        email={user.email}
        selected={selected}
        onChange={setSelected}
        calColors={calColors}
        onColorChange={(id, color) => setCalColors(prev => {
          const next = new Map(prev);
          if (color) next.set(id, color); else next.delete(id);
          return next;
        })}
        userColor={color}
        onReauth={handleLogout}
        onCalendarAssigned={() => loadPrefs(user.email)}
      />

      {msg   && <Alert severity="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {error && <Alert severity="error"   onClose={() => setError(null)}>{error}</Alert>}

      <Box>
        <Button variant="contained" size="large"
          startIcon={saving ? <CircularProgress size={18} color="inherit" /> : <SaveIcon />}
          onClick={handleSave} disabled={saving}>
          Save My Settings
        </Button>
      </Box>

      {getStoredRole() !== "owner" && (
        <>
          <Divider sx={{ mt: 1 }} />
          <Box>
            <Typography variant="body2" fontWeight={500} color="text.secondary" gutterBottom>
              Danger Zone
            </Typography>
            <Button
              variant="outlined"
              color="error"
              size="small"
              startIcon={removing ? <CircularProgress size={16} color="inherit" /> : <DeleteIcon />}
              onClick={() => setRemoveDialog(true)}
              disabled={removing}
            >
              Remove My Account
            </Button>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
              Removes you from the dashboard. Your Google Calendar is not affected.
            </Typography>
          </Box>
        </>
      )}

      <Dialog open={removeDialog} onClose={() => setRemoveDialog(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Remove your account?</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary">
            This will remove <strong>{user?.name}</strong> from the dashboard and sign you out.
            Your Google account and calendars are not affected — you can re-add yourself at any time by signing in again.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRemoveDialog(false)}>Cancel</Button>
          <Button
            color="error"
            variant="contained"
            disabled={removing}
            startIcon={removing ? <CircularProgress size={16} color="inherit" /> : null}
            onClick={() => { setRemoveDialog(false); handleRemoveSelf(); }}
          >
            Remove My Account
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

// ── Shell ──────────────────────────────────────────────────────────────────────

function MyAccount({ onSignIn, onSignOut }) {
  const [clientId,  setClientId]  = useState(null);
  const [hasSecret, setHasSecret] = useState(false);

  useEffect(() => {
    api.get("/api/settings/oauth")
      .then(res => { setClientId(res.data.client_id || ""); setHasSecret(!!res.data.configured); })
      .catch(() => setClientId(""));
  }, []);

  return (
    <Section icon={<AccountCircleIcon />} title="My Account">
      {clientId === null ? (
        <CircularProgress size={24} />
      ) : !clientId ? (
        <Alert severity="warning">
          This dashboard hasn't been configured yet. Ask the person who set it up to visit the{" "}
          <a href="/admin" style={{ color: "inherit" }}>admin page</a> and add the Google credentials.
        </Alert>
      ) : (
        <MyAccountInner hasSecret={hasSecret} onSignIn={onSignIn} onSignOut={onSignOut} />
      )}
    </Section>
  );
}

// ── Family Calendars — subscribe to other users' dashboard calendars ─────────

function FamilyCalendars() {
  const currentUser = getStoredUser();
  const [members,  setMembers]  = useState([]);   // other users with their selected_calendars
  const [calNames, setCalNames] = useState({});   // email → { calId → summary }
  const [loading,  setLoading]  = useState(false);

  useEffect(() => {
    setLoading(true);
    api.get("/api/user-prefs").then(async res => {
      const all = (res.data || []).filter(m =>
        m.email !== currentUser?.email && (m.selected_calendars || []).length > 0
      );
      setMembers(all);

      const nameMap = {};
      await Promise.allSettled(all.map(async m => {
        try {
          const r = await api.get(`/api/calendar/list/${encodeURIComponent(m.email)}`);
          nameMap[m.email] = {};
          for (const cal of (r.data.calendars || [])) {
            nameMap[m.email][cal.id] = cal.summaryOverride || cal.summary || cal.id;
          }
        } catch {}
      }));
      setCalNames(nameMap);
    }).catch(() => {}).finally(() => setLoading(false));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Section icon={<GroupAddIcon />} title="Family Calendars">
      <Typography variant="body2" color="text.secondary">
        Calendars shown on the dashboard by other family members. Click <strong>Subscribe</strong> to add one to your own Google Calendar.
      </Typography>
      {loading ? (
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, py: 1 }}>
          <CircularProgress size={18} />
          <Typography variant="body2" color="text.secondary">Loading…</Typography>
        </Box>
      ) : !members.length ? (
        <Typography variant="body2" color="text.secondary">
          No other family members have connected their calendars yet.
        </Typography>
      ) : (
        members.map(m => (
          <Box key={m.email}>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 0.5 }}>
              <Box sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: m.display_color, flexShrink: 0 }} />
              <Typography variant="body2" fontWeight={600}>{m.display_name || m.email}</Typography>
            </Box>
            <List dense disablePadding sx={{ pl: 2.5 }}>
              {(m.selected_calendars || []).map(c => {
                const name = calNames[m.email]?.[c.id] || c.id;
                const color = c.color || m.display_color || "#1976d2";
                return (
                  <ListItem key={c.id} disableGutters sx={{ py: 0.25, gap: 1 }}>
                    <Box sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: color, flexShrink: 0 }} />
                    <ListItemText
                      primary={name}
                      primaryTypographyProps={{ variant: "body2", noWrap: true, sx: { flex: 1, minWidth: 0 } }}
                    />
                    <Button
                      size="small"
                      variant="outlined"
                      startIcon={<AddIcon />}
                      href={googleCalSubscribeUrl(c.id)}
                      target="_blank"
                      rel="noopener noreferrer"
                      component="a"
                      sx={{ flexShrink: 0, fontSize: "0.7rem", py: 0.25, px: 1 }}
                    >
                      Subscribe
                    </Button>
                  </ListItem>
                );
              })}
            </List>
          </Box>
        ))
      )}
    </Section>
  );
}

// ── Family Members ────────────────────────────────────────────────────────────

function FamilyMembers({ currentUser, currentRole }) {
  const [members,       setMembers]       = useState([]);
  const [busy,          setBusy]          = useState({});
  const [error,         setError]         = useState(null);
  const [confirmDialog, setConfirmDialog] = useState(null);
  const [actionMenu,    setActionMenu]    = useState(null);

  const load = useCallback(async () => {
    try { const res = await api.get("/api/user-prefs"); setMembers(res.data); } catch {}
  }, []);

  useEffect(() => { load(); }, [load]);

  const setBusyFor = (email, val) => setBusy(p => ({ ...p, [email]: val }));

  const patchRole = async (email, role) => {
    setBusyFor(email, true);
    try {
      await api.patch(`/api/user-prefs/${encodeURIComponent(email)}/role`, {
        role, requester: currentUser?.email,
      });
      setMembers(prev => prev.map(m => m.email === email ? { ...m, role } : m));
    } catch (e) { setError(e?.response?.data?.detail || "Role change failed."); }
    finally { setBusyFor(email, false); }
  };

  const patchBlocked = async (email, blocked) => {
    setBusyFor(email, true);
    try {
      await api.patch(`/api/user-prefs/${encodeURIComponent(email)}/blocked`, {
        blocked, requester: currentUser?.email,
      });
      setMembers(prev => prev.map(m => m.email === email ? { ...m, blocked } : m));
    } catch (e) { setError(e?.response?.data?.detail || "Action failed."); }
    finally { setBusyFor(email, false); }
  };

  const handleRemove = async (email) => {
    setBusyFor(email, true);
    try {
      await api.delete(`/api/user-prefs/${encodeURIComponent(email)}`);
      if (getStoredUser()?.email === email) clearStoredUser();
      setMembers(prev => prev.filter(m => m.email !== email));
    } catch (e) { setError(e?.response?.data?.detail || `Could not remove ${email}.`); }
    finally { setBusyFor(email, false); }
  };

  const isAdminOrOwner = currentRole === "admin" || currentRole === "owner";
  const isOwner        = currentRole === "owner";

  return (
    <Section icon={<CalendarMonthIcon />} title="Family Members">
      <Typography variant="body2" color="text.secondary">
        Everyone connected to this dashboard.
        {isAdminOrOwner && " Use the menu on each member to manage roles and access."}
      </Typography>
      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
      {!members.length && (
        <Typography variant="body2" color="text.secondary">No family members have signed in yet.</Typography>
      )}
      <List dense disablePadding sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
        {members.map(m => {
          const isSelf     = m.email === currentUser?.email;
          const isOwnerTarget = m.role === "owner";
          const canPromote = isOwner && m.role === "user"  && !m.blocked;
          const canDemote  = isOwner && m.role === "admin";
          const canBlock   = isAdminOrOwner && !isOwnerTarget;
          const canDelete  = isAdminOrOwner && !isOwnerTarget && !isSelf;
          const hasActions = canPromote || canDemote || canBlock || canDelete;
          return (
            <ListItem key={m.email} disableGutters sx={{ gap: 1, py: 0.75, flexWrap: "wrap" }}>
              <Box sx={{ width: 12, height: 12, borderRadius: "50%", bgcolor: m.display_color, flexShrink: 0 }} />
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, flexWrap: "wrap" }}>
                  <Typography variant="body2" fontWeight={500} sx={{ opacity: m.blocked ? 0.55 : 1 }}>
                    {m.display_name || m.email}
                  </Typography>
                  {m.blocked && (
                    <Chip size="small" label="blocked" color="error" variant="outlined"
                      sx={{ height: 18, fontSize: "0.65rem" }} />
                  )}
                  {isSelf && (
                    <Chip size="small" label="you" variant="outlined"
                      sx={{ height: 18, fontSize: "0.65rem" }} />
                  )}
                </Box>
                <Typography variant="caption" color="text.secondary">{m.email}</Typography>
              </Box>
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, flexShrink: 0 }}>
                <Chip size="small"
                  label={m.role || "user"}
                  color={m.role === "owner" ? "primary" : m.role === "admin" ? "secondary" : "default"}
                  variant={m.role === "owner" ? "filled" : m.role === "admin" ? "filled" : "outlined"}
                  icon={m.role === "admin" || m.role === "owner" ? <AdminPanelSettingsIcon /> : undefined}
                />
                <Chip size="small"
                  label={m.has_refresh ? "Permanent" : m.has_token ? "Temporary" : "No token"}
                  color={m.has_refresh ? "success" : m.has_token ? "warning" : "default"}
                  variant="outlined"
                />
                {busy[m.email] && <CircularProgress size={18} />}
                {!busy[m.email] && hasActions && (
                  <IconButton size="small"
                    onClick={e => setActionMenu({ anchor: e.currentTarget, member: m })}>
                    <MoreVertIcon fontSize="small" />
                  </IconButton>
                )}
              </Box>
            </ListItem>
          );
        })}
      </List>
      <Typography variant="caption" color="text.secondary">
        "Permanent" = sign-in auto-renews. "Temporary" = re-sign-in needed after ~1 hour.
      </Typography>

      {/* Action menu */}
      <Menu
        anchorEl={actionMenu?.anchor}
        open={!!actionMenu}
        onClose={() => setActionMenu(null)}
      >
        {actionMenu && (() => {
          const m = actionMenu.member;
          const canPromote = isOwner && m.role === "user"  && !m.blocked;
          const canDemote  = isOwner && m.role === "admin";
          const canBlock   = isAdminOrOwner && m.role !== "owner";
          const canDelete  = isAdminOrOwner && m.role !== "owner" && m.email !== currentUser?.email;
          return [
            canPromote && (
              <MenuItem key="promote" onClick={() => { patchRole(m.email, "admin"); setActionMenu(null); }}>
                Promote to Admin
              </MenuItem>
            ),
            canDemote && (
              <MenuItem key="demote" onClick={() => { patchRole(m.email, "user"); setActionMenu(null); }}>
                Demote to User
              </MenuItem>
            ),
            canBlock && (
              <MenuItem key="block" onClick={() => { patchBlocked(m.email, !m.blocked); setActionMenu(null); }}>
                {m.blocked ? "Unblock" : "Block"}
              </MenuItem>
            ),
            canDelete && (
              <MenuItem key="remove" sx={{ color: "error.main" }}
                onClick={() => { setConfirmDialog({ email: m.email, name: m.display_name || m.email }); setActionMenu(null); }}>
                Remove from Dashboard
              </MenuItem>
            ),
          ].filter(Boolean);
        })()}
      </Menu>

      <Dialog open={!!confirmDialog} onClose={() => setConfirmDialog(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Remove {confirmDialog?.name}?</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary">
            This removes them from the dashboard. Their Google calendars are not affected.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmDialog(null)}>Cancel</Button>
          <Button color="error" variant="contained"
            onClick={() => { handleRemove(confirmDialog.email); setConfirmDialog(null); }}>
            Remove
          </Button>
        </DialogActions>
      </Dialog>
    </Section>
  );
}

// ── Weather Location ───────────────────────────────────────────────────────────

function WeatherLocation() {
  const [location, setLocation] = useState("");
  const [units,    setUnits]    = useState("imperial");
  const [saving,   setSaving]   = useState(false);
  const [msg,      setMsg]      = useState(null);
  const [error,    setError]    = useState(null);

  useEffect(() => {
    api.get("/api/settings/weather").then(res => {
      setLocation(res.data.location || "");
      setUnits(res.data.units || "imperial");
    }).catch(() => {});
  }, []);

  const handleSave = async () => {
    setSaving(true); setMsg(null); setError(null);
    try {
      await api.put("/api/settings/weather", { location, units });
      setMsg("Weather location saved.");
    } catch {
      setError("Failed to save.");
    } finally { setSaving(false); }
  };

  return (
    <Section icon={<CloudIcon />} title="Weather Location">
      <Typography variant="body2" color="text.secondary">
        Enter your city and state (e.g. <strong>Moraga, CA</strong>) or ZIP code (e.g. <strong>94556</strong>).
        The display will show the location label you enter here.
      </Typography>
      <TextField
        label="City, State or ZIP code" size="small" fullWidth
        value={location} onChange={e => setLocation(e.target.value)}
        placeholder="e.g. Moraga, CA  or  94556"
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
          onClick={handleSave} disabled={saving || !location.trim()}>
          Save Location
        </Button>
      </Box>
    </Section>
  );
}

// ── RSS Feeds ──────────────────────────────────────────────────────────────────

function RssSettings() {
  const [feeds,      setFeeds]      = useState([{ url: "", label: "" }]);
  const [mode,       setMode]       = useState("shuffle");
  const [dadJokes,   setDadJokes]   = useState(true);
  const [hackerNews, setHackerNews] = useState(true);
  const [saving,     setSaving]     = useState(false);
  const [msg,        setMsg]        = useState(null);
  const [error,      setError]      = useState(null);

  useEffect(() => {
    api.get("/api/settings/rss").then(res => {
      const loaded = res.data.feeds || [];
      setFeeds(loaded.length > 0 ? loaded : [{ url: "", label: "" }]);
      setMode(res.data.mode || "shuffle");
      setDadJokes(res.data.dad_jokes  !== false);
      setHackerNews(res.data.hacker_news !== false);
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
      const res = await api.put("/api/settings/rss", { feeds, mode, dad_jokes: dadJokes, hacker_news: hackerNews });
      setMsg(`Saved ${res.data.count} feed${res.data.count !== 1 ? "s" : ""}.`);
    } catch {
      setError("Failed to save.");
    } finally { setSaving(false); }
  };

  return (
    <Section icon={<RssFeedIcon />} title="RSS News Feeds" defaultExpanded={false}>
      <Typography variant="body2" color="text.secondary">
        Headlines rotate in the news ticker at the top of the dashboard.
        The label is optional and appears as a source tag.
      </Typography>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
        <Typography variant="body2" color="text.secondary">Display mode:</Typography>
        <ToggleButtonGroup size="small" exclusive value={mode}
          onChange={(_, v) => { if (v) setMode(v); }}>
          <ToggleButton value="shuffle">Shuffle all feeds</ToggleButton>
          <ToggleButton value="rotate">Rotate feed by feed</ToggleButton>
        </ToggleButtonGroup>
      </Box>
      <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 0.5 }}>Built-in sources:</Typography>
        <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", maxWidth: 360 }}>
          <Box>
            <Typography variant="body2" fontWeight={500}>Hacker News</Typography>
            <Typography variant="caption" color="text.secondary">Top tech &amp; science headlines</Typography>
          </Box>
          <Switch checked={hackerNews} onChange={e => setHackerNews(e.target.checked)} size="small" />
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", maxWidth: 360 }}>
          <Box>
            <Typography variant="body2" fontWeight={500}>Dad Jokes</Typography>
            <Typography variant="caption" color="text.secondary">Random jokes via icanhazdadjoke.com</Typography>
          </Box>
          <Switch checked={dadJokes} onChange={e => setDadJokes(e.target.checked)} size="small" />
        </Box>
      </Box>
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
                label="Label" size="small" sx={{ flex: 1 }}
                value={feed.label} onChange={e => updateFeed(i, "label", e.target.value)}
                placeholder="e.g. AP"
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

// ── Pi Display ─────────────────────────────────────────────────────────────────

function PiDisplay() {
  const [theme,       setTheme]       = useState("auto");
  const [view,        setView]        = useState("rolling");
  const [weatherView, setWeatherView] = useState("daily");
  const [saving,      setSaving]      = useState(false);
  const [msg,         setMsg]         = useState(null);
  const [error,       setError]       = useState(null);

  useEffect(() => {
    api.get("/api/settings/display").then(res => {
      setTheme(res.data.theme              || "auto");
      setView(res.data.view                || "rolling");
      setWeatherView(res.data.weather_view || "daily");
    }).catch(() => {});
  }, []);

  const handleSave = async () => {
    setSaving(true); setMsg(null); setError(null);
    try {
      await api.put("/api/settings/display", { theme, view, weather_view: weatherView });
      setMsg("Saved — display updates within 30 seconds.");
    } catch {
      setError("Failed to save.");
    } finally { setSaving(false); }
  };

  return (
    <Section icon={<TvIcon />} title="Pi Display">
      <Typography variant="body2" color="text.secondary">
        Controls what's shown on the kiosk screen. Changes are picked up automatically.
      </Typography>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
        <Typography variant="body2" color="text.secondary" sx={{ minWidth: 80 }}>Theme:</Typography>
        <ToggleButtonGroup size="small" exclusive value={theme} onChange={(_, v) => { if (v) setTheme(v); }}>
          <ToggleButton value="auto">Auto</ToggleButton>
          <ToggleButton value="light">Light</ToggleButton>
          <ToggleButton value="dark">Dark</ToggleButton>
        </ToggleButtonGroup>
      </Box>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
        <Typography variant="body2" color="text.secondary" sx={{ minWidth: 80 }}>Calendar:</Typography>
        <ToggleButtonGroup size="small" exclusive value={view} onChange={(_, v) => { if (v) setView(v); }}>
          <ToggleButton value="rolling">Rolling Week</ToggleButton>
          <ToggleButton value="week">Mon–Sun</ToggleButton>
          <ToggleButton value="2week">2 Week</ToggleButton>
          <ToggleButton value="month">Month</ToggleButton>
        </ToggleButtonGroup>
      </Box>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
        <Typography variant="body2" color="text.secondary" sx={{ minWidth: 80 }}>Weather:</Typography>
        <ToggleButtonGroup size="small" exclusive value={weatherView} onChange={(_, v) => { if (v) setWeatherView(v); }}>
          <ToggleButton value="daily">Forecast Days</ToggleButton>
          <ToggleButton value="hourly">Hourly</ToggleButton>
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

// ── Restart Services ───────────────────────────────────────────────────────────

function RestartServices() {
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
          ? "Backend restarting — page will reconnect in a few seconds."
          : "Pi display restarting."
      );
    } catch {
      setError("Restart command failed.");
    } finally { setB(false); }
  };

  return (
    <Section icon={<ReplayIcon />} title="Restart Services" defaultExpanded={false}>
      <Typography variant="body2" color="text.secondary">
        Restart the backend API or the Pi display process without rebooting.
        The backend briefly drops and reconnects automatically.
      </Typography>
      {msg   && <Alert severity="info"  onClose={() => setMsg(null)}>{msg}</Alert>}
      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
      <Box sx={{ display: "flex", gap: 2, flexWrap: "wrap" }}>
        <Button variant="outlined"
          startIcon={backendBusy ? <CircularProgress size={16} color="inherit" /> : <ReplayIcon />}
          onClick={() => restart("backend")} disabled={backendBusy || displayBusy}>
          Restart Backend
        </Button>
        <Button variant="outlined"
          startIcon={displayBusy ? <CircularProgress size={16} color="inherit" /> : <TvIcon />}
          onClick={() => restart("display")} disabled={backendBusy || displayBusy}>
          Restart Display
        </Button>
      </Box>
    </Section>
  );
}

// ── OAuth Credentials (owner-only) ────────────────────────────────────────────

function OAuthSettings() {
  const [clientId,     setClientId]     = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [showSecret,   setShowSecret]   = useState(false);
  const [saving,       setSaving]       = useState(false);
  const [msg,          setMsg]          = useState(null);
  const [error,        setError]        = useState(null);
  const MASKED = "••••••••";

  useEffect(() => {
    api.get("/api/settings/oauth").then(res => {
      setClientId(res.data.client_id || "");
      setClientSecret(res.data.configured ? MASKED : "");
    }).catch(() => {});
  }, []);

  const handleSave = async () => {
    setSaving(true); setMsg(null); setError(null);
    try {
      await api.put("/api/settings/oauth", { client_id: clientId, client_secret: clientSecret });
      setMsg("Saved. Restart the backend for changes to take effect.");
      if (clientSecret && clientSecret !== MASKED) setClientSecret(MASKED);
    } catch { setError("Failed to save."); }
    finally { setSaving(false); }
  };

  return (
    <Section icon={<LockIcon />} title="OAuth / Google Credentials">
      <Typography variant="body2" color="text.secondary">
        Found in <strong>Google Cloud Console → APIs &amp; Services → Credentials</strong>.
        Required for family members to sign in. This is a one-time setup.
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

      {/* ── Google Cloud Setup Guide ─────────────────────────────────────────── */}
      <Divider sx={{ mt: 1 }} />
      <Typography variant="body2" fontWeight={600} gutterBottom>
        How to set up Google OAuth — step by step
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Create a free Google Cloud project, enable the Calendar API, and generate the Client ID and
        Secret above. This is a one-time process that takes about 10 minutes.
      </Typography>

      {[
        {
          n: 1,
          title: "Create a Google Cloud Project",
          href: "https://console.cloud.google.com/projectcreate",
          btnLabel: "Create a New Project",
          content: (
            <Typography variant="body2" color="text.secondary">
              Go to the Google Cloud Console and create a new project (or select an existing one from
              the top-left dropdown). Name it anything — e.g. <em>Family Dashboard</em>.
            </Typography>
          ),
        },
        {
          n: 2,
          title: "Enable the Google Calendar API",
          href: "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com",
          btnLabel: "Enable Calendar API",
          content: (
            <Typography variant="body2" color="text.secondary">
              Inside your project, go to <strong>APIs &amp; Services → Library</strong>. Search for
              &ldquo;Google Calendar API&rdquo; and click <strong>Enable</strong>.
            </Typography>
          ),
        },
        {
          n: 3,
          title: "Configure the OAuth Consent Screen",
          href: "https://console.cloud.google.com/apis/credentials/consent",
          btnLabel: "OAuth Consent Screen",
          content: (
            <Typography variant="body2" color="text.secondary">
              Go to <strong>APIs &amp; Services → OAuth consent screen</strong>. Select{" "}
              <strong>External</strong> user type (required for personal Gmail accounts). Fill in:
              App name (<em>Family Dashboard</em>), user support email, and developer contact email.
              Click <strong>Save and Continue</strong> to reach the Scopes step.
            </Typography>
          ),
        },
        {
          n: 4,
          title: "Add Required Scopes",
          href: null,
          content: (
            <Box>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 0.75 }}>
                On the Scopes step click <strong>Add or Remove Scopes</strong>. Search for and enable
                all four, then click <strong>Update → Save and Continue</strong>:
              </Typography>
              <Box sx={{ display: "flex", flexDirection: "column", gap: 0.4, mb: 0.75 }}>
                {[
                  ["openid",               "OpenID Connect authentication"],
                  [".../userinfo.email",   "Read the user's email address"],
                  [".../userinfo.profile", "Read the user's public profile"],
                  [".../auth/calendar",    "Full Calendar access — read events & manage subscriptions"],
                ].map(([s, d]) => (
                  <Box key={s} sx={{ display: "flex", gap: 1, alignItems: "center" }}>
                    <Typography sx={{
                      fontFamily: "monospace", bgcolor: "action.hover",
                      px: 0.75, py: 0.1, borderRadius: 0.5, fontSize: "0.73rem", flexShrink: 0,
                    }}>
                      {s}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">{d}</Typography>
                  </Box>
                ))}
              </Box>
            </Box>
          ),
        },
        {
          n: 5,
          title: "Add Test Users",
          href: "https://console.cloud.google.com/apis/credentials/consent",
          btnLabel: "OAuth Consent Screen",
          content: (
            <Typography variant="body2" color="text.secondary">
              On the <strong>Test Users</strong> step click <strong>+ Add Users</strong> and enter each
              family member&rsquo;s Gmail address. Only listed users can sign in while the app is in
              Testing mode. Alternatively, finish the wizard and then click <strong>Publish App</strong>
              on the consent screen — the app remains unverified but works perfectly for personal use.
            </Typography>
          ),
        },
        {
          n: 6,
          title: "Create OAuth 2.0 Credentials",
          href: "https://console.cloud.google.com/apis/credentials",
          btnLabel: "Credentials Page",
          content: (
            <Box>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 0.5 }}>
                Go to <strong>Credentials → + Create Credentials → OAuth 2.0 Client ID</strong>. Set:
              </Typography>
              <Box component="ul" sx={{ m: 0, pl: 2.5, mb: 0.5 }}>
                {[
                  <><strong>Application type:</strong> Web application</>,
                  <><strong>Name:</strong> Family Dashboard (or anything)</>,
                  <><strong>Authorized JavaScript origins:</strong> your Pi&rsquo;s URL — set it in the{" "}
                    <strong>FQDN Setup</strong> tab below</>,
                ].map((item, i) => (
                  <Box key={i} component="li">
                    <Typography variant="body2" color="text.secondary">{item}</Typography>
                  </Box>
                ))}
              </Box>
              <Typography variant="body2" color="text.secondary">
                After clicking <strong>Create</strong>, a dialog shows your <strong>Client ID</strong>{" "}
                and <strong>Client Secret</strong>. Copy both and paste them into the fields at the top
                of this page. No Redirect URIs are needed — this dashboard uses a popup flow.
              </Typography>
            </Box>
          ),
        },
      ].map(({ n, title, href, btnLabel, content }) => (
        <Box key={n} sx={{ display: "flex", gap: 1.5 }}>
          <Box sx={{
            width: 24, height: 24, minWidth: 24, borderRadius: "50%",
            bgcolor: "primary.main", color: "#fff",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: "0.72rem", fontWeight: 700, mt: 0.2, flexShrink: 0,
          }}>
            {n}
          </Box>
          <Box sx={{ flex: 1, display: "flex", flexDirection: "column", gap: 0.5 }}>
            <Typography variant="body2" fontWeight={600}>{title}</Typography>
            {content}
            {href && (
              <Box>
                <Button size="small" variant="outlined" component="a" href={href}
                  target="_blank" rel="noopener noreferrer"
                  endIcon={<OpenInNewIcon sx={{ fontSize: "0.8rem !important" }} />}
                  sx={{ textTransform: "none", fontSize: "0.78rem", py: 0.25 }}>
                  {btnLabel}
                </Button>
              </Box>
            )}
          </Box>
        </Box>
      ))}
    </Section>
  );
}

// ── FQDN Setup (owner-only) ───────────────────────────────────────────────────

function TunnelSettings() {
  const [token,       setToken]       = useState("");
  const [showToken,   setShowToken]   = useState(false);
  const [configured,  setConfigured]  = useState(false);
  const [active,      setActive]      = useState(false);
  const [saving,      setSaving]      = useState(false);
  const [controlling, setControlling] = useState(false);
  const [msg,         setMsg]         = useState(null);
  const [error,       setError]       = useState(null);
  const [fqdn,        setFqdn]        = useState("");
  const [fqdnSaving,  setFqdnSaving]  = useState(false);
  const [fqdnMsg,     setFqdnMsg]     = useState(null);
  const [detecting,   setDetecting]   = useState(false);
  const [detectMsg,   setDetectMsg]   = useState(null);   // { type, text }
  const MASKED = "••••••••";

  const load = useCallback(async () => {
    try {
      const res = await api.get("/api/settings/tunnel");
      setToken(res.data.configured ? MASKED : "");
      setConfigured(res.data.configured);
      setActive(res.data.active);
    } catch {}
  }, []);

  useEffect(() => {
    api.get("/api/settings/display")
      .then(res => setFqdn(res.data.custom_fqdn || ""))
      .catch(() => {});
  }, []);

  const handleFqdnSave = async () => {
    setFqdnSaving(true); setFqdnMsg(null);
    try {
      await api.put("/api/settings/display", { custom_fqdn: fqdn });
      setFqdnMsg("Saved.");
    } catch { setFqdnMsg("error"); }
    finally { setFqdnSaving(false); }
  };

  const handleDetect = async () => {
    setDetecting(true); setDetectMsg(null);
    try {
      const res = await api.get("/api/settings/fqdn/detect");
      const { tailscale, cloudflare_tunnel_id } = res.data;
      if (tailscale) {
        setFqdn(tailscale);
        setDetectMsg({ type: "success",
          text: `Tailscale hostname detected: ${tailscale}. Click Save to apply.` });
      } else if (cloudflare_tunnel_id) {
        setDetectMsg({ type: "info",
          text: "Cloudflare tunnel is configured. Enter your public hostname from the Cloudflare dashboard (e.g. dashboard.yourdomain.com)." });
      } else {
        setDetectMsg({ type: "warning",
          text: "No hostname detected automatically. Neither Tailscale nor a Cloudflare tunnel is configured on this Pi." });
      }
    } catch {
      setDetectMsg({ type: "error", text: "Detection failed — could not reach the Pi." });
    } finally {
      setDetecting(false);
    }
  };

  useEffect(() => { load(); }, [load]);

  const handleSave = async () => {
    setSaving(true); setMsg(null); setError(null);
    try {
      await api.put("/api/settings/tunnel", { token });
      setMsg("Token saved. Use the controls below to start the tunnel.");
      if (token && token !== MASKED) setToken(MASKED);
      setConfigured(true);
    } catch { setError("Failed to save token."); }
    finally { setSaving(false); }
  };

  const handleControl = async (action) => {
    setControlling(true); setMsg(null); setError(null);
    try {
      await api.post(`/api/settings/tunnel/${action}`);
      const labels = { start: "Starting", stop: "Stopping", restart: "Restarting" };
      setMsg(`${labels[action]} cloudflared — status will update in a few seconds.`);
      setTimeout(() => load(), 4000);
    } catch { setError(`Failed to ${action} tunnel.`); }
    finally { setControlling(false); }
  };

  const handleClear = async () => {
    setSaving(true); setMsg(null); setError(null);
    try {
      await api.post("/api/settings/tunnel/stop");
      await api.put("/api/settings/tunnel", { clear: true });
      setToken(""); setConfigured(false); setActive(false);
      setMsg("Token cleared and tunnel stopped.");
    } catch { setError("Failed to clear token."); }
    finally { setSaving(false); }
  };

  return (
    <Section icon={<RouterIcon />} title="FQDN Setup">
      <Typography variant="body2" color="text.secondary">
        Google OAuth requires a real domain name (FQDN) — raw IP addresses are not accepted as
        Authorized JavaScript Origins in Google Cloud Console. Choose the option that fits your
        setup, then add the resulting URL to your OAuth client credentials.
      </Typography>

      {/* ── Option 1 — Tailscale ─────────────────────────────────────────────── */}
      <Accordion disableGutters elevation={0} sx={{
        border: "1px solid", borderColor: "divider", borderRadius: "6px !important",
        "&:before": { display: "none" },
      }}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />} sx={{ minHeight: 44, "& .MuiAccordionSummary-content": { my: 0.75 } }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Chip size="small" label="Free" color="success" sx={{ fontSize: "0.65rem", height: 18 }} />
            <Typography variant="body2" fontWeight={600}>Option 1 — Tailscale (no custom domain needed)</Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails sx={{ pt: 0, pb: 2 }}>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1.25 }}>
            Tailscale gives every device a stable <strong>machine.tail-xxxxx.ts.net</strong> HTTPS
            hostname for free — no port-forwarding, no firewall changes, no domain required.
          </Typography>
          <Box component="ol" sx={{ m: 0, pl: 2.5, display: "flex", flexDirection: "column", gap: 1 }}>
            {[
              { text: "SSH into your Pi and install Tailscale:", code: "curl -fsSL https://tailscale.com/install.sh | sh" },
              { text: "Authenticate (a URL is printed to the console):", code: "sudo tailscale up" },
              { text: "Open the printed URL in a browser and sign in. Tailscale accounts are free at tailscale.com." },
              { text: "Open tailscale.com/admin/machines and find your Pi. Copy its MagicDNS hostname — e.g.:", code: "pi-name.tail-xxxxx.ts.net" },
              { text: "In Google Cloud Console → Credentials, edit your OAuth 2.0 client. Under Authorized JavaScript origins → Add URI:", code: "https://pi-name.tail-xxxxx.ts.net" },
              { text: "Click Save (Google takes a few minutes to propagate)." },
              { text: "In the Custom Hostname field below, enter:", code: "pi-name.tail-xxxxx.ts.net" },
              { text: "Access the dashboard at:", code: "https://pi-name.tail-xxxxx.ts.net/settings" },
            ].map((item, i) => (
              <Box component="li" key={i}>
                <Typography variant="body2">{item.text}</Typography>
                {item.code && (
                  <Typography variant="body2" sx={{
                    fontFamily: "monospace", bgcolor: "action.hover",
                    px: 1, py: 0.25, borderRadius: 0.5, mt: 0.5,
                    wordBreak: "break-all", fontSize: "0.78rem",
                  }}>
                    {item.code}
                  </Typography>
                )}
              </Box>
            ))}
          </Box>
        </AccordionDetails>
      </Accordion>

      {/* ── Option 2 — Cloudflare Tunnel ─────────────────────────────────────── */}
      <Accordion disableGutters elevation={0} sx={{
        border: "1px solid", borderColor: "divider", borderRadius: "6px !important",
        "&:before": { display: "none" },
      }}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />} sx={{ minHeight: 44, "& .MuiAccordionSummary-content": { my: 0.75 } }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Chip size="small" label="Custom domain" color="info" sx={{ fontSize: "0.65rem", height: 18 }} />
            <Typography variant="body2" fontWeight={600}>Option 2 — Cloudflare Tunnel (requires a domain on Cloudflare)</Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails sx={{ pt: 0, pb: 2 }}>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1.25 }}>
            <strong>cloudflared is pre-installed</strong> on this Pi. Connecting it to Cloudflare
            exposes your dashboard at a custom subdomain with automatic HTTPS — no port-forwarding
            or open firewall ports needed.
          </Typography>
          <Box component="ol" sx={{ m: 0, pl: 2.5, display: "flex", flexDirection: "column", gap: 1 }}>
            {[
              { text: "In your Cloudflare dashboard → Zero Trust → Networks → Tunnels → Create a tunnel. Choose Cloudflared, give it a name, and copy the tunnel token." },
              { text: "Paste the token into the Cloudflare Tunnel Service form below, click Save Token, then Start Tunnel." },
              { text: "Back in Cloudflare → your tunnel → Public Hostname → Add a hostname:", code: "Subdomain: dashboard  (or your choice)\nDomain:    yourdomain.com\nService:   HTTP  →  localhost:80" },
              { text: "In Google Cloud Console → Credentials, edit your OAuth client. Under Authorized JavaScript origins → Add URI:", code: "https://dashboard.yourdomain.com" },
              { text: "Click Save (Google takes a few minutes to propagate)." },
              { text: "In the Custom Hostname field below, enter:", code: "dashboard.yourdomain.com" },
              { text: "Access the dashboard at:", code: "https://dashboard.yourdomain.com/settings" },
            ].map((item, i) => (
              <Box component="li" key={i}>
                <Typography variant="body2">{item.text}</Typography>
                {item.code && (
                  <Typography variant="body2" sx={{
                    fontFamily: "monospace", bgcolor: "action.hover",
                    px: 1, py: 0.25, borderRadius: 0.5, mt: 0.5,
                    whiteSpace: "pre-wrap", fontSize: "0.78rem",
                  }}>
                    {item.code}
                  </Typography>
                )}
              </Box>
            ))}
          </Box>
        </AccordionDetails>
      </Accordion>

      {/* ── Cloudflare Tunnel Service ─────────────────────────────────────────── */}
      <Divider sx={{ mt: 1 }} />
      <Typography variant="body2" fontWeight={600} gutterBottom>
        Cloudflare Tunnel Service
      </Typography>
      <Alert severity="info" icon={false} sx={{ py: 0.75 }}>
        <strong>cloudflared is pre-installed</strong> on this Pi image — no manual install needed.
        Paste the tunnel token from the Cloudflare dashboard (Step 1 above) to connect this Pi to
        your Cloudflare network.
      </Alert>

      <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
        <Typography variant="body2" fontWeight={500}>Tunnel status:</Typography>
        <Chip
          size="small"
          label={active ? "Active" : "Inactive"}
          color={active ? "success" : "default"}
          variant={active ? "filled" : "outlined"}
        />
        <Tooltip title="Refresh status">
          <IconButton size="small" onClick={load}>
            <RefreshIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>

      <TextField
        label="Tunnel Token" size="small" fullWidth
        type={showToken ? "text" : "password"}
        value={token}
        onChange={e => setToken(e.target.value)}
        placeholder="eyJhIjoixxxxxxxxxxxxxxxxxxxxxxxx…"
        helperText="Cloudflare dashboard → Zero Trust → Networks → Tunnels → your tunnel → Configure → Token"
        InputProps={{
          endAdornment: (
            <InputAdornment position="end">
              <Tooltip title={showToken ? "Hide" : "Show"}>
                <IconButton size="small" onClick={() => setShowToken(v => !v)}>
                  {showToken ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
                </IconButton>
              </Tooltip>
            </InputAdornment>
          ),
        }}
      />

      {msg   && <Alert severity="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {error && <Alert severity="error"   onClose={() => setError(null)}>{error}</Alert>}

      <Box sx={{ display: "flex", gap: 1.5, flexWrap: "wrap" }}>
        <Button variant="contained"
          startIcon={saving ? <CircularProgress size={16} color="inherit" /> : <SaveIcon />}
          onClick={handleSave}
          disabled={saving || !token.trim() || token === MASKED}>
          Save Token
        </Button>
        <Button variant="outlined"
          startIcon={controlling ? <CircularProgress size={16} color="inherit" /> : null}
          onClick={() => handleControl(active ? "stop" : "start")}
          disabled={controlling || !configured}>
          {active ? "Stop Tunnel" : "Start Tunnel"}
        </Button>
        <Button variant="outlined"
          startIcon={controlling ? <CircularProgress size={16} color="inherit" /> : <ReplayIcon />}
          onClick={() => handleControl("restart")}
          disabled={controlling || !configured}>
          Restart
        </Button>
      </Box>

      {configured && (
        <>
          <Divider sx={{ mt: 1 }} />
          <Box>
            <Typography variant="body2" fontWeight={500} color="text.secondary" gutterBottom>
              Danger Zone
            </Typography>
            <Button variant="outlined" color="error" size="small"
              startIcon={saving ? <CircularProgress size={16} color="inherit" /> : <DeleteIcon />}
              onClick={handleClear} disabled={saving || controlling}>
              Clear Token &amp; Stop Tunnel
            </Button>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
              Removes the token from this Pi and stops cloudflared. The tunnel still exists in your
              Cloudflare dashboard and can be reconnected by saving a new token.
            </Typography>
          </Box>
        </>
      )}

      {/* ── Custom Hostname ───────────────────────────────────────────────────── */}
      <Divider sx={{ mt: 1 }} />
      <Typography variant="body2" fontWeight={600}>Custom Hostname (FQDN)</Typography>
      <Typography variant="body2" color="text.secondary">
        Shown in the dashboard footer and used as the OAuth origin. Set this to your
        Tailscale hostname or Cloudflare subdomain after completing the steps above.
        Leave blank to use the Pi&rsquo;s auto-detected hostname.
      </Typography>
      <Box sx={{ display: "flex", gap: 1.5, alignItems: "flex-start", flexWrap: "wrap" }}>
        <TextField
          label="Custom Hostname" size="small" sx={{ flex: 1, minWidth: 240 }}
          value={fqdn} onChange={e => setFqdn(e.target.value)}
          placeholder="dashboard.yourdomain.com"
        />
        <Button
          variant="outlined" size="small" sx={{ mt: 0.25 }}
          startIcon={detecting ? <CircularProgress size={14} color="inherit" /> : <TravelExploreIcon />}
          onClick={handleDetect}
          disabled={detecting || fqdnSaving}
        >
          Auto-detect
        </Button>
        <Button
          variant="contained" size="small" sx={{ mt: 0.25 }}
          startIcon={fqdnSaving ? <CircularProgress size={14} color="inherit" /> : <SaveIcon />}
          onClick={handleFqdnSave}
          disabled={fqdnSaving || detecting}
        >
          Save
        </Button>
      </Box>
      {detectMsg && (
        <Alert severity={detectMsg.type} onClose={() => setDetectMsg(null)} sx={{ mt: 0.5 }}>
          {detectMsg.text}
        </Alert>
      )}
      {fqdnMsg === "error" && (
        <Alert severity="error" onClose={() => setFqdnMsg(null)}>Failed to save hostname.</Alert>
      )}
      {fqdnMsg && fqdnMsg !== "error" && (
        <Alert severity="success" onClose={() => setFqdnMsg(null)}>{fqdnMsg}</Alert>
      )}
    </Section>
  );
}

// ── Backup & Restore (owner-only) ──────────────────────────────────────────────

function BackupSection() {
  const [restoring,   setRestoring]   = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [pendingData, setPendingData] = useState(null);
  const [msg,         setMsg]         = useState(null);
  const [error,       setError]       = useState(null);
  const fileRef = useRef(null);

  const handleDownload = async () => {
    try {
      const res = await api.get("/api/setup/backup", { responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const a   = document.createElement("a");
      a.href     = url;
      a.download = `dashboard-backup-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch { setError("Failed to download backup."); }
  };

  const handleFileChange = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (fileRef.current) fileRef.current.value = "";
    try {
      const data = JSON.parse(await file.text());
      if (data.version !== 1) throw new Error("Unsupported backup version.");
      setPendingData(data);
      setConfirmOpen(true);
    } catch (err) { setError(err.message || "Invalid backup file."); }
  };

  const handleConfirmRestore = async () => {
    setConfirmOpen(false);
    setRestoring(true); setMsg(null); setError(null);
    try {
      await api.post("/api/setup/restore", pendingData);
      setMsg("Restored — reload the page for all changes to take effect.");
    } catch (err) {
      setError(err?.response?.data?.detail || "Restore failed.");
    } finally { setRestoring(false); setPendingData(null); }
  };

  const backupDate = pendingData?.created_at
    ? new Date(pendingData.created_at).toLocaleString() : "";

  return (
    <Section icon={<BackupIcon />} title="Backup & Restore">
      <Typography variant="body2" color="text.secondary">
        Back up all settings, users, calendars, and OAuth credentials to a JSON file.
        Use it to restore after a reset or migrate to a new Pi.
      </Typography>
      {msg   && <Alert severity="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {error && <Alert severity="error"   onClose={() => setError(null)}>{error}</Alert>}
      <Box sx={{ display: "flex", gap: 2, flexWrap: "wrap" }}>
        <Button variant="contained" startIcon={<DownloadIcon />} onClick={handleDownload}>
          Download Backup
        </Button>
        <Button variant="outlined" component="label"
          startIcon={restoring ? <CircularProgress size={16} color="inherit" /> : <UploadIcon />}
          disabled={restoring}>
          Restore from Backup
          <input ref={fileRef} type="file" accept=".json" hidden onChange={handleFileChange} />
        </Button>
      </Box>

      <Dialog open={confirmOpen} onClose={() => setConfirmOpen(false)}>
        <DialogTitle>Restore this backup?</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary">
            {backupDate && <><strong>{backupDate}</strong><br /><br /></>}
            This will <strong>overwrite all current settings, users, and calendars</strong>.
            This cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmOpen(false)}>Cancel</Button>
          <Button color="warning" variant="contained" onClick={handleConfirmRestore}>Restore</Button>
        </DialogActions>
      </Dialog>
    </Section>
  );
}

// ── Reset Install (owner-only) ─────────────────────────────────────────────────

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
    } catch { setError("Reset failed — try again."); }
    finally { setBusy(false); setOpen(false); }
  };

  return (
    <Section icon={<RestartAltIcon />} title="Reset Install">
      <Typography variant="body2" color="text.secondary">
        Removes all signed-in users and RSS feeds. OAuth and weather credentials are kept.
        Use this to hand the dashboard to a new family or start fresh.
      </Typography>
      {success && <Alert severity="success" onClose={() => setSuccess(false)}>Reset complete — all users and RSS feeds removed.</Alert>}
      {error   && <Alert severity="error"   onClose={() => setError(null)}>{error}</Alert>}
      <Box>
        <Button variant="outlined" color="error" startIcon={<RestartAltIcon />} onClick={() => setOpen(true)}>
          Reset Install
        </Button>
      </Box>
      <Dialog open={open} onClose={() => !busy && setOpen(false)}>
        <DialogTitle>Reset this install?</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary">
            This removes <strong>all signed-in users</strong> and <strong>all RSS feeds</strong>.
            OAuth and weather credentials are kept. This cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)} disabled={busy}>Cancel</Button>
          <Button color="error" variant="contained" onClick={handleReset} disabled={busy}
            startIcon={busy ? <CircularProgress size={16} color="inherit" /> : <RestartAltIcon />}>
            {busy ? "Resetting…" : "Reset"}
          </Button>
        </DialogActions>
      </Dialog>
    </Section>
  );
}

// ── Permissions ────────────────────────────────────────────────────────────────

const SECTION_LABELS = {
  weather_location: "Weather Location",
  pi_display:       "Pi Display Controls",
  display_schedule: "Display Schedule",
  family_calendars: "Family Calendars",
  family_members:   "Family Members",
  rss_feeds:        "RSS News Feeds",
  restart_services: "Restart Services",
};

function PermissionsSettings({ currentRole }) {
  const [sections,   setSections]   = useState([]);
  const [adminPerms, setAdminPerms] = useState([]);
  const [userPerms,  setUserPerms]  = useState([]);
  const [saving,     setSaving]     = useState(false);
  const [msg,        setMsg]        = useState(null);
  const [error,      setError]      = useState(null);

  useEffect(() => {
    api.get("/api/settings/permissions").then(res => {
      setSections(res.data.sections || []);
      setAdminPerms(res.data.admin  || []);
      setUserPerms(res.data.user    || []);
    }).catch(() => {});
  }, []);

  const toggleAdmin = s =>
    setAdminPerms(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]);
  const toggleUser = s =>
    setUserPerms(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]);

  const handleSave = async () => {
    setSaving(true); setMsg(null); setError(null);
    try {
      const body = currentRole === "owner"
        ? { admin: adminPerms, user: userPerms }
        : { user: userPerms };
      await api.put("/api/settings/permissions", body);
      setMsg("Permissions saved.");
    } catch { setError("Failed to save."); }
    finally { setSaving(false); }
  };

  // Admin can only configure user access to sections they themselves can see
  const userConfigSections = currentRole === "owner"
    ? sections
    : sections.filter(s => adminPerms.includes(s));

  return (
    <Section icon={<SecurityIcon />} title="Permissions" defaultExpanded={false}>
      <Typography variant="body2" color="text.secondary">
        Control which settings sections are visible to each role.
        My Account is always visible to everyone.
      </Typography>
      <Box sx={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
        {currentRole === "owner" && (
          <Box sx={{ flex: 1, minWidth: 180 }}>
            <Typography variant="body2" fontWeight={600} gutterBottom>Admin can see</Typography>
            {sections.map(s => (
              <Box key={s} sx={{ display: "flex", alignItems: "center" }}>
                <FormControlLabel
                  control={<Checkbox size="small" checked={adminPerms.includes(s)} onChange={() => toggleAdmin(s)} />}
                  label={<Typography variant="body2">{SECTION_LABELS[s] || s}</Typography>}
                />
              </Box>
            ))}
          </Box>
        )}
        <Box sx={{ flex: 1, minWidth: 180 }}>
          <Typography variant="body2" fontWeight={600} gutterBottom>User can see</Typography>
          {userConfigSections.map(s => (
            <Box key={s} sx={{ display: "flex", alignItems: "center" }}>
              <FormControlLabel
                control={<Checkbox size="small" checked={userPerms.includes(s)} onChange={() => toggleUser(s)} />}
                label={<Typography variant="body2">{SECTION_LABELS[s] || s}</Typography>}
              />
            </Box>
          ))}
          {userConfigSections.length === 0 && (
            <Typography variant="body2" color="text.secondary">
              No sections available to configure.
            </Typography>
          )}
        </Box>
      </Box>
      {msg   && <Alert severity="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {error && <Alert severity="error"   onClose={() => setError(null)}>{error}</Alert>}
      <Box>
        <Button variant="contained"
          startIcon={saving ? <CircularProgress size={16} color="inherit" /> : <SaveIcon />}
          onClick={handleSave} disabled={saving}>
          Save Permissions
        </Button>
      </Box>
    </Section>
  );
}

// ── Software Updates (owner-only) ─────────────────────────────────────────────

function UpdateSettings() {
  const [version,     setVersion]     = useState(null);
  const [checking,    setChecking]    = useState(false);
  const [checkResult, setCheckResult] = useState(null);
  const [applying,    setApplying]    = useState(false);
  const [status,      setStatus]      = useState(null);
  const [showLog,     setShowLog]     = useState(false);
  const pollRef  = useRef(null);
  const logBoxRef = useRef(null);

  const loadVersion = useCallback(async () => {
    try {
      const res = await api.get("/api/settings/update/version");
      setVersion(res.data);
    } catch {}
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  const startPolling = useCallback(() => {
    if (pollRef.current) return;
    let failCount = 0;
    pollRef.current = setInterval(async () => {
      try {
        const res = await api.get("/api/settings/update/status");
        failCount = 0;
        setStatus(res.data);
        if (logBoxRef.current) logBoxRef.current.scrollTop = logBoxRef.current.scrollHeight;
        if (!res.data.running) {
          stopPolling();
          setApplying(false);
          loadVersion();
        }
      } catch {
        failCount++;
        // Backend restarted mid-update (expected — setup.sh restarts it)
        if (failCount >= 5) {
          stopPolling();
          setApplying(false);
          setStatus(prev => prev
            ? { ...prev, running: false, restarted: true }
            : { running: false, restarted: true, log: [] }
          );
          loadVersion();
        }
      }
    }, 2500);
  }, [stopPolling, loadVersion]);

  useEffect(() => {
    loadVersion();
    // Resume tracking if an update is already in progress
    api.get("/api/settings/update/status")
      .then(res => {
        if (res.data.running) {
          setApplying(true);
          setStatus(res.data);
          setShowLog(true);
          startPolling();
        }
      })
      .catch(() => {});
  }, [loadVersion, startPolling]);

  useEffect(() => () => stopPolling(), [stopPolling]);

  const handleCheck = async () => {
    setChecking(true); setCheckResult(null);
    try {
      const res = await api.get("/api/settings/update/check");
      setCheckResult(res.data);
    } catch (e) {
      setCheckResult({ error: e?.response?.data?.detail || "Check failed." });
    } finally { setChecking(false); }
  };

  const handleApply = async () => {
    setApplying(true); setStatus(null); setShowLog(true);
    try {
      await api.post("/api/settings/update/apply");
      startPolling();
    } catch (e) {
      setApplying(false);
      setCheckResult(prev => ({ ...prev, applyError: e?.response?.data?.detail || "Failed to start update." }));
    }
  };

  if (version === null) return (
    <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
      <CircularProgress size={24} />
    </Box>
  );

  const isGitInstall = version?.installed;

  return (
    <Section icon={<SystemUpdateAltIcon />} title="Software Updates">
      <Typography variant="body2" color="text.secondary">
        Pull the latest features and bug fixes from GitHub. Your settings, users, and
        config are never overwritten — only the application code is updated.
      </Typography>

      {/* ── Current version ─────────────────────────────────────────────── */}
      <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, flexWrap: "wrap" }}>
        <Typography variant="body2" fontWeight={500}>Installed version:</Typography>
        {isGitInstall ? (
          <>
            <Chip size="small" label={version.commit}
              sx={{ fontFamily: "monospace", fontSize: "0.75rem" }} />
            {version.date && (
              <Typography variant="caption" color="text.secondary">
                {new Date(version.date).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })}
              </Typography>
            )}
            {version.branch && version.branch !== "main" && (
              <Chip size="small" label={`branch: ${version.branch}`} color="warning" variant="outlined" />
            )}
          </>
        ) : (
          <Chip size="small" label="Not a git install" color="warning" />
        )}
      </Box>

      {/* ── Not a git install ────────────────────────────────────────────── */}
      {!isGitInstall && (
        <Alert severity="info">
          Automatic updates are only available on Pis that were set up via the
          official image (installed from GitHub). Manual installs cannot use
          this feature.
        </Alert>
      )}

      {/* ── Check + Apply ────────────────────────────────────────────────── */}
      {isGitInstall && (
        <>
          <Box sx={{ display: "flex", gap: 1.5, flexWrap: "wrap", alignItems: "center" }}>
            <Button
              variant="outlined"
              startIcon={checking
                ? <CircularProgress size={16} color="inherit" />
                : <RefreshIcon />}
              onClick={handleCheck}
              disabled={checking || applying}
            >
              Check for Updates
            </Button>
            {checkResult?.up_to_date && (
              <Chip size="small" color="success" label="Already up to date ✓" />
            )}
          </Box>

          {/* Check error */}
          {checkResult?.error && (
            <Alert severity="error" onClose={() => setCheckResult(null)}>
              {checkResult.error}
            </Alert>
          )}

          {/* Updates available */}
          {checkResult && !checkResult.error && !checkResult.up_to_date && (
            <Box sx={{ border: "1px solid", borderColor: "primary.light", borderRadius: 1, p: 1.5,
                       bgcolor: "primary.50" }}>
              <Typography variant="body2" fontWeight={600} gutterBottom>
                {checkResult.count} update{checkResult.count !== 1 ? "s" : ""} available
              </Typography>
              {checkResult.changes.length > 0 && (
                <Box sx={{ display: "flex", flexDirection: "column", gap: 0.15, mb: 1.5,
                           maxHeight: 160, overflowY: "auto" }}>
                  {checkResult.changes.map((c, i) => (
                    <Typography key={i} variant="caption"
                      sx={{ fontFamily: "monospace", color: "text.secondary" }}>
                      {c}
                    </Typography>
                  ))}
                </Box>
              )}
              <Button
                variant="contained"
                startIcon={applying
                  ? <CircularProgress size={16} color="inherit" />
                  : <SystemUpdateAltIcon />}
                onClick={handleApply}
                disabled={applying}
              >
                {applying ? "Updating…" : "Apply Update"}
              </Button>
              {checkResult.applyError && (
                <Alert severity="error" sx={{ mt: 1 }}>{checkResult.applyError}</Alert>
              )}
            </Box>
          )}
        </>
      )}

      {/* ── Update progress / log ────────────────────────────────────────── */}
      {(applying || status !== null) && (
        <Box>
          {status?.restarted && (
            <Alert severity="success" sx={{ mb: 1 }} onClose={() => setStatus(null)}>
              Update applied — backend restarted with new code.
            </Alert>
          )}
          {!status?.restarted && status?.success === true && (
            <Alert severity="success" sx={{ mb: 1 }} onClose={() => setStatus(null)}>
              Update complete! The backend is restarting.
            </Alert>
          )}
          {!status?.restarted && status?.success === false && (
            <Alert severity="error" sx={{ mb: 1 }}>
              Update failed (exit code {status.exit_code}). See the log below.
            </Alert>
          )}

          <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
            <Typography variant="body2" fontWeight={500} color={applying ? "primary.main" : "text.primary"}>
              {applying ? (
                <Box component="span" sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
                  <CircularProgress size={14} />
                  Update in progress…
                </Box>
              ) : "Update log"}
            </Typography>
            <Button size="small" sx={{ py: 0, minHeight: 0 }}
              onClick={() => setShowLog(v => !v)}>
              {showLog ? "Hide" : "Show"} log
            </Button>
          </Box>

          {showLog && (
            <Box ref={logBoxRef} sx={{
              bgcolor: "grey.900", color: "#e0e0e0",
              p: 1.5, borderRadius: 1, mt: 0.5,
              fontFamily: "monospace", fontSize: "0.72rem", lineHeight: 1.6,
              maxHeight: 320, overflowY: "auto",
              whiteSpace: "pre-wrap", wordBreak: "break-all",
            }}>
              {(status?.log || []).length === 0 && applying && (
                <Box component="span" sx={{ opacity: 0.5 }}>Starting…</Box>
              )}
              {(status?.log || []).map((line, i) => (
                <Box key={i} component="div">{line}</Box>
              ))}
              {applying && (
                <Box component="span" sx={{ opacity: 0.5, animation: "blink 1s step-end infinite" }}>▋</Box>
              )}
            </Box>
          )}
        </Box>
      )}

      {/* ── Manual install tip ───────────────────────────────────────────── */}
      {isGitInstall && !applying && (
        <Alert severity="info" icon={false} sx={{ py: 0.75 }}>
          <Typography variant="caption">
            You can also update manually over SSH:{" "}
            <Box component="span" sx={{ fontFamily: "monospace" }}>
              sudo bash /opt/dashboard/pi/update.sh
            </Box>
          </Typography>
        </Alert>
      )}
    </Section>
  );
}

// ── Display Schedule ──────────────────────────────────────────────────────────

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function DisplayScheduleSettings() {
  const [enabled,    setEnabled]    = useState(false);
  const [onTime,     setOnTime]     = useState("07:00");
  const [offTime,    setOffTime]    = useState("22:00");
  const [days,       setDays]       = useState([0, 1, 2, 3, 4, 5, 6]);   // 0=Mon … 6=Sun
  const [displayOff, setDisplayOff] = useState(false);   // live state from Pi
  const [saving,     setSaving]     = useState(false);
  const [powering,   setPowering]   = useState(false);
  const [msg,        setMsg]        = useState(null);
  const [error,      setError]      = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await api.get("/api/settings/display_schedule");
      setEnabled(!!res.data.enabled);
      setOnTime(res.data.on_time   || "07:00");
      setOffTime(res.data.off_time || "22:00");
      setDays(res.data.days        ?? [0, 1, 2, 3, 4, 5, 6]);
      setDisplayOff(!!res.data.display_is_off);
    } catch {}
  }, []);

  useEffect(() => { load(); }, [load]);

  const toggleDay = (d) =>
    setDays(prev => prev.includes(d) ? prev.filter(x => x !== d) : [...prev, d].sort((a, b) => a - b));

  const handleSave = async () => {
    setSaving(true); setMsg(null); setError(null);
    try {
      await api.put("/api/settings/display_schedule", { enabled, on_time: onTime, off_time: offTime, days });
      setMsg("Schedule saved — will take effect within 60 seconds.");
    } catch { setError("Failed to save schedule."); }
    finally { setSaving(false); }
  };

  const handlePower = async (on) => {
    setPowering(true); setMsg(null); setError(null);
    try {
      await api.post("/api/settings/display_schedule/power", { on });
      setDisplayOff(!on);
      setMsg(`Display turned ${on ? "on" : "off"} — screen updates within 1 second.`);
    } catch { setError("Power command failed."); }
    finally { setPowering(false); }
  };

  return (
    <Section icon={<AccessTimeIcon />} title="Display Schedule">
      <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
        <Typography variant="body2" color="text.secondary">
          Automatically blank the Pi display at night and restore it in the morning.
          Works by signalling display.py directly — no driver or root access needed.
        </Typography>
        <Chip
          size="small"
          label={displayOff ? "Blanked" : "On"}
          color={displayOff ? "default" : "success"}
          variant={displayOff ? "outlined" : "filled"}
          sx={{ flexShrink: 0 }}
        />
      </Box>

      {/* ── Enable toggle ────────────────────────────────────────────────── */}
      <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
        <Typography variant="body2" fontWeight={500}>Enable schedule:</Typography>
        <Switch checked={enabled} onChange={e => setEnabled(e.target.checked)} />
        <Typography variant="body2" color="text.secondary">
          {enabled ? "On — display follows the schedule below" : "Off — display is always on"}
        </Typography>
      </Box>

      {/* ── Time window ──────────────────────────────────────────────────── */}
      <Box sx={{ display: "flex", gap: 3, flexWrap: "wrap", alignItems: "flex-end", opacity: enabled ? 1 : 0.45 }}>
        <TextField
          label="Turn on at"
          type="time"
          size="small"
          value={onTime}
          onChange={e => setOnTime(e.target.value)}
          disabled={!enabled}
          sx={{ width: 150 }}
          InputLabelProps={{ shrink: true }}
          inputProps={{ step: 300 }}
        />
        <TextField
          label="Turn off at"
          type="time"
          size="small"
          value={offTime}
          onChange={e => setOffTime(e.target.value)}
          disabled={!enabled}
          sx={{ width: 150 }}
          InputLabelProps={{ shrink: true }}
          inputProps={{ step: 300 }}
        />
        {onTime && offTime && (() => {
          const [oh, om] = onTime.split(":").map(Number);
          const [fh, fm] = offTime.split(":").map(Number);
          const onMin  = oh * 60 + om;
          const offMin = fh * 60 + fm;
          const overnight = offMin <= onMin;
          return (
            <Typography variant="caption" color="text.secondary" sx={{ pb: 0.5 }}>
              {overnight
                ? `Display on ${onTime}–midnight then midnight–${offTime} (overnight)`
                : `Display on ${onTime}–${offTime}`}
            </Typography>
          );
        })()}
      </Box>

      {/* ── Day-of-week picker ───────────────────────────────────────────── */}
      <Box sx={{ opacity: enabled ? 1 : 0.45 }}>
        <Typography variant="body2" fontWeight={500} gutterBottom>Active days:</Typography>
        <Box sx={{ display: "flex", gap: 0.75, flexWrap: "wrap" }}>
          {DAY_LABELS.map((label, idx) => (
            <Box
              key={idx}
              onClick={() => enabled && toggleDay(idx)}
              sx={{
                width: 42, height: 42,
                borderRadius: "50%",
                border: "2px solid",
                borderColor: days.includes(idx) ? "primary.main" : "divider",
                bgcolor: days.includes(idx) ? "primary.main" : "transparent",
                color: days.includes(idx) ? "primary.contrastText" : "text.secondary",
                display: "flex", alignItems: "center", justifyContent: "center",
                cursor: enabled ? "pointer" : "default",
                fontWeight: 600, fontSize: "0.78rem",
                userSelect: "none",
                transition: "all 0.15s",
                "&:hover": enabled ? { opacity: 0.85 } : {},
              }}
            >
              {label}
            </Box>
          ))}
        </Box>
        <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
          {days.length === 7
            ? "Every day"
            : days.length === 0
            ? "No days selected — display will always be on"
            : DAY_LABELS.filter((_, i) => days.includes(i)).join(", ")}
        </Typography>
      </Box>

      {msg   && <Alert severity="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {error && <Alert severity="error"   onClose={() => setError(null)}>{error}</Alert>}

      <Box sx={{ display: "flex", gap: 1.5, flexWrap: "wrap" }}>
        <Button variant="contained"
          startIcon={saving ? <CircularProgress size={16} color="inherit" /> : <SaveIcon />}
          onClick={handleSave} disabled={saving}>
          Save Schedule
        </Button>
      </Box>

      {/* ── Manual override ──────────────────────────────────────────────── */}
      <Divider sx={{ mt: 1 }} />
      <Typography variant="body2" fontWeight={500}>Manual override</Typography>
      <Typography variant="body2" color="text.secondary">
        Immediately turn the display on or off for testing. The scheduler resumes on the next
        60-second tick.
      </Typography>
      <Box sx={{ display: "flex", gap: 1.5, flexWrap: "wrap" }}>
        <Button
          variant="outlined"
          color="success"
          startIcon={powering ? <CircularProgress size={16} color="inherit" /> : <PowerSettingsNewIcon />}
          onClick={() => handlePower(true)}
          disabled={powering}
        >
          Display On
        </Button>
        <Button
          variant="outlined"
          color="error"
          startIcon={powering ? <CircularProgress size={16} color="inherit" /> : <PowerSettingsNewIcon />}
          onClick={() => handlePower(false)}
          disabled={powering}
        >
          Display Off
        </Button>
      </Box>
    </Section>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────────

// ── Sidebar nav button ─────────────────────────────────────────────────────────

function NavItem({ icon, label, selected, onClick }) {
  return (
    <Box
      component="button"
      onClick={onClick}
      sx={{
        display: "flex", alignItems: "center", gap: 1.5,
        width: "100%", px: 2.5, py: 1.1,
        border: "none", background: "none", cursor: "pointer",
        textAlign: "left", fontFamily: "inherit",
        fontSize: "0.875rem",
        fontWeight: selected ? 600 : 400,
        color: selected ? "primary.main" : "text.secondary",
        bgcolor: selected ? "action.selected" : "transparent",
        borderRight: "3px solid",
        borderColor: selected ? "primary.main" : "transparent",
        "&:hover": { bgcolor: selected ? "action.selected" : "action.hover" },
        transition: "background-color 0.15s",
      }}
    >
      <Box sx={{ display: "flex", flexShrink: 0 }}>{icon}</Box>
      {label}
    </Box>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────────

export default function Settings() {
  const navigate = useNavigate();
  const [currentUser,     setCurrentUser]     = useState(getStoredUser);
  const [currentRole,     setCurrentRole]     = useState(getStoredRole);
  const [permissions,     setPermissions]     = useState({ admin: [], user: ["family_calendars"] });
  const [oauthConfigured, setOauthConfigured] = useState(true);
  const [tab,             setTab]             = useState("my_account");

  // Refresh role from server on load
  useEffect(() => {
    const user = getStoredUser();
    if (!user?.email) return;
    api.get(`/api/user-prefs/${encodeURIComponent(user.email)}`)
      .then(res => { const r = res.data.role || "user"; setCurrentRole(r); storeRole(r); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    api.get("/api/settings/permissions").then(res => {
      setPermissions({ admin: res.data.admin || [], user: res.data.user || [] });
    }).catch(() => {});
    api.get("/api/settings/oauth").then(res => {
      setOauthConfigured(!!res.data.configured);
    }).catch(() => {});
  }, []);

  const handleSignIn = useCallback(({ email, name, picture, role }) => {
    const u = { email, name, picture };
    setCurrentUser(u); storeUser(u);
    setCurrentRole(role || "user"); storeRole(role || "user");
  }, []);

  const handleSignOut = useCallback(() => {
    setCurrentUser(null); clearStoredUser();
    setCurrentRole("user"); clearStoredRole();
  }, []);

  const canSee = useCallback((section) => {
    if (currentRole === "owner") return true;
    if (currentRole === "admin") return permissions.admin.includes(section);
    return permissions.user.includes(section);
  }, [currentRole, permissions]);

  const isOwner        = currentRole === "owner";
  const isAdminOrOwner = currentRole === "admin" || currentRole === "owner";

  const { userTabs, adminTabs } = useMemo(() => {
    const user = [
      { value: "my_account",       label: "My Account",       icon: <AccountCircleIcon fontSize="small" /> },
      canSee("weather_location") && { value: "weather_location", label: "Weather",          icon: <CloudIcon fontSize="small" /> },
      canSee("pi_display")       && { value: "pi_display",       label: "Pi Display",       icon: <TvIcon fontSize="small" /> },
      canSee("display_schedule") && { value: "display_schedule", label: "Schedule",          icon: <AccessTimeIcon fontSize="small" /> },
      canSee("family_calendars") && { value: "family_calendars", label: "Family Calendars", icon: <GroupAddIcon fontSize="small" /> },
      canSee("family_members")   && { value: "family_members",   label: "Family Members",   icon: <CalendarMonthIcon fontSize="small" /> },
      canSee("rss_feeds")        && { value: "rss_feeds",        label: "RSS Feeds",        icon: <RssFeedIcon fontSize="small" /> },
      canSee("restart_services") && { value: "restart_services", label: "Restart",          icon: <ReplayIcon fontSize="small" /> },
    ].filter(Boolean);

    const admin = [
      isAdminOrOwner                  && { value: "permissions", label: "Permissions",        icon: <SecurityIcon fontSize="small" /> },
      (isOwner || !oauthConfigured)   && { value: "oauth",       label: "OAuth / Google",     icon: <LockIcon fontSize="small" /> },
      isOwner                         && { value: "tunnel",      label: "FQDN Setup",         icon: <RouterIcon fontSize="small" /> },
      isOwner                         && { value: "updates",     label: "Updates",            icon: <SystemUpdateAltIcon fontSize="small" /> },
      isOwner                         && { value: "backup",      label: "Backup & Restore",   icon: <BackupIcon fontSize="small" /> },
      isOwner                         && { value: "reset",       label: "Reset Install",      icon: <RestartAltIcon fontSize="small" /> },
    ].filter(Boolean);

    return { userTabs: user, adminTabs: admin };
  }, [canSee, isAdminOrOwner, isOwner, oauthConfigured]);

  const renderContent = () => {
    switch (tab) {
      case "my_account":       return <MyAccount onSignIn={handleSignIn} onSignOut={handleSignOut} />;
      case "weather_location": return <WeatherLocation />;
      case "pi_display":       return <PiDisplay />;
      case "display_schedule": return <DisplayScheduleSettings />;
      case "family_calendars": return <FamilyCalendars />;
      case "family_members":   return <FamilyMembers currentUser={currentUser} currentRole={currentRole} />;
      case "rss_feeds":        return <RssSettings />;
      case "restart_services": return <RestartServices />;
      case "permissions":      return <PermissionsSettings currentRole={currentRole} />;
      case "oauth":            return <OAuthSettings />;
      case "tunnel":           return <TunnelSettings />;
      case "updates":          return <UpdateSettings />;
      case "backup":           return <BackupSection />;
      case "reset":            return <ResetSection />;
      default:                 return null;
    }
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100vh", bgcolor: "grey.100" }}>
      {/* Top bar */}
      <Box sx={{
        flexShrink: 0, bgcolor: "background.paper",
        borderBottom: "1px solid", borderColor: "divider",
        px: 3, py: 1.5,
        display: "flex", alignItems: "center", justifyContent: "space-between",
      }}>
        <Typography variant="h5" fontWeight={700}>Settings</Typography>
        <Button size="small" variant="outlined" startIcon={<DashboardIcon />} onClick={() => navigate("/")}>
          Back to Dashboard
        </Button>
      </Box>

      {/* Body */}
      <Box sx={{ display: "flex", flex: 1, overflow: "hidden" }}>
        {/* Left sidebar nav */}
        <Box sx={{
          width: 220, flexShrink: 0,
          bgcolor: "background.paper",
          borderRight: "1px solid", borderColor: "divider",
          display: "flex", flexDirection: "column",
          overflow: "hidden",
        }}>
          {/* Scrollable nav items */}
          <Box sx={{ flex: 1, overflowY: "auto", py: 1 }}>
            {userTabs.map(t => (
              <NavItem key={t.value} icon={t.icon} label={t.label}
                selected={tab === t.value} onClick={() => setTab(t.value)} />
            ))}

            {adminTabs.length > 0 && (
              <>
                <Divider sx={{ my: 1 }} />
                <Typography variant="overline" sx={{
                  px: 2.5, display: "block", color: "text.disabled",
                  fontSize: "0.65rem", letterSpacing: "0.08em", lineHeight: 2,
                }}>
                  Administration
                </Typography>
                {adminTabs.map(t => (
                  <NavItem key={t.value} icon={t.icon} label={t.label}
                    selected={tab === t.value} onClick={() => setTab(t.value)} />
                ))}
              </>
            )}
          </Box>

          {/* Pinned bottom button */}
          <Box sx={{ p: 1.5, borderTop: "1px solid", borderColor: "divider" }}>
            <Button
              fullWidth
              variant="outlined"
              size="small"
              endIcon={<OpenInNewIcon fontSize="small" />}
              component="a"
              href="https://calendar.google.com"
              target="_blank"
              rel="noopener noreferrer"
              sx={{ textTransform: "none", justifyContent: "space-between" }}
            >
              Manage Calendars
            </Button>
          </Box>
        </Box>

        {/* Content pane */}
        <Box sx={{ flex: 1, overflowY: "auto", p: 3 }}>
          <Box sx={{ maxWidth: 680 }}>
            {renderContent()}
          </Box>
        </Box>
      </Box>
    </Box>
  );
}
