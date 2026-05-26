import { useState, useEffect, useCallback } from "react";
import {
  Box, Button, Card, CardActionArea, CardContent,
  CircularProgress, Container, Divider, IconButton,
  InputAdornment, LinearProgress, Link, Stack, Step, StepLabel,
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
import VpnKeyIcon         from "@mui/icons-material/VpnKey";
import PublicIcon         from "@mui/icons-material/Public";

const STEPS = ["WiFi Network", "Device Info", "Applying"];

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

  useEffect(() => { scan(); }, [scan]);

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
  const canAdvance = value.device_name.trim() && value.city.trim() && value.activation_code.trim();
  return (
    <Stack spacing={3}>
      <Typography variant="h6">Name this device</Typography>
      <TextField
        label="Device name"
        placeholder="e.g. SmithFamily, Reception, Lobby"
        value={value.device_name}
        onChange={e => onChange({ ...value, device_name: e.target.value })}
        fullWidth
        autoFocus
        helperText="Used as the hostname — letters, numbers, and hyphens only"
      />
      <TextField
        label="City or ZIP code"
        placeholder="e.g. Seattle or 98101"
        value={value.city}
        onChange={e => onChange({ ...value, city: e.target.value })}
        fullWidth
        helperText="Used for the weather display"
      />
      <TextField
        label="Activation code"
        placeholder="XXXX-XXXX-XXXX-XXXX"
        value={value.activation_code}
        onChange={e => onChange({ ...value, activation_code: e.target.value.toUpperCase() })}
        fullWidth
        helperText="Single-use code included with your device — required to register your tunnel"
        InputProps={{
          startAdornment: <InputAdornment position="start"><VpnKeyIcon /></InputAdornment>,
        }}
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

function ApplyingStep({ success, error, errorType, fqdn, ssid, deviceName, alreadyConnected, onRetry }) {
  if (error) {
    let title = "Setup failed";
    let hint  = null;

    if (errorType === "invalid_activation_code") {
      title = "Invalid activation code";
      hint  = "Double-check the code and make sure it hasn't already been used.";
    } else if (errorType === "hostname_taken") {
      title = "Device name already taken";
      hint  = "Choose a different device name on the previous screen.";
    } else if (errorType === "api_error") {
      title = "Provisioning error";
      hint  = "A server-side error occurred. Please try again in a moment.";
    }

    return (
      <Stack spacing={2} alignItems="center" sx={{ textAlign: "center" }}>
        <ErrorIcon color="error" sx={{ fontSize: 56 }} />
        <Typography variant="h6" color="error">{title}</Typography>
        <Typography variant="body2" color="text.secondary">{error}</Typography>
        {hint && (
          <Typography variant="caption" color="text.secondary">{hint}</Typography>
        )}
        <Button variant="outlined" onClick={onRetry}>
          Try again
        </Button>
      </Stack>
    );
  }

  if (success) {
    const tunnelUrl = fqdn ? `https://${fqdn}` : null;
    return (
      <Stack spacing={3} alignItems="center" sx={{ textAlign: "center" }}>
        <CheckCircleIcon color="success" sx={{ fontSize: 56 }} />
        <Typography variant="h6">{alreadyConnected ? "Setup complete!" : "Connected!"}</Typography>

        {!alreadyConnected && (
          <Typography variant="body2" color="text.secondary">
            The Pi is connecting to <strong>{ssid}</strong> and rebooting.
          </Typography>
        )}
        {alreadyConnected && (
          <Typography variant="body2" color="text.secondary">
            Settings saved. The Pi is rebooting — it will be available shortly.
          </Typography>
        )}

        {tunnelUrl && (
          <>
            <Divider sx={{ width: "100%" }} />
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <PublicIcon color="primary" />
              <Typography variant="body2" fontWeight={600}>
                Your dashboard will be available at:
              </Typography>
            </Box>
            <Link
              href={tunnelUrl}
              target="_blank"
              rel="noopener noreferrer"
              underline="hover"
            >
              <Typography
                variant="h6"
                sx={{ fontFamily: "monospace", bgcolor: "action.hover", px: 2, py: 1, borderRadius: 1 }}
              >
                {fqdn}
              </Typography>
            </Link>
            <Typography variant="caption" color="text.secondary">
              The tunnel may take 1–2 minutes to come online after the Pi connects to WiFi.
            </Typography>
          </>
        )}

        {!tunnelUrl && (
          <>
            <Divider sx={{ width: "100%" }} />
            {!alreadyConnected && (
              <Typography variant="body2" color="text.secondary">
                Reconnect your device to your home WiFi, then visit:
              </Typography>
            )}
            <Typography
              variant="h6"
              sx={{ fontFamily: "monospace", bgcolor: "action.hover", px: 2, py: 1, borderRadius: 1 }}
            >
              {`http://${(deviceName || "dashboard").toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "")}.local`}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              It may take a minute after reboot before the dashboard is available.
            </Typography>
          </>
        )}
      </Stack>
    );
  }

  // In-progress
  return (
    <Stack spacing={2} alignItems="center" sx={{ textAlign: "center" }}>
      <CircularProgress size={48} />
      <Typography variant="h6">Registering your device…</Typography>
      <Typography variant="body2" color="text.secondary">
        Creating your tunnel and registering your FQDN — this takes a few seconds.
      </Typography>
      {!alreadyConnected && (
        <Typography variant="caption" color="text.secondary">
          Will then connect to <strong>{ssid}</strong> and reboot.
        </Typography>
      )}
    </Stack>
  );
}

// ─── Main wizard ─────────────────────────────────────────────────────────────

export default function Setup() {
  const [step, setStep]             = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult]         = useState(null); // {success, error?, errorType?, fqdn?}
  const [netStatus, setNetStatus]   = useState(null);
  const [rebooting, setRebooting]   = useState(false);

  const [form, setForm] = useState({
    ssid:            "",
    password:        "",
    device_name:     "",
    city:            "",
    activation_code: "",
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
    setResult(null);
    setStep(2);
    try {
      const res = await fetch("/api/setup/configure", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ ...form, already_connected: alreadyConnected }),
      });
      const data = await res.json();
      if (data.success) {
        setResult({ success: true, fqdn: data.fqdn || null });
      } else {
        setResult({
          error:     data.error || "Unknown error",
          errorType: data.error_type || null,
        });
      }
    } catch (e) {
      setResult({ error: e.message });
    } finally {
      setSubmitting(false);
    }
  };

  const handleRetry = () => {
    setResult(null);
    setStep(1);
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

  const connectionBanner = alreadyConnected && step < 2 ? (
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
        <Stack spacing={1} alignItems="center" sx={{ mb: 4 }}>
          <RouterIcon sx={{ fontSize: 40, color: "primary.main" }} />
          <Typography variant="h5" fontWeight={600}>Dashboard Setup</Typography>
          <Typography variant="body2" color="text.secondary">
            Get your Pi connected and ready to go
          </Typography>
        </Stack>

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

        <Stepper activeStep={step} alternativeLabel sx={{ mb: 4 }}>
          {STEPS.map(label => (
            <Step key={label}>
              <StepLabel>{label}</StepLabel>
            </Step>
          ))}
        </Stepper>

        {connectionBanner}

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
              errorType={result?.errorType}
              fqdn={result?.fqdn}
              ssid={form.ssid}
              deviceName={form.device_name}
              alreadyConnected={alreadyConnected}
              onRetry={handleRetry}
            />
          )}
        </Card>
      </Container>
    </Box>
  );
}
