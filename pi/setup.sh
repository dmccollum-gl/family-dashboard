#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# setup.sh  --  Install the Family Dashboard on an existing Raspberry Pi OS
#              (or any arm64 Debian / Ubuntu system)
#
# Run this ON the Pi after flashing standard Raspberry Pi OS Lite 64-bit:
#
#   curl -sSL https://raw.githubusercontent.com/dmccollum-gl/family-dashboard/main/pi/setup.sh | sudo bash
#
# Or copy this repo to the Pi and run:
#   sudo bash pi/setup.sh
#
# Tested on:
#   Raspberry Pi OS Bookworm Lite 64-bit (Debian 12)
#   Ubuntu Server 24.04 LTS arm64
#
# The script is idempotent -- safe to re-run after updates.
# -----------------------------------------------------------------------------
set -euo pipefail

# -- Must run as root ----------------------------------------------------------
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo bash $0"; exit 1; }

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}==>${NC} $*"; }
warn()  { echo -e "${YELLOW}WARN:${NC} $*"; }
error() { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }
step()  { echo -e "\n${GREEN}----------------------------------------${NC}"; info "$*"; }

# -- Detect source location ----------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
APP_DIR=/opt/dashboard
DASH_USER=dashboard
VENV="$APP_DIR/venv"

step "Family Dashboard Setup"
info "Architecture: $(uname -m)"
info "OS: $(. /etc/os-release && echo "$PRETTY_NAME")"

# -- System packages -----------------------------------------------------------
step "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  python3 python3-pip python3-venv python3-dev \
  libsdl2-dev libsdl2-image-dev libsdl2-ttf-dev libsdl2-mixer-dev \
  libopenjp2-7 libtiff6 libfreetype6 \
  sqlite3 \
  fonts-dejavu-core fonts-liberation \
  cec-utils \
  nginx git curl wget ca-certificates

# -- Create user ---------------------------------------------------------------
step "Creating '$DASH_USER' user"
if ! id "$DASH_USER" &>/dev/null; then
  useradd -m -s /bin/bash \
    -G video,render,gpio,audio,dialout,plugdev,netdev \
    "$DASH_USER"
  info "User created."
else
  info "User already exists -- updating group membership."
fi
for grp in video render audio gpio; do
  getent group "$grp" >/dev/null && usermod -aG "$grp" "$DASH_USER" || true
done

# -- Copy / update application files ------------------------------------------
step "Installing application files to $APP_DIR"
mkdir -p "$APP_DIR"

if [ -d "$PROJECT_DIR/backend" ]; then
  # Running from a local clone
  rsync -a --exclude '__pycache__' --exclude '*.pyc' \
    "$PROJECT_DIR/backend/" "$APP_DIR/backend/"
  info "Backend copied from local clone."
elif [ -d "$APP_DIR/backend" ]; then
  info "Using existing files at $APP_DIR/backend."
else
  error "No backend source found. Clone the repo first:\n  git clone https://github.com/dmccollum-gl/family-dashboard /tmp/dashboard && cd /tmp/dashboard && sudo bash pi/setup.sh"
fi

# Build or copy frontend
if [ -d "$PROJECT_DIR/frontend/dist" ]; then
  rsync -a "$PROJECT_DIR/frontend/dist/" "$APP_DIR/frontend-dist/"
  info "Frontend dist copied."
elif command -v npm &>/dev/null && [ -d "$PROJECT_DIR/frontend" ]; then
  info "Building frontend with npm..."
  pushd "$PROJECT_DIR/frontend" >/dev/null
  npm install --silent && npm run build
  popd >/dev/null
  rsync -a "$PROJECT_DIR/frontend/dist/" "$APP_DIR/frontend-dist/"
else
  warn "No pre-built frontend found and npm not available. Web UI will not work."
  warn "Build on another machine: cd frontend && npm run build, then re-run this script."
fi

chown -R "$DASH_USER:$DASH_USER" "$APP_DIR"

# -- Python venv ---------------------------------------------------------------
step "Setting up Python virtual environment"
[ -d "$VENV" ] || sudo -u "$DASH_USER" python3 -m venv "$VENV"
sudo -u "$DASH_USER" "$VENV/bin/pip" install --upgrade --quiet pip wheel
sudo -u "$DASH_USER" "$VENV/bin/pip" install --quiet \
  -r "$APP_DIR/backend/requirements.txt"
sudo -u "$DASH_USER" "$VENV/bin/pip" install --quiet pygame-ce uvloop httptools
info "Python packages installed."

# -- Session secret ------------------------------------------------------------
# SECRET_KEY signs the login session cookie. Generate a unique random key on
# first boot and persist it. It is regenerated only if missing or still the
# placeholder — never on a normal update, so existing logins survive upgrades.
step "Ensuring a unique session secret"
ENV_FILE="$APP_DIR/backend/.env"
touch "$ENV_FILE"
if ! grep -q '^SECRET_KEY=.\+' "$ENV_FILE" || grep -q '^SECRET_KEY=change-me' "$ENV_FILE"; then
  SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  sed -i '/^SECRET_KEY=/d' "$ENV_FILE"
  echo "SECRET_KEY=$SECRET" >> "$ENV_FILE"
  info "Generated a new session secret."
else
  info "Existing session secret preserved."
fi
chown "$DASH_USER:$DASH_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

# -- Default config ------------------------------------------------------------
if [ ! -f "$APP_DIR/backend/dashboard_config.json" ]; then
  if [ -f "$APP_DIR/backend/dashboard_config.example.json" ]; then
    cp "$APP_DIR/backend/dashboard_config.example.json" \
       "$APP_DIR/backend/dashboard_config.json"
    chown "$DASH_USER:$DASH_USER" "$APP_DIR/backend/dashboard_config.json"
    warn "Default config installed. Edit $APP_DIR/backend/dashboard_config.json"
    warn "Then: sudo systemctl restart dashboard-backend"
  else
    echo "{}" > "$APP_DIR/backend/dashboard_config.json"
    chown "$DASH_USER:$DASH_USER" "$APP_DIR/backend/dashboard_config.json"
  fi
fi

# -- cloudflared (Cloudflare Tunnel client) ------------------------------------
step "Installing cloudflared"
CF_BIN=/usr/local/bin/cloudflared
if [ ! -f "$CF_BIN" ]; then
  ARCH=$(uname -m)
  case "$ARCH" in
    aarch64|arm64) CF_ARCH="arm64" ;;
    armv7l|armhf)  CF_ARCH="arm"   ;;
    x86_64)        CF_ARCH="amd64" ;;
    *)             CF_ARCH="arm64" ;;
  esac
  CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}"
  info "Downloading cloudflared (${CF_ARCH})..."
  if curl -fsSL --retry 3 --retry-delay 5 -o "$CF_BIN" "$CF_URL"; then
    chmod +x "$CF_BIN"
    info "cloudflared installed: $("$CF_BIN" --version 2>&1 | head -1)"
  else
    warn "cloudflared download failed — Cloudflare Tunnel will not work until manually installed."
  fi
else
  info "cloudflared already installed: $("$CF_BIN" --version 2>&1 | head -1)"
fi

# Helper script that reads the tunnel token from dashboard_config.json
cat > "$APP_DIR/cloudflared-run.sh" << 'CFRUN'
#!/bin/bash
TOKEN=$(python3 -c "
import json, os
cfg = os.environ.get('DASHBOARD_CONFIG', '/opt/dashboard/backend/dashboard_config.json')
try:
    with open(cfg) as f:
        print(json.load(f).get('tunnel_token') or '')
except Exception:
    print('')
" 2>/dev/null)
if [ -z "$TOKEN" ]; then
    echo "[cloudflared] No tunnel token configured — exiting cleanly." >&2
    exit 0
fi
exec /usr/local/bin/cloudflared tunnel run --token "$TOKEN"
CFRUN
chmod +x "$APP_DIR/cloudflared-run.sh"
chown "$DASH_USER:$DASH_USER" "$APP_DIR/cloudflared-run.sh"

# cloudflared systemd service
cat > /etc/systemd/system/cloudflared.service << 'CFSVC'
[Unit]
Description=Cloudflare Tunnel for Dashboard
After=network-online.target dashboard-backend.service
Wants=network-online.target

[Service]
Type=simple
User=dashboard
Group=dashboard
ExecStartPre=/bin/bash -c 'for i in $(seq 1 45); do curl -sf http://127.0.0.1:8001/api/health >/dev/null 2>&1 && exit 0; sleep 2; done; exit 1'
ExecStart=/opt/dashboard/cloudflared-run.sh
Restart=on-failure
RestartSec=30
StartLimitIntervalSec=600
StartLimitBurst=3
StandardOutput=journal
StandardError=journal
SyslogIdentifier=cloudflared

[Install]
WantedBy=multi-user.target
CFSVC
systemctl enable cloudflared.service 2>/dev/null || true
info "cloudflared service configured."

# -- nginx ---------------------------------------------------------------------
step "Configuring nginx"
cat > /etc/nginx/sites-available/dashboard << 'NGINX'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    root /opt/dashboard/frontend-dist;
    index index.html;

    # Compress the JS/CSS bundle in transit — the built assets are several
    # hundred KB uncompressed, and this Pi is often reached over WiFi.
    gzip on;
    gzip_vary on;
    gzip_comp_level 5;
    gzip_min_length 256;
    gzip_proxied any;
    gzip_types
        text/plain
        text/css
        text/xml
        application/json
        application/javascript
        application/xml
        application/xml+rss
        image/svg+xml
        font/ttf
        font/otf;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/terminal/ws {
        proxy_pass         http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host $host;
        proxy_read_timeout 3600s;
    }

    location /api/ {
        proxy_pass         http://127.0.0.1:8001;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 60s;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/dashboard /etc/nginx/sites-enabled/dashboard
rm -f /etc/nginx/sites-enabled/default
# enable + reload so config changes (e.g. the WebSocket block) take effect on an
# in-place update, not just on a fresh boot.
nginx -t && systemctl enable nginx && systemctl reload-or-restart nginx

# -- udev rule -----------------------------------------------------------------
step "Adding udev DRI rule"
cat > /etc/udev/rules.d/99-dashboard-dri.rules << 'UDEV'
SUBSYSTEM=="drm", GROUP="render", MODE="0660"
SUBSYSTEM=="drm", KERNEL=="renderD*", GROUP="render", MODE="0660"
UDEV
udevadm control --reload-rules 2>/dev/null || true

# -- sudoers rules (dashboard user needs these for the web UI controls) -------
step "Configuring sudoers"
cat > /etc/sudoers.d/dashboard << 'SUDOERS'
Defaults:dashboard !requiretty
dashboard ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart dashboard-backend
dashboard ALL=(ALL) NOPASSWD: /usr/bin/systemctl start cloudflared
dashboard ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop cloudflared
dashboard ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart cloudflared
dashboard ALL=(ALL) NOPASSWD: /bin/bash /opt/dashboard/pi/update.sh
dashboard ALL=(ALL) NOPASSWD: /sbin/reboot
dashboard ALL=(ALL) NOPASSWD: /sbin/shutdown
dashboard ALL=(ALL) NOPASSWD: /usr/sbin/chpasswd
dashboard ALL=(ALL) NOPASSWD: /usr/sbin/useradd
dashboard ALL=(ALL) NOPASSWD: /usr/sbin/usermod
dashboard ALL=(ALL) NOPASSWD: /usr/bin/systemctl enable ssh
dashboard ALL=(ALL) NOPASSWD: /usr/bin/systemctl start ssh
SUDOERS
chmod 0440 /etc/sudoers.d/dashboard
info "sudoers rules installed."

# -- systemd services ----------------------------------------------------------
step "Installing systemd services"
SVC_SRC="$SCRIPT_DIR/services"
[ -d "$SVC_SRC" ] || SVC_SRC="$APP_DIR/services"

for svc in dashboard-backend.service dashboard-display.service; do
  if [ -f "$SCRIPT_DIR/services/$svc" ]; then
    cp "$SCRIPT_DIR/services/$svc" /etc/systemd/system/
  else
    warn "$svc not found in $SCRIPT_DIR/services -- skipping."
  fi
done
systemctl daemon-reload
systemctl enable dashboard-backend.service  2>/dev/null || true
systemctl enable dashboard-display.service  2>/dev/null || true

# -- Pi hardware config --------------------------------------------------------
step "Configuring Pi firmware"
CONFIG=/boot/firmware/config.txt
[ -f "$CONFIG" ] || CONFIG=/boot/config.txt

if [ -f "$CONFIG" ]; then
  add_config() {
    local k="$1" v="$2"
    if grep -q "^${k}=" "$CONFIG"; then
      sed -i "s|^${k}=.*|${k}=${v}|" "$CONFIG"
    else
      echo "${k}=${v}" >> "$CONFIG"
    fi
  }
  add_config gpu_mem 128
  add_config disable_splash 1
  add_config disable_overscan 1
  add_config hdmi_force_hotplug 1
  grep -q "vc4-kms-v3d" "$CONFIG" || echo "dtoverlay=vc4-kms-v3d" >> "$CONFIG"
  grep -q "arm_64bit=1"  "$CONFIG" || echo "arm_64bit=1"            >> "$CONFIG"
  info "Pi config updated: $CONFIG"
else
  warn "No Pi config.txt found -- skipping firmware configuration."
fi

# -- Swap ----------------------------------------------------------------------
if [ -f /etc/dphys-swapfile ]; then
  sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=512/' /etc/dphys-swapfile
  info "Swap set to 512 MB."
fi

# -- Auto-login ----------------------------------------------------------------
step "Configuring console auto-login"
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${DASH_USER} --noclear %I \$TERM
EOF
systemctl daemon-reload

# -- Mark as installed ---------------------------------------------------------
touch "$APP_DIR/.installed"
chown "$DASH_USER:$DASH_USER" "$APP_DIR/.installed"
info "Marked as installed: $APP_DIR/.installed"

# -- Start services ------------------------------------------------------------
step "Starting services"
# The display is a separate service — safe to restart inline.
systemctl restart dashboard-display.service  2>/dev/null && info "Display started." \
  || warn "Display failed to start. Check: journalctl -u dashboard-display"

# Restarting dashboard-backend is special. When this script is launched by the
# in-app updater it runs *inside* the dashboard-backend systemd cgroup, so a
# direct `systemctl restart` kills this very script at the final step (the
# update then reports a bogus failure, exit code -15). Schedule the restart in a
# detached transient unit a few seconds out, so setup.sh finishes and the
# updater reports success first; the backend then restarts cleanly on its own.
if command -v systemd-run >/dev/null 2>&1; then
  systemd-run --on-active=5 --collect \
    systemctl restart dashboard-backend.service >/dev/null 2>&1 \
    && info "Backend restart scheduled (5s) — survives the updater exiting." \
    || { systemctl restart dashboard-backend.service 2>/dev/null \
         && info "Backend started." \
         || warn "Backend failed to start. Check: journalctl -u dashboard-backend"; }
else
  systemctl restart dashboard-backend.service 2>/dev/null && info "Backend started." \
    || warn "Backend failed to start. Check: journalctl -u dashboard-backend"
fi

# -- Done ----------------------------------------------------------------------
IP=$(hostname -I | awk '{print $1}')
echo ""
echo -e "${GREEN}--------------------------------------------------------${NC}"
echo -e "${GREEN}  Family Dashboard setup complete!${NC}"
echo -e "${GREEN}--------------------------------------------------------${NC}"
echo ""
echo "  Pi IP address : $IP"
echo "  Web settings  : http://$IP  (from phone or laptop on same WiFi)"
echo "  API           : http://$IP:8001"
echo ""
echo "  Config file   : $APP_DIR/backend/dashboard_config.json"
echo "  Logs          : journalctl -u dashboard-backend -f"
echo "                  journalctl -u dashboard-display -f"
echo ""
echo "  Reboot to start the kiosk display:  sudo reboot"
echo ""
