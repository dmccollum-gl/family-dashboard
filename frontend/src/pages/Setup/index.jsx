import { useState, useEffect, useCallback } from "react";
import {
  Box, Button, Card, CardActionArea, CardContent,
  CircularProgress, Container, Divider, IconButton,
  InputAdornment, LinearProgress, Stack, Step, StepLabel,
  Stepper, TextField, Typography,
} from "@mui/material";
import CheckCircleIcon    from "@mui/icons-material/CheckCircle";
import ErrorIcon          from "@mui/icons-material/Error";
import RefreshIcon        from "@mui/icons-material/Refresh";
import Visibility         from "@mui/icons-material/Visibility";
import VisibilityOff      from "@mui/icons-material/VisibilityOff";
import WifiIcon           from "@mui/icons-material/Wifi";
import WifiOffIcon        from "@mui/icons-material/WifiOff";
import RouterIcon         from "@mui/icons-material/Router";

const STEPS = ["WiFi Network", "Device Info", "Applying"];

// Map signal strength (0-100) to 1-4 bars label
function signalLabel(s) {
  if (s > 70) return "Excellent";
  if (s > 50) return "Good";
  if (s > 30) return "Fair";
  return "Weak";
}

function SignalBars({ signal }) {
  const bars = signal > 70 ? 4 : signal > 50 ? 3 : signal > 30 ? 2 : 1;
  return (
    <Box sx={{ display: "flex", alignItems: "flex-end", gap: "2px", height: 16 }}>
      {[1, 2, 3, 4].map(b => (
        <Box
          key={b}
          sx={{
            width: 4,
            height: b * 4,
            borderRadius: "1px",
            bgcolor: b <= bars ? "primary.main" : "action.disabled",
          }}
        />
      ))}
    </Box>
  );
}

// ─── Step 0: WiFi ────────────────────────────────────────────────────────────

function WifiStep({ value, onChange, onNext }) {
  const [networks, setNetworks]     = useState([]);
  const [scanning, setScanning]     = useState(true);
  const [showPass, setShowPass]     = useState(false);
  const [manual, setManual]         = useState(false);

  const scan = useCallback(async () => {
    setScanning(true);
    try {
      const res = await fetch("/api/setup/wifi/scan");
      const data = await res.json();
      setNetworks(data.networks || []);
    } catch {
      setNetworks([]);
    } finally {
      setScanning(false);
    }
  }, []);

  // Initial scan
  useEffect(() => { scan(); }, [scan]);

  // Auto-rescan every 20 s until the user picks a network
  useEffect(() => {
    if (value.ssid || manual) return;
    const id = setInterval(scan, 20000);
    return () => clearInterval(id);
  }, [scan, value.ssid, manual]);

  const selectNetwork = (ssid) => onChange({ ...value, ssid, password: "" });
  const canAdvance    = value.ssid && (value.password || networks.find(n => n.ssid === value.ssid && n.security === "none"));

  return (
    <Stack spacing={3}>
      <Typography variant="h6">Choose your WiFi network</Typography>

      {/* Scan list */}
      {!manual && (
        <Box>
          <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mb: 1 }}>
            <Typography variant="body2" color="text.secondary">
              {scanning
                ? "Scanning…"
                : networks.length > 0
                  ? `${networks.length} network${networks.length !== 1 ? "s" : ""} found`
                  : "No networks found — retrying every 20 s"}
            </Typography>
            <IconButton size="small" onClick={scan} disabled={scanning}>
              <RefreshIcon fontSize="small" />
            </IconButton>
          </Box>

          {scanning && <LinearProgress sx={{ mb: 1 }} />}

          <Stack spacing={1} sx={{ maxHeight: 280, overflowY: "auto" }}>
            {networks.map(n => (
              <Card
                key={n.ssid}
                variant="outlined"
                sx={{ borderColor: value.ssid === n.ssid ? "primary.main" : "divider" }}
              >
                <CardActionArea onClick={() => selectNetwork(n.ssid)}>
                  <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
                    <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                        <WifiIcon fontSize="small" color={value.ssid === n.ssid ? "primary" : "action"} />
                        <Typography variant="body1">{n.ssid}</Typography>
                      </Box>
                      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                        <Typography variant="caption" color="text.secondary">
                          {signalLabel(n.signal)}
                        </Typography>
                        <SignalBars signal={n.signal} />
                      </Box>
                    </Box>
                  </CardContent>
                </CardActionArea>
              </Card>
            ))}

            {!scanning && networks.length === 0 && (
              <Box sx={{ textAlign: "center", py: 2 }}>
                <WifiOffIcon sx={{ color: "text.disabled", mb: 1 }} />
                <Typography variant="body2" color="text.secondary">No networks found</Typography>
              </Box>
            )}
          </Stack>

          <Button
            size="small"
            sx={{ mt: 1 }}
            onClick={() => { setManual(true); onChange({ ...value, ssid: "" }); }}
          >
            Enter network name manually
          </Button>
        </Box>
      )}

      {/* Manual SSID entry */}
      {manual && (
        <TextField
          label="Network name (SSID)"
          value={value.ssid}
          onChange={e => onChange({ ...value, ssid: e.target.value })}
          fullWidth
          autoFocus
          InputProps={{
            startAdornment: <InputAdornment position="start"><RouterIcon /></InputAdornment>,
          }}
        />
      )}
      {manual && (
        <Button size="small" onClick={() => { setManual(false); scan(); }}>
          ← Show scanned networks
        </Button>
      )}

      {/* Password field — shown once a network is selected */}
      {value.ssid && (
        <TextField
          label={`Password for "${value.ssid}"`}
          type={showPass ? "text" : "password"}
          value={value.password}
          onChange={e => onChange({ ...value, password: e.target.value })}
          fullWidth
          autoFocus={!!value.ssid}
          InputProps={{
            endAdornment: (
              <InputAdornment position="end">
                <IconButton onClick={() => setShowPass(s => !s)} edge="end">
                  {showPass ? <VisibilityOff /> : <Visibility />}
                </IconButton>
              </InputAdornment>
            ),
          }}
        />
      )}

      <Button
        variant="contained"
        disabled={!canAdvance}
        onClick={onNext}
        size="large"
      >
        Next
      </Button>
    </Stack>
  );
}

// ─── Step 1: Device info ─────────────────────────────────────────────────────

function DeviceStep({ value, onChange, onNext, onBack, submitting }) {
  const canAdvance = value.device_name.trim() && value.city.trim();
  return (
    <Stack spacing={3}>
      <Typography variant="h6">Name this device</Typography>
      <TextField
        label="Device name"
        placeholder="e.g. Living Room, Reception, Lobby"
        value={value.device_name}
        onChange={e => onChange({ ...value, device_name: e.target.value })}
        fullWidth
        autoFocus
        helperText="Used as the hostname and in the dashboard header"
      />
      <TextField
        label="City or ZIP code"
        placeholder="e.g. Seattle or 98101"
        value={value.city}
        onChange={e => onChange({ ...value, city: e.target.value })}
        fullWidth
        helperText="Used for the weather display"
      />
      <Stack direction="row" spacing={2}>
        {onBack && <Button variant="outlined" onClick={onBack} disabled={submitting} fullWidth>Back</Button>}
        <Button variant="contained" disabled={!canAdvance || submitting} onClick={onNext} fullWidth>
          {submitting ? <CircularProgress size={20} color="inherit" /> : "Apply Settings"}
        </Button>
      </Stack>
    </Stack>
  );
}

// ─── Step 2: Applying / Result ───────────────────────────────────────────────

function ApplyingStep({ success, error, ssid, deviceName, alreadyConnected }) {
  const hostname = (deviceName || "dashboard")
    .toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "");
  const dashboardUrl = `http://${hostname}.local`;
  if (error) {
    return (
      <Stack spacing={2} alignItems="center" sx={{ textAlign: "center" }}>
        <ErrorIcon color="error" sx={{ fontSize: 56 }} />
        <Typography variant="h6" color="error">Setup failed</Typography>
        <Typography variant="body2" color="text.secondary">{error}</Typography>
        <Button variant="outlined" onClick={() => window.location.reload()}>
          Try again
        </Button>
      </Stack>
    );
  }

  if (success) {
    return (
      <Stack spacing={3} alignItems="center" sx={{ textAlign: "center" }}>
        <CheckCircleIcon color="success" sx={{ fontSize: 56 }} />
        <Typography variant="h6">{alreadyConnected ? "Setup complete!" : "Connected!"}</Typography>
        {!alreadyConnected && (
          <Typography variant="body2" color="text.secondary">
            The Pi is connecting to <strong>{ssid}</strong> and rebooting.
          </Typography>
        )}
        <Divider sx={{ width: "100%" }} />
        {!alreadyConnected && (
          <Typography variant="body2" color="text.secondary">
            Reconnect your device to your home WiFi, then visit:
          </Typography>
        )}
        {alreadyConnected && (
          <Typography variant="body2" color="text.secondary">
            Settings saved. The Pi is rebooting — it will be available shortly at:
          </Typography>
        )}
        <Typography
          variant="h6"
          sx={{ fontFamily: "monospace", bgcolor: "action.hover", px: 2, py: 1, borderRadius: 1 }}
        >
          {dashboardUrl}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          It may take a minute after reboot before the dashboard is available.
        </Typography>
      </Stack>
    );
  }

  return (
    <Stack spacing={2} alignItems="center" sx={{ textAlign: "center" }}>
      <CircularProgress size={48} />
      <Typography variant="h6">Applying settings…</Typography>
      <Typography variant="body2" color="text.secondary">
        {alreadyConnected
          ? "Saving configuration and rebooting…"
          : <>Saving configuration and connecting to <strong>{ssid}</strong></>}
      </Typography>
    </Stack>
  );
}

// ─── Main wizard ─────────────────────────────────────────────────────────────

export default function Setup() {
  const [step, setStep]             = useState(null); // null = loading status
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult]         = useState(null); // null | {success, error}
  const [netStatus, setNetStatus]   = useState(null); // {connected, connection_type, ssid}
  const [rebooting, setRebooting]   = useState(false);

  const [form, setForm] = useState({
    ssid:        "",
    password:    "",
    device_name: "",
    city:        "",
  });

  useEffect(() => {
    fetch("/api/setup/status")
      .then(r => r.json())
      .then(data => {
        const connected = data.connected === true;
        setNetStatus({ connected, connection_type: data.connection_type, ssid: data.ssid });
        setStep(connected ? 1 : 0);
      })
      .catch(() => {
        setNetStatus({ connected: false, connection_type: "none", ssid: null });
        setStep(0);
      });
  }, []);

  const alreadyConnected = netStatus?.connected === true;

  const handleSubmit = async () => {
    setSubmitting(true);
    setStep(2);
    try {
      const res = await fetch("/api/setup/configure", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ ...form, activation_code: "", already_connected: alreadyConnected }),
      });
      const data = await res.json();
      setResult(data.success ? { success: true } : { error: data.error || "Unknown error" });
    } catch (e) {
      setResult({ error: e.message });
    } finally {
      setSubmitting(false);
    }
  };

  const handleReboot = async () => {
    if (!window.confirm("Reboot the Pi now?")) return;
    setRebooting(true);
    try {
      await fetch("/api/setup/reboot", { method: "POST" });
    } catch {
      // expected — Pi reboots and drops the connection
    }
  };

  if (step === null) {
    return (
      <Box sx={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <CircularProgress />
      </Box>
    );
  }

  const connectionBanner = alreadyConnected && step < 3 ? (
    <Box
      sx={{
        display:      "flex",
        alignItems:   "center",
        gap:          1,
        bgcolor:      "success.main",
        color:        "success.contrastText",
        borderRadius: 1,
        px:           2,
        py:           1,
        mb:           3,
      }}
    >
      <CheckCircleIcon fontSize="small" />
      <Typography variant="body2">
        {netStatus.connection_type === "ethernet"
          ? "Already connected via ethernet — WiFi setup skipped"
          : `Already connected to "${netStatus.ssid}" — WiFi setup skipped`}
      </Typography>
    </Box>
  ) : null;

  return (
    <Box
      sx={{
        minHeight:      "100vh",
        display:        "flex",
        alignItems:     "center",
        justifyContent: "center",
        bgcolor:        "background.default",
        p:              2,
      }}
    >
      <Container maxWidth="sm">
        {/* Header */}
        <Stack spacing={1} alignItems="center" sx={{ mb: 4 }}>
          <RouterIcon sx={{ fontSize: 40, color: "primary.main" }} />
          <Typography variant="h5" fontWeight={600}>Dashboard Setup</Typography>
          <Typography variant="body2" color="text.secondary">
            Get your Pi connected and ready to go
          </Typography>
        </Stack>

        {/* Persistent reboot button */}
        {step !== 2 && (
          <Box sx={{ display: "flex", justifyContent: "flex-end", mb: 1 }}>
            <Button
              size="small"
              variant="outlined"
              color="warning"
              onClick={handleReboot}
              disabled={rebooting || submitting}
            >
              {rebooting ? "Rebooting…" : "Reboot Pi"}
            </Button>
          </Box>
        )}

        {/* Stepper */}
        <Stepper activeStep={step} alternativeLabel sx={{ mb: 4 }}>
          {STEPS.map(label => (
            <Step key={label}>
              <StepLabel>{label}</StepLabel>
            </Step>
          ))}
        </Stepper>

        {/* Already-connected notice */}
        {connectionBanner}

        {/* Step content */}
        <Card variant="outlined" sx={{ p: 3 }}>
          {step === 0 && (
            <WifiStep
              value={form}
              onChange={setForm}
              onNext={() => setStep(1)}
            />
          )}
          {step === 1 && (
            <DeviceStep
              value={form}
              onChange={setForm}
              onNext={handleSubmit}
              onBack={alreadyConnected ? null : () => setStep(0)}
              submitting={submitting}
            />
          )}
          {step === 2 && (
            <ApplyingStep
              success={result?.success}
              error={result?.error}
              ssid={form.ssid}
              deviceName={form.device_name}
              alreadyConnected={alreadyConnected}
            />
          )}
        </Card>
      </Container>
    </Box>
  );
}
