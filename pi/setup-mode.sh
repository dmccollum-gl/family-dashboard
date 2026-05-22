#!/bin/bash
# Runs on every boot (via dashboard-setup.service).
# Starts a WiFi hotspot if the device has never been configured, OR if it has
# been configured but cannot reach any network (recovery mode).

CONFIGURED_FLAG="/opt/dashboard/.configured"
HOTSPOT_CON="dashboard-hotspot"
HOTSPOT_SSID="Dashboard-Setup"
HOTSPOT_IP="10.42.0.1/24"
DNSMASQ_SHARED_DIR="/etc/NetworkManager/dnsmasq-shared.d"

# -- Check configured flag + network connectivity ----------------------------
if [ -f "$CONFIGURED_FLAG" ]; then
  echo "[setup-mode] Configured flag exists. Waiting up to 20s for network..."
  CONNECTED=0
  for i in $(seq 1 20); do
    sleep 1
    ACTIVE=$(nmcli -t -f NAME,STATE con show --active 2>/dev/null | \
      grep -v "^${HOTSPOT_CON}:" | grep ":activated" | head -1)
    if [ -n "$ACTIVE" ]; then
      echo "[setup-mode] Network up: $ACTIVE -- skipping hotspot."
      CONNECTED=1
      break
    fi
  done
  if [ "$CONNECTED" = "1" ]; then
    exit 0
  fi
  echo "[setup-mode] No network after 20s -- starting recovery hotspot."
else
  echo "[setup-mode] Not configured. Waiting for NetworkManager to settle..."
  sleep 8
fi

# -- Set WiFi regulatory domain -----------------------------------------------
# iw reg set covers the current session; /etc/default/crda persists across boots.
iw reg set US 2>/dev/null || true
echo "[setup-mode] Regulatory domain: $(iw reg get 2>/dev/null | head -2 || echo unknown)"

echo "[setup-mode] Starting setup hotspot..."

# Captive-portal: redirect all DNS queries to the Pi's AP address.
mkdir -p "$DNSMASQ_SHARED_DIR"
cat > "$DNSMASQ_SHARED_DIR/captive-portal.conf" << 'EOF'
address=/#/10.42.0.1
EOF

# Disconnect wlan0 from any existing association so it can enter AP mode.
nmcli device disconnect wlan0 2>/dev/null || true
sleep 2

# Delete any stale hotspot connection and recreate it fresh.
# NOTE: do NOT include wifi.regulatory-domain here  --  it is not a valid nmcli
# property and will cause the entire connection add to fail silently.
nmcli connection delete "$HOTSPOT_CON" 2>/dev/null || true

if ! nmcli connection add \
    type         wifi            \
    ifname       wlan0           \
    con-name     "$HOTSPOT_CON"  \
    ssid         "$HOTSPOT_SSID" \
    802-11-wireless.mode ap      \
    802-11-wireless.band bg      \
    ipv4.method  shared          \
    ipv4.addresses "$HOTSPOT_IP"; then
  echo "[setup-mode] ERROR: nmcli connection add failed  --  cannot create hotspot."
  exit 0
fi

echo "[setup-mode] Hotspot connection created. Bringing it up..."

for attempt in 1 2 3 4 5; do
  if nmcli connection up "$HOTSPOT_CON" 2>&1; then
    echo "[setup-mode] Hotspot '$HOTSPOT_SSID' is UP at ${HOTSPOT_IP%/*}"
    iw dev wlan0 info 2>/dev/null | grep -E "type|ssid" || true
    exit 0
  fi
  echo "[setup-mode] Attempt $attempt/5 failed. Waiting 5s..."
  sleep 5
done

echo "[setup-mode] Could not bring up hotspot after 5 attempts."
nmcli device status 2>/dev/null || true
exit 0
