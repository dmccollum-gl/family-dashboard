#!/bin/bash
# hotspot-up.sh -- Bring up the "Dashboard-Setup" WiFi hotspot on wlan0.
#
# Idempotent: if the hotspot is already active it exits 0 without touching the
# radio. Shared by setup-mode.sh (first-boot / boot-time recovery) and
# wifi-watchdog.sh (runtime fallback when WiFi drops during normal operation),
# so the AP is created exactly one way.
set -u

HOTSPOT_CON="dashboard-hotspot"
HOTSPOT_SSID="Dashboard-Setup"
HOTSPOT_IP="10.42.0.1/24"
DNSMASQ_SHARED_DIR="/etc/NetworkManager/dnsmasq-shared.d"

log() { echo "[hotspot-up] $*"; }

# -- Already up? --------------------------------------------------------------
if nmcli -t -f NAME,STATE con show --active 2>/dev/null \
     | grep -q "^${HOTSPOT_CON}:activated"; then
  log "Hotspot already active -- nothing to do."
  exit 0
fi

# -- Regulatory domain (kernel refuses AP mode without a country code) --------
iw reg set US 2>/dev/null || true

# -- Captive-portal DNS: point every lookup at the AP address -----------------
mkdir -p "$DNSMASQ_SHARED_DIR"
cat > "$DNSMASQ_SHARED_DIR/captive-portal.conf" << 'EOF'
address=/#/10.42.0.1
EOF

# -- Release wlan0 from any station association so it can enter AP mode --------
nmcli device disconnect wlan0 2>/dev/null || true
sleep 2

# Recreate the hotspot connection fresh (drop any stale definition).
# NOTE: do NOT add wifi.regulatory-domain here -- it is not a valid nmcli
# property and makes the whole `connection add` fail silently.
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
  log "ERROR: nmcli connection add failed -- cannot create hotspot."
  exit 1
fi

log "Hotspot connection created. Bringing it up..."
for attempt in 1 2 3 4 5; do
  if nmcli connection up "$HOTSPOT_CON" 2>&1; then
    log "Hotspot '$HOTSPOT_SSID' is UP at ${HOTSPOT_IP%/*}"
    iw dev wlan0 info 2>/dev/null | grep -E "type|ssid" || true
    exit 0
  fi
  log "Attempt $attempt/5 failed. Waiting 5s..."
  sleep 5
done

log "Could not bring up hotspot after 5 attempts."
nmcli device status 2>/dev/null || true
exit 1
