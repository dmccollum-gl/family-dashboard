import { useState, useEffect, useCallback } from "react";
import {
  Box, Typography, Paper, Button, Divider, Alert, TextField,
  CircularProgress, List, ListItem, Avatar, Chip, Menu, MenuItem,
  Dialog, DialogTitle, DialogContent, DialogActions,
  Switch, ListItemText, ListItemIcon, IconButton, Tooltip,
  Checkbox, Radio, RadioGroup, FormControlLabel, FormControl, FormLabel,
} from "@mui/material";
import SaveIcon             from "@mui/icons-material/Save";
import AccountCircleIcon    from "@mui/icons-material/AccountCircle";
import DashboardIcon        from "@mui/icons-material/Dashboard";
import LogoutIcon           from "@mui/icons-material/Logout";
import CalendarMonthIcon    from "@mui/icons-material/CalendarMonth";
import DeleteIcon           from "@mui/icons-material/Delete";
import RefreshIcon          from "@mui/icons-material/Refresh";
import MoreVertIcon         from "@mui/icons-material/MoreVert";
import GroupAddIcon         from "@mui/icons-material/GroupAdd";
import AddCircleOutlineIcon from "@mui/icons-material/AddCircleOutline";
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

// ── Calendar URL / ID parser ───────────────────────────────────────────────────

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

// ── Section wrapper ────────────────────────────────────────────────────────────

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
              Checked members have this calendar in their Google Calendar. It won't appear on the
              dashboard until they toggle it on in their own settings.
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

// ── Calendar picker ────────────────────────────────────────────────────────────

const SCOPE_ERROR = "Request had insufficient authentication scopes";
function isScopeError(msg) { return typeof msg === "string" && msg.includes("insufficient authentication scopes"); }

function CalendarPicker({ email, selected, onChange, onReauth }) {
  const [calendars,    setCalendars]    = useState([]);
  const [loading,      setLoading]      = useState(false);
  const [error,        setError]        = useState(null);
  const [members,      setMembers]      = useState([]);
  const [menuState,    setMenuState]    = useState(null);
  const [dialog,       setDialog]       = useState(null);
  const [confirm,      setConfirm]      = useState(null);
  const [shareDialog,  setShareDialog]  = useState(null);
  const [busy,         setBusy]         = useState(null);
  const [actionMsg,    setActionMsg]    = useState(null);

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
      await api.post(`/api/calendar/subscription/${encodeURIComponent(targetEmail)}`, { calendar_id: cal.id });
      setActionMsg(`Copied "${cal.summary || cal.id}" to ${name}`);
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
      body:  `"${cal.summary || cal.id}" will be added to ${member?.display_name || targetEmail}'s Google Calendar. Your copy is unaffected.`,
      onConfirm: () => execCopy(cal, targetEmail),
    });
  };
  const handleTransfer = (targetEmail) => {
    const cal = dialog.cal;
    const member = members.find(m => m.email === targetEmail);
    setDialog(null);
    setConfirm({
      title: "Transfer calendar?",
      body:  `"${cal.summary || cal.id}" will be added to ${member?.display_name || targetEmail}'s Google Calendar and removed from yours.`,
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
        Toggle to show on dashboard · <GroupAddIcon sx={{ fontSize: 13, verticalAlign: "middle" }} /> to share with family · ⋮ for more options
      </Typography>

      {actionMsg && <Alert severity="success" onClose={() => setActionMsg(null)} sx={{ mb: 1 }}>{actionMsg}</Alert>}
      {error === "__scope__" ? (
        <Alert severity="warning" onClose={() => setError(null)} sx={{ mb: 1 }}
          action={<Button size="small" color="inherit" onClick={onReauth}>Sign out &amp; re-authorize</Button>}>
          Your sign-in needs to be renewed to manage calendars. Sign out and sign back in to grant the required permission.
        </Alert>
      ) : error?.startsWith("__scope_other__") ? (
        <Alert severity="warning" onClose={() => setError(null)} sx={{ mb: 1 }}>
          <strong>{error.slice("__scope_other__".length)}</strong> needs to sign out and sign back in before you can manage their calendars.
        </Alert>
      ) : error ? (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 1 }}>{error}</Alert>
      ) : null}

      <List dense disablePadding sx={{ maxHeight: 320, overflowY: "auto", border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
        {calendars.map(cal => (
          <ListItem
            key={cal.id}
            disableGutters
            sx={{ px: 1.5, py: 0.5 }}
            secondaryAction={
              <Box sx={{ display: "flex", alignItems: "center" }}>
                <Tooltip title="Family Sharing">
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
            }
          >
            <ListItemIcon sx={{ minWidth: 28 }}>
              <Box sx={{ width: 12, height: 12, borderRadius: "50%", bgcolor: cal.backgroundColor || "#1976d2" }} />
            </ListItemIcon>
            <ListItemText
              primary={cal.summary}
              secondary={cal.primary ? "Primary calendar" : cal.accessRole}
              primaryTypographyProps={{ variant: "body2", noWrap: true }}
              secondaryTypographyProps={{ variant: "caption" }}
            />
          </ListItem>
        ))}
      </List>
      <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
        {selected.size} of {calendars.length} shown on dashboard
      </Typography>

      {/* Per-calendar action menu */}
      <Menu anchorEl={menuState?.anchor} open={!!menuState} onClose={() => setMenuState(null)}>
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

      {/* Copy / Transfer dialog */}
      <Dialog open={!!dialog} onClose={() => setDialog(null)} maxWidth="xs" fullWidth>
        <DialogTitle>{dialog?.type === "copy" ? "Copy calendar to…" : "Transfer calendar to…"}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {dialog?.type === "copy"
              ? `"${dialog?.cal?.summary}" will be added to the selected member's Google Calendar.`
              : `"${dialog?.cal?.summary}" will be moved to the selected member and removed from yours.`}
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

      {/* Confirmation dialog */}
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

      {/* Family Sharing dialog */}
      <FamilySharingDialog
        cal={shareDialog}
        ownerEmail={email}
        open={!!shareDialog}
        onClose={() => setShareDialog(null)}
      />
    </Box>
  );
}

// ── My Account inner ───────────────────────────────────────────────────────────

function MyAccountInner({ hasSecret }) {
  const [user,     setUser]     = useState(getStoredUser);
  const [color,    setColor]    = useState("#1976d2");
  const [hexDraft, setHexDraft] = useState("#1976d2");
  const [selected, setSelected] = useState(new Set());
  const [saving,   setSaving]   = useState(false);
  const [signing,  setSigning]  = useState(false);
  const [msg,      setMsg]      = useState(null);
  const [error,    setError]    = useState(null);

  const loadPrefs = useCallback(async (email) => {
    try {
      const res = await api.get(`/api/user-prefs/${encodeURIComponent(email)}`);
      if (res.data.display_color)      { setColor(res.data.display_color); setHexDraft(res.data.display_color); }
      if (res.data.selected_calendars) setSelected(new Set(res.data.selected_calendars));
    } catch {}
  }, []);

  useEffect(() => { if (user?.email) loadPrefs(user.email); }, [user, loadPrefs]);

  const handleCode = async (codeResponse) => {
    setSigning(true); setError(null);
    try {
      const res = await api.post("/api/auth/google", { code: codeResponse.code });
      const u = { email: res.data.email, name: res.data.name, picture: res.data.picture };
      setUser(u); storeUser(u); loadPrefs(u.email);
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
      await api.put(`/api/user-prefs/${encodeURIComponent(info.email)}`, {
        display_name: info.name, display_color: color,
        selected_calendars: [...selected], access_token: tokenResponse.access_token, token_expiry: expiryMs,
      });
      setUser(u); storeUser(u); loadPrefs(u.email);
    } catch (e) {
      setError(e?.response?.data?.detail || "Sign-in failed.");
    } finally { setSigning(false); }
  };

  const handleLogout = () => {
    googleLogout(); clearStoredUser();
    setUser(null); setColor("#1976d2"); setSelected(new Set()); setMsg(null);
  };

  const handleSave = async () => {
    if (!user) return;
    setSaving(true); setMsg(null); setError(null);
    try {
      await api.put(`/api/user-prefs/${encodeURIComponent(user.email)}`, {
        display_name: user.name, display_color: color, selected_calendars: [...selected],
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

      <CalendarPicker email={user.email} selected={selected} onChange={setSelected} onReauth={handleLogout} />

      {msg   && <Alert severity="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {error && <Alert severity="error"   onClose={() => setError(null)}>{error}</Alert>}

      <Box>
        <Button variant="contained" size="large"
          startIcon={saving ? <CircularProgress size={18} color="inherit" /> : <SaveIcon />}
          onClick={handleSave} disabled={saving}>
          Save My Settings
        </Button>
      </Box>
    </Box>
  );
}

// ── Shell ──────────────────────────────────────────────────────────────────────

function MyAccount() {
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
        <MyAccountInner hasSecret={hasSecret} />
      )}
    </Section>
  );
}

// ── Add Calendar ───────────────────────────────────────────────────────────────

function AddCalendar() {
  const [members,    setMembers]    = useState([]);
  const [url,        setUrl]        = useState("");
  const [primary,    setPrimary]    = useState("");
  const [secondary,  setSecondary]  = useState(new Set());
  const [submitting, setSubmitting] = useState(false);
  const [msg,        setMsg]        = useState(null);
  const [error,      setError]      = useState(null);

  useEffect(() => {
    api.get("/api/user-prefs").then(res => {
      const users = res.data || [];
      setMembers(users);
      if (users.length > 0 && !primary) setPrimary(users[0].email);
    }).catch(() => {});
  }, []);

  const calId    = extractCalendarId(url);
  const calValid = url.trim().length > 0 && calId.length > 0 && !calId.startsWith("http") && (calId.includes("@") || calId.includes("#"));

  const toggleSecondary = (email) => {
    setSecondary(prev => {
      const next = new Set(prev);
      if (next.has(email)) next.delete(email); else next.add(email);
      return next;
    });
  };

  const handleSubmit = async () => {
    if (!calValid || !primary) return;
    setSubmitting(true); setMsg(null); setError(null);
    try {
      await api.post(`/api/calendar/subscription/${encodeURIComponent(primary)}`, { calendar_id: calId });
      const prefsRes = await api.get(`/api/user-prefs/${encodeURIComponent(primary)}`);
      const existing = new Set(prefsRes.data.selected_calendars || []);
      existing.add(calId);
      await api.put(`/api/user-prefs/${encodeURIComponent(primary)}`, { selected_calendars: [...existing] });
      await Promise.allSettled(
        [...secondary].map(email =>
          api.post(`/api/calendar/subscription/${encodeURIComponent(email)}`, { calendar_id: calId })
        )
      );
      const name = members.find(m => m.email === primary)?.display_name || primary;
      setMsg(`Calendar added and set to display on the dashboard for ${name}.`);
      setUrl(""); setSecondary(new Set());
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to add calendar. Check that the calendar ID is correct and the user has signed in.");
    } finally { setSubmitting(false); }
  };

  if (!members.length) return null;

  return (
    <Section icon={<AddCircleOutlineIcon />} title="Add Calendar">
      <Typography variant="body2" color="text.secondary">
        Add any Google Calendar by URL or ID. The primary user's copy will appear on the dashboard;
        secondary users get it added to their Google Calendar without dashboard visibility.
      </Typography>

      {msg   && <Alert severity="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {error && <Alert severity="error"   onClose={() => setError(null)}>{error}</Alert>}

      <TextField
        label="Calendar URL or ID"
        placeholder="Paste a Google Calendar sharing URL or a calendar ID"
        value={url}
        onChange={e => setUrl(e.target.value)}
        fullWidth size="small"
        helperText={
          url.trim() === "" ? "Example: https://calendar.google.com/calendar/embed?src=en.usa%23holiday%40group.v.calendar.google.com" :
          calValid          ? `Calendar ID: ${calId}` :
                              "Couldn't parse a calendar ID — try pasting the full sharing URL."
        }
        error={url.trim().length > 0 && !calValid}
      />

      <FormControl component="fieldset">
        <FormLabel component="legend" sx={{ fontSize: "0.875rem", fontWeight: 500, mb: 0.5 }}>
          Primary user — calendar shown on dashboard
        </FormLabel>
        <RadioGroup value={primary} onChange={e => setPrimary(e.target.value)}>
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

      {members.filter(m => m.email !== primary).length > 0 && (
        <FormControl component="fieldset">
          <FormLabel component="legend" sx={{ fontSize: "0.875rem", fontWeight: 500, mb: 0.5 }}>
            Secondary users — added to Google Calendar, not shown on dashboard
          </FormLabel>
          {members.filter(m => m.email !== primary).map(m => (
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

      <Box>
        <Button variant="contained" onClick={handleSubmit}
          disabled={!calValid || !primary || submitting}
          startIcon={submitting ? <CircularProgress size={18} color="inherit" /> : <AddCircleOutlineIcon />}>
          Add Calendar
        </Button>
      </Box>
    </Section>
  );
}

// ── Family Members ─────────────────────────────────────────────────────────────

function FamilyMembers() {
  const [members,  setMembers]  = useState([]);
  const [removing, setRemoving] = useState(null);
  const [error,    setError]    = useState(null);

  const load = useCallback(async () => {
    try { const res = await api.get("/api/user-prefs"); setMembers(res.data); } catch {}
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleRemove = async (email) => {
    setRemoving(email); setError(null);
    try {
      await api.delete(`/api/user-prefs/${encodeURIComponent(email)}`);
      if (getStoredUser()?.email === email) clearStoredUser();
      setMembers(prev => prev.filter(m => m.email !== email));
    } catch { setError(`Could not remove ${email}.`); }
    finally { setRemoving(null); }
  };

  if (!members.length) return null;

  return (
    <Section icon={<CalendarMonthIcon />} title="Family Members">
      <Typography variant="body2" color="text.secondary">
        Everyone connected to this dashboard. Remove a member to stop showing their events.
      </Typography>
      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
      <List dense disablePadding sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
        {members.map(m => (
          <ListItem key={m.email} disableGutters sx={{ gap: 1.5 }}
            secondaryAction={
              <Tooltip title="Remove from dashboard">
                <IconButton size="small" color="error" disabled={removing === m.email}
                  onClick={() => handleRemove(m.email)}>
                  {removing === m.email ? <CircularProgress size={18} /> : <DeleteIcon fontSize="small" />}
                </IconButton>
              </Tooltip>
            }>
            <Box sx={{ width: 12, height: 12, borderRadius: "50%", bgcolor: m.display_color, flexShrink: 0 }} />
            <ListItemText
              primary={m.display_name || m.email}
              secondary={m.email}
              primaryTypographyProps={{ variant: "body2", fontWeight: 500 }}
              secondaryTypographyProps={{ variant: "caption" }}
            />
            <Chip size="small"
              label={m.has_refresh ? "Permanent" : m.has_token ? "Temporary" : "No token"}
              color={m.has_refresh ? "success" : m.has_token ? "warning" : "default"}
              variant="outlined" sx={{ mr: 1 }} />
          </ListItem>
        ))}
      </List>
      <Typography variant="caption" color="text.secondary">
        "Permanent" means the sign-in will auto-renew. "Temporary" means they'll need to sign in again after ~1 hour.
      </Typography>
    </Section>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────────

export default function Settings() {
  const navigate = useNavigate();

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "grey.100", py: 4, px: 3 }}>
      <Box sx={{ maxWidth: 680, mx: "auto", display: "flex", flexDirection: "column", gap: 3 }}>

        <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <Typography variant="h4" fontWeight={700}>My Settings</Typography>
          <Button variant="outlined" startIcon={<DashboardIcon />} onClick={() => navigate("/")}>
            Back to Dashboard
          </Button>
        </Box>

        <MyAccount />
        <AddCalendar />
        <FamilyMembers />

      </Box>
    </Box>
  );
}
