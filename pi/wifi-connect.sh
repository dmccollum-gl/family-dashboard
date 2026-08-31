#!/bin/bash
# wifi-connect.sh <ssid>   (password on stdin; empty line = open network)
#
# Switch the Pi to a new WiFi network WITHOUT permanently orphaning it: the new
# connection is built and activated on a temporary profile first, and only if it
# comes up is it promoted to the canonical profile and the old one removed. If it
# fails (e.g. wrong password), the temporary profile is deleted and the previous
# network is brought back up.
#
# Called by the backend via:  sudo -n bash /opt/dashboard/pi/wifi-connect.sh <ssid>
# ---------------------------------------------------------------------------
set -uo pipefail

SSID="${1:-}"
read -r PASSWORD 2>/dev/null || PASSWORD=""
IFACE=wlan0
CANON="dashboard-wifi"          # canonical, autoconnecting profile name
TMP="dashboard-wifi-pending"    # temporary profile we test first

[ -z "$SSID" ] && { echo "ERROR: no SSID"; exit 1; }

# Remember the currently-active WiFi connection so we can roll back to it.
PREV=$(nmcli -t -f NAME,TYPE,STATE con show --active 2>/dev/null \
        | awk -F: '$2=="802-11-wireless" && $3=="activated"{print $1; exit}')

nmcli connection delete "$TMP" 2>/dev/null || true

if [ -n "$PASSWORD" ]; then
  nmcli connection add type wifi ifname "$IFACE" con-name "$TMP" ssid "$SSID" \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PASSWORD" \
    connection.autoconnect no ipv4.method auto >/dev/null 2>&1
else
  nmcli connection add type wifi ifname "$IFACE" con-name "$TMP" ssid "$SSID" \
    connection.autoconnect no ipv4.method auto >/dev/null 2>&1
fi

if timeout 45 nmcli connection up "$TMP" >/dev/null 2>&1; then
  # New network is up — promote it and drop the old profile(s).
  [ -n "$PREV" ] && [ "$PREV" != "$TMP" ] && nmcli connection delete "$PREV" 2>/dev/null || true
  [ "$CANON" != "$PREV" ] && nmcli connection delete "$CANON" 2>/dev/null || true
  nmcli connection modify "$TMP" connection.id "$CANON" \
    connection.autoconnect yes connection.autoconnect-priority 10 >/dev/null 2>&1
  echo "WIFI_OK $SSID"
  exit 0
else
  # New network failed — clean up and restore the previous one.
  nmcli connection delete "$TMP" 2>/dev/null || true
  [ -n "$PREV" ] && nmcli connection up "$PREV" >/dev/null 2>&1 || true
  echo "WIFI_FAIL $SSID"
  exit 2
fi
