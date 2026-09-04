#!/bin/bash
# setup-mode.sh -- runs once on every boot (via dashboard-setup.service).
#
# Starts the "Dashboard-Setup" hotspot if the device has never been configured,
# OR if it is configured but cannot reach any network within the boot window
# (boot-time recovery). Ongoing recovery *after* boot -- e.g. the router reboots
# or WiFi drops hours later -- is handled by wifi-watchdog.sh on a timer.
#
# The actual AP bring-up lives in hotspot-up.sh so setup-mode and the watchdog
# create the hotspot exactly one way.

CONFIGURED_FLAG="/opt/dashboard/.configured"
HOTSPOT_CON="dashboard-hotspot"
HERE="$(cd "$(dirname "$0")" && pwd)"

# -- Configured devices: give the real network a chance before falling back ---
if [ -f "$CONFIGURED_FLAG" ]; then
  echo "[setup-mode] Configured flag exists. Waiting up to 30s for network..."
  CONNECTED=0
  for i in $(seq 1 30); do
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
  echo "[setup-mode] No network after 30s -- starting recovery hotspot."
else
  echo "[setup-mode] Not configured. Waiting for NetworkManager to settle..."
  sleep 8
fi

# -- Hand off to the shared hotspot bring-up ----------------------------------
exec "$HERE/hotspot-up.sh"
