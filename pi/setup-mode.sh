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

# -- Per-device session secret ------------------------------------------------
# The image ships without a .env (baking one would share a signing key across
# every device flashed from it). This runs as root before dashboard-backend
# starts, so generate a unique SECRET_KEY on first boot if one isn't set yet.
# Idempotent: only writes when missing or still the placeholder, so logins
# survive reboots.
ENV_FILE="/opt/dashboard/backend/.env"
if ! grep -q '^SECRET_KEY=.\+' "$ENV_FILE" 2>/dev/null || grep -q '^SECRET_KEY=change-me' "$ENV_FILE" 2>/dev/null; then
  SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null)"
  if [ -n "$SECRET" ]; then
    touch "$ENV_FILE"
    sed -i '/^SECRET_KEY=/d' "$ENV_FILE" 2>/dev/null || true
    echo "SECRET_KEY=$SECRET" >> "$ENV_FILE"
    chown dashboard:dashboard "$ENV_FILE" 2>/dev/null || true
    chmod 600 "$ENV_FILE" 2>/dev/null || true
    echo "[setup-mode] Generated a per-device session secret."
  fi
fi

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
