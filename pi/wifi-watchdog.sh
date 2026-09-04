#!/bin/bash
# wifi-watchdog.sh -- runtime WiFi fallback for a *configured* dashboard.
#
# Runs every ~60s from dashboard-wifi-watchdog.timer. setup-mode.sh only checks
# connectivity once, at boot; this script covers the rest of the device's life:
# if the network drops later (router reboot, moved device, changed WiFi
# password) the dashboard would otherwise be silently unreachable with no way to
# fix it. This brings up the "Dashboard-Setup" recovery hotspot after a
# sustained outage so someone can rejoin and re-enter WiFi at http://10.42.0.1.
#
# While the hotspot is up it periodically drops it and retries the saved WiFi,
# so a transient outage self-heals without anyone having to touch the device.
set -u

CONFIGURED_FLAG="/opt/dashboard/.configured"
HOTSPOT_CON="dashboard-hotspot"
STATE_DIR="/run/dashboard-wifi"
DOWN_STAMP="$STATE_DIR/down-since"
HOTSPOT_STAMP="$STATE_DIR/hotspot-since"
HERE="$(cd "$(dirname "$0")" && pwd)"

DOWN_THRESHOLD=150   # sustained seconds offline before starting the hotspot
RETRY_AFTER=900      # seconds on the hotspot before retrying the saved WiFi
RECONNECT_WAIT=45    # seconds to wait for autoconnect after dropping the hotspot

log() { echo "[wifi-watchdog] $*"; }
now() { date +%s; }

mkdir -p "$STATE_DIR"

# Only manage a configured device. Until first-time setup completes,
# dashboard-setup.service owns the radio.
[ -f "$CONFIGURED_FLAG" ] || exit 0

hotspot_active() {
  nmcli -t -f NAME,STATE con show --active 2>/dev/null \
    | grep -q "^${HOTSPOT_CON}:activated"
}

# True when a real (non-hotspot) connection is activated -- WiFi or ethernet.
network_up() {
  nmcli -t -f NAME,TYPE,STATE con show --active 2>/dev/null \
    | awk -F: -v h="$HOTSPOT_CON" \
        '$3=="activated" && $1!=h && $2!="loopback" {found=1} END{exit found?0:1}'
}

# -- Case A: connected -- clear all state and we're done ----------------------
if network_up; then
  rm -f "$DOWN_STAMP" "$HOTSPOT_STAMP"
  exit 0
fi

# -- Case B: hotspot already up -- periodically retry the saved WiFi ----------
if hotspot_active; then
  [ -f "$HOTSPOT_STAMP" ] || echo "$(now)" > "$HOTSPOT_STAMP"
  since=$(cat "$HOTSPOT_STAMP" 2>/dev/null || echo 0)
  age=$(( $(now) - since ))
  if [ "$age" -lt "$RETRY_AFTER" ]; then
    exit 0
  fi
  log "Hotspot up for ${age}s -- dropping it to retry the saved WiFi."
  nmcli connection down "$HOTSPOT_CON" 2>/dev/null || true
  nmcli device wifi rescan ifname wlan0 2>/dev/null || true
  for i in $(seq 1 "$RECONNECT_WAIT"); do
    sleep 1
    if network_up; then
      log "Reconnected to the saved WiFi -- recovery complete."
      rm -f "$DOWN_STAMP" "$HOTSPOT_STAMP"
      exit 0
    fi
  done
  log "Saved WiFi still unreachable -- bringing the hotspot back."
  echo "$(now)" > "$HOTSPOT_STAMP"
  exec "$HERE/hotspot-up.sh"
fi

# -- Case C: no network and no hotspot -- start (or continue) the outage timer -
if [ ! -f "$DOWN_STAMP" ]; then
  echo "$(now)" > "$DOWN_STAMP"
  # First miss: nudge NetworkManager to reconnect before we give up on it.
  nmcli device wifi rescan ifname wlan0 2>/dev/null || true
  nmcli device connect wlan0 2>/dev/null || true
  log "Network down -- starting ${DOWN_THRESHOLD}s grace period before hotspot."
  exit 0
fi

since=$(cat "$DOWN_STAMP" 2>/dev/null || echo 0)
down_for=$(( $(now) - since ))
if [ "$down_for" -ge "$DOWN_THRESHOLD" ]; then
  log "No network for ${down_for}s -- starting recovery hotspot."
  echo "$(now)" > "$HOTSPOT_STAMP"
  exec "$HERE/hotspot-up.sh"
fi

log "No network for ${down_for}s (< ${DOWN_THRESHOLD}s) -- still waiting."
exit 0
