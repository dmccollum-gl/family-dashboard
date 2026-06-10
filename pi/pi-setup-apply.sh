#!/bin/bash
set -uo pipefail

SSID="${1:-}"
DEVICE_NAME="${2:-dashboard-setup}"
# stdin protocol (one value per line, secrets never passed via argv):
#   line 1: WiFi password        (empty when already connected)
#   line 2: terminal login user  (empty to skip setting a login password)
#   line 3: terminal login pass   (empty to skip)
read -r PASSWORD   2>/dev/null || PASSWORD=""
read -r LOGIN_USER 2>/dev/null || LOGIN_USER=""
read -r LOGIN_PASS 2>/dev/null || LOGIN_PASS=""

# Temporary debug log — lengths only, never the secrets themselves.
echo "[setup-apply] DEBUG: SSID='${SSID}' PASS_LEN=${#PASSWORD} DEVICE='${DEVICE_NAME}' LOGIN_USER='${LOGIN_USER}' LOGIN_PASS_LEN=${#LOGIN_PASS}" >> /opt/dashboard/apply-debug.log 2>/dev/null || true

HOTSPOT_CON="dashboard-hotspot"
WIFI_CON="dashboard-wifi"
DNSMASQ_SHARED_DIR="/etc/NetworkManager/dnsmasq-shared.d"

# -- System fixes (idempotent) ------------------------------------------------
# nginx: no-cache headers so captive portal browsers always get a fresh bundle
cat > /etc/nginx/sites-available/dashboard << 'NGINX'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    root /opt/dashboard/frontend-dist;
    index index.html;

    location = /index.html {
        try_files $uri =404;
        add_header Cache-Control "no-store, no-cache, must-revalidate";
        add_header Pragma "no-cache";
        expires 0;
    }

    location / {
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-store, no-cache, must-revalidate";
        add_header Pragma "no-cache";
        expires 0;
    }

    location /api/ {
        proxy_pass         http://127.0.0.1:8001;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 60s;
    }
}
NGINX
/usr/sbin/nginx -t 2>/dev/null && systemctl reload nginx 2>/dev/null || true
echo "[setup-apply] nginx config updated."

# sudoers: guarantee no-TTY requirement for dashboard sudo
cat > /etc/sudoers.d/dashboard-setup << 'SUDOERS'
Defaults:dashboard !requiretty
dashboard ALL=(ALL) NOPASSWD: /opt/dashboard/pi-setup-apply.sh
SUDOERS
chmod 440 /etc/sudoers.d/dashboard-setup

# Statically enable getty@tty1 autologin
systemctl enable getty@tty1.service 2>/dev/null || true

# Remove NoNewPrivileges from backend service -- it blocks sudo from gaining root,
# which prevents this apply script from being called from uvicorn at all.
SERVICE_FILE=/etc/systemd/system/dashboard-backend.service
if grep -q 'NoNewPrivileges' "$SERVICE_FILE" 2>/dev/null; then
  sed -i '/NoNewPrivileges/d' "$SERVICE_FILE"
  systemctl daemon-reload 2>/dev/null || true
  echo "[setup-apply] Removed NoNewPrivileges from backend service."
fi

# Set system timezone to Pacific (matches OWM location 94556 — Danville, CA)
timedatectl set-timezone America/Los_Angeles 2>/dev/null || true
echo "[setup-apply] Timezone set to America/Los_Angeles."

# Reduce GPU memory to 16 MB to free RAM for uvicorn + display.py
CONFIG_TXT=/boot/firmware/config.txt
if [ -f "$CONFIG_TXT" ]; then
  sed -i '/^gpu_mem=/d' "$CONFIG_TXT"
  echo "gpu_mem=16" >> "$CONFIG_TXT"
  echo "[setup-apply] GPU memory set to 16 MB."
fi

# Fix display service to wait for backend API before launching Pygame
cat > /etc/systemd/system/dashboard-display.service << 'DISPLAY_SVC'
[Unit]
Description=Family Dashboard - Pygame Kiosk Display
After=dashboard-backend.service network-online.target

[Service]
Type=simple
User=dashboard
Group=video

WorkingDirectory=/opt/dashboard/backend

Environment="PATH=/opt/dashboard/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PYTHONUNBUFFERED=1"
Environment="SDL_VIDEODRIVER=kmsdrm"
Environment="SDL_RENDER_DRIVER=software"
Environment="SDL_AUDIODRIVER=dummy"

ExecStartPre=/bin/bash -c 'for i in $(seq 1 60); do curl -sf http://127.0.0.1:8001/api/health >/dev/null 2>&1 && exit 0; sleep 3; done; exit 1'
ExecStart=/opt/dashboard/venv/bin/python3 display.py --fullscreen

Restart=always
RestartSec=30
TimeoutStopSec=5
StartLimitIntervalSec=300
StartLimitBurst=3

StandardOutput=journal
StandardError=journal
SyslogIdentifier=dashboard-display

PrivateTmp=no
PrivateDevices=no

[Install]
WantedBy=multi-user.target
DISPLAY_SVC
systemctl daemon-reload 2>/dev/null || true
echo "[setup-apply] Display service updated (waits for API health)."

# Fix .bash_profile to wait for API before starting display.py
DASH_HOME=/home/dashboard
cat > "${DASH_HOME}/.bash_profile" << 'PROFILE'
[ -f ~/.bashrc ] && . ~/.bashrc

if [ "$(tty)" = "/dev/tty1" ]; then
    export XDG_RUNTIME_DIR=/run/user/$(id -u)
    mkdir -p "$XDG_RUNTIME_DIR"
    chmod 0700 "$XDG_RUNTIME_DIR"

    for _card in /dev/dri/card0 /dev/dri/card1; do
        [ -r "$_card" ] && export SDL_VIDEO_KMSDRM_DEVICE="$_card" && break
    done

    export TZ=America/Los_Angeles
    export SDL_VIDEODRIVER=kmsdrm
    export SDL_VIDEO_KMSDRM_ALLOW_MODESET=1
    export SDL_RENDER_DRIVER=software
    export SDL_AUDIODRIVER=dummy
    export PYGAME_HIDE_SUPPORT_PROMPT=1

    until curl -sf --max-time 5 http://127.0.0.1:8001/api/health >/dev/null 2>&1; do
        sleep 5
    done

    # Don't start display.py while in setup mode (hotspot active).
    # Keeping display.py idle avoids thread-pool saturation in uvicorn,
    # so the captive-portal setup page is always responsive.
    while true; do
        STATUS=$(curl -sf --max-time 5 http://127.0.0.1:8001/api/setup/status 2>/dev/null)
        echo "$STATUS" | grep -q '"setup_mode":false' && break
        sleep 10
    done
    # Brief warmup so uvicorn pages are settled before Pygame loads.
    sleep 15

    cd /opt/dashboard/backend
    while true; do
        /opt/dashboard/venv/bin/python3 display.py --fullscreen 2>&1 \
            | logger -t dashboard-display
        RC=$?
        logger -t dashboard-display "exited rc=$RC, restarting in 5s"
        sleep 5
    done
fi
PROFILE
chown dashboard:dashboard "${DASH_HOME}/.bash_profile"
echo "[setup-apply] .bash_profile updated (waits for API health + 60s warmup)."

# -- Terminal / SSH login credentials (set by the owner during setup) ---------
# The image ships with the dashboard account locked (no default password), so
# this is what enables SSH/terminal login. printf is a bash builtin, so the
# password is never exposed in the process list.
if [ -n "$LOGIN_PASS" ]; then
  TARGET_USER="${LOGIN_USER:-dashboard}"
  if [ "$TARGET_USER" != "dashboard" ] && ! id "$TARGET_USER" &>/dev/null; then
    useradd -m -s /bin/bash -G sudo "$TARGET_USER" 2>/dev/null \
      && echo "[setup-apply] Created login user '$TARGET_USER'."
  fi
  if printf '%s:%s\n' "$TARGET_USER" "$LOGIN_PASS" | chpasswd 2>/dev/null; then
    passwd -u "$TARGET_USER" 2>/dev/null || true   # unlock (image ships locked)
    [ "$TARGET_USER" != "dashboard" ] && usermod -aG sudo "$TARGET_USER" 2>/dev/null || true
    echo "[setup-apply] Terminal login password set for '$TARGET_USER'."
  else
    echo "[setup-apply] WARNING: failed to set password for '$TARGET_USER'."
  fi
else
  echo "[setup-apply] No login password provided — leaving account unchanged."
fi

# -- WiFi / hostname ----------------------------------------------------------
rm -f "$DNSMASQ_SHARED_DIR/captive-portal.conf"

HOSTNAME=$(echo "$DEVICE_NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd '[:alnum:]-')
echo "$HOSTNAME" > /etc/hostname
sed -i "s/127\.0\.1\.1.*/127.0.1.1\t${HOSTNAME}/" /etc/hosts 2>/dev/null || \
  echo "127.0.1.1 $HOSTNAME" >> /etc/hosts
hostnamectl set-hostname "$HOSTNAME" 2>/dev/null || true

if [ -n "$SSID" ]; then
  echo "[setup-apply] Configuring WiFi: $SSID"
  nmcli connection delete "$WIFI_CON" 2>/dev/null || true
  NM_ADD_OUT=$(nmcli connection add type wifi ifname wlan0 con-name "$WIFI_CON" \
      ssid "$SSID" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PASSWORD" \
      connection.autoconnect yes ipv4.method auto 2>&1)
  NM_ADD_RC=$?
  echo "[setup-apply] nmcli add rc=${NM_ADD_RC} out=${NM_ADD_OUT}" >> /opt/dashboard/apply-debug.log 2>/dev/null || true
  if [ $NM_ADD_RC -eq 0 ]; then
    nmcli connection down "$HOTSPOT_CON" 2>/dev/null || true
    sleep 1
    if nmcli connection up "$WIFI_CON" 2>&1 | tee -a /opt/dashboard/apply-debug.log; then
      echo "[setup-apply] WiFi activated."
    else
      echo "[setup-apply] WARNING: WiFi failed -- will reboot anyway."
    fi
  else
    echo "[setup-apply] ERROR: nmcli add failed -- will reboot anyway."
  fi
else
  echo "[setup-apply] No SSID -- skipping WiFi."
fi

# -- Start cloudflared if the device has been provisioned ---------------------
CFG=/opt/dashboard/backend/dashboard_config.json
if python3 -c "
import json, sys
try:
    d = json.load(open('$CFG'))
    sys.exit(0 if d.get('provisioned') and d.get('tunnel_token') else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
  echo "[setup-apply] Provisioned — starting cloudflared tunnel..."
  systemctl start cloudflared.service 2>/dev/null || true
  sleep 3
  {
    echo "=== cloudflared status at $(date) ==="
    systemctl status cloudflared.service --no-pager 2>&1
  } >> /var/log/dashboard-cloudflared.log 2>/dev/null || true
  echo "[setup-apply] cloudflared status logged to /var/log/dashboard-cloudflared.log"
else
  echo "[setup-apply] Device not provisioned — skipping cloudflared start."
fi

echo "[setup-apply] Rebooting in 5s..."
sleep 5
systemctl reboot
