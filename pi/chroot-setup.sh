#!/usr/bin/env bash
# Runs inside the Raspberry Pi OS ARM64 chroot.
# Installs all dependencies, creates the dashboard user, and wires up systemd.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export PYTHONDONTWRITEBYTECODE=1

APP_DIR=/opt/dashboard
VENV="$APP_DIR/venv"
DASH_USER=dashboard

GREEN='\033[0;32m'; NC='\033[0m'
info() { echo -e "${GREEN}  [chroot]${NC} $*"; }

# -- System packages -----------------------------------------------------------
info "Updating package lists..."
apt-get update -qq

info "Installing system dependencies..."
apt-get install -y -qq \
  python3 python3-pip python3-venv python3-dev \
  python3-pygame \
  libsdl2-dev libsdl2-image-dev libsdl2-ttf-dev libsdl2-mixer-dev \
  libdrm2 libgbm1 \
  libopenjp2-7 libtiff6 libfreetype6 \
  sqlite3 libsqlite3-dev \
  fonts-dejavu-core fonts-liberation \
  nginx \
  git curl wget ca-certificates \
  dnsutils iputils-ping \
  iw wireless-regdb \
  libseat1 \
  raspi-config \
  dnsmasq-base

# Clean up apt cache to save image space
apt-get clean
rm -rf /var/lib/apt/lists/*

# -- Create dashboard user -----------------------------------------------------
info "Creating '$DASH_USER' user..."
if ! id "$DASH_USER" &>/dev/null; then
  useradd -m -s /bin/bash \
    -G video,render,gpio,audio,dialout,plugdev,netdev \
    "$DASH_USER"
fi
# Ensure group membership even if user already existed
for grp in video render audio gpio; do
  getent group "$grp" >/dev/null && usermod -aG "$grp" "$DASH_USER" || true
done

# -- Python virtual environment ------------------------------------------------
# Always create the venv using the Pi OS's own Python so the version matches.
# Wheels were pre-downloaded in the Docker stage and staged at /tmp/pi-wheels.
# --system-site-packages lets the venv fall back to apt-installed packages
# (especially python3-pygame) if any pip install fails. Pip-installed versions
# always take priority over system packages inside the venv.
info "Creating Python venv with $(python3 --version)..."
python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip wheel

WHEELS_DIR=/tmp/pi-wheels
if [ -d "$WHEELS_DIR" ] && [ "$(ls $WHEELS_DIR/*.whl 2>/dev/null | wc -l)" -gt 0 ]; then
  info "Installing pre-staged wheels (pygame, uvloop, httptools, qrcode)..."
  # Install only the packages that are actually pre-staged -- do not use --no-index
  # for requirements.txt because only a subset of packages are staged.
  "$VENV/bin/pip" install --quiet --prefer-binary --no-index --find-links "$WHEELS_DIR" \
    pygame uvloop httptools qrcode 2>/dev/null || true
fi

# Always install requirements.txt from PyPI (covers staged and non-staged paths).
# Packages already installed from wheels are skipped automatically.
info "Installing Python app requirements from PyPI..."
"$VENV/bin/pip" install --quiet --prefer-binary \
  --timeout 180 --retries 5 \
  -r "$APP_DIR/backend/requirements.txt"

# -- Verify pygame is importable (fail loud rather than ship a broken image) --
if "$VENV/bin/python3" -c "import pygame; print('  pygame', pygame.__version__, 'OK')" 2>/dev/null; then
  info "pygame verified in venv."
else
  info "pip pygame not in venv -- system python3-pygame will be used (via --system-site-packages)."
  "$VENV/bin/python3" -c "import pygame; print('  pygame', pygame.__version__, 'OK (system)')" || \
    { info "ERROR: pygame unavailable in venv and system -- display will not work!"; }
fi

# -- File ownership ------------------------------------------------------------
info "Setting file ownership..."
chown -R "$DASH_USER:$DASH_USER" "$APP_DIR"

# -- nginx config to serve pre-built React frontend ---------------------------
info "Configuring nginx..."
cat > /etc/nginx/sites-available/dashboard << 'NGINX'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    root /opt/dashboard/frontend-dist;
    index index.html;

    # Never cache index.html -- captive portal browsers must get a fresh copy
    location = /index.html {
        try_files $uri =404;
        add_header Cache-Control "no-store, no-cache, must-revalidate";
        add_header Pragma "no-cache";
        expires 0;
    }

    # Serve static React build
    location / {
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-store, no-cache, must-revalidate";
        add_header Pragma "no-cache";
        expires 0;
    }

    # Proxy API calls to FastAPI backend
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

# -- udev rules: framebuffer + DRM access for dashboard user ------------------
info "Adding udev rules for display access..."
cat > /etc/udev/rules.d/99-dashboard-dri.rules << 'UDEV'
SUBSYSTEM=="drm", KERNEL=="card*",    GROUP="video",  MODE="0660"
SUBSYSTEM=="drm", KERNEL=="renderD*", GROUP="render", MODE="0660"
SUBSYSTEM=="graphics", KERNEL=="fb*", GROUP="video",  MODE="0660"
UDEV

# -- cloudflared (Cloudflare Tunnel client) ------------------------------------
info "Installing cloudflared..."
CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64"
if curl -fsSL --retry 3 --retry-delay 5 -o /usr/local/bin/cloudflared "$CF_URL"; then
  chmod +x /usr/local/bin/cloudflared
  info "cloudflared installed: $(/usr/local/bin/cloudflared --version 2>&1 | head -1)"
else
  info "WARNING: cloudflared download failed — tunnel will not work until manually installed."
fi

# Helper script that reads the tunnel token from dashboard_config.json and
# execs cloudflared.  Exits 0 (clean) when the device is not yet provisioned
# so systemd does not count it as a failure and enter a restart loop.
cat > "$APP_DIR/cloudflared-run.sh" << 'CFRUN'
#!/bin/bash
CFG=/opt/dashboard/backend/dashboard_config.json

TOKEN=$(python3 -c "
import json, sys, os
cfg = os.environ.get('DASHBOARD_CONFIG', '/opt/dashboard/backend/dashboard_config.json')
try:
    with open(cfg) as f:
        d = json.load(f)
    print(d.get('tunnel_token') or '')
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$TOKEN" ]; then
    echo "[cloudflared] Not yet provisioned — tunnel token not found in $CFG." >&2
    exit 0
fi

exec /usr/local/bin/cloudflared tunnel run --token "$TOKEN"
CFRUN
chmod +x "$APP_DIR/cloudflared-run.sh"
chown "$DASH_USER:$DASH_USER" "$APP_DIR/cloudflared-run.sh"

# -- Setup scripts (captive portal + network apply helper) --------------------
info "Installing setup scripts..."
cp /tmp/setup-mode.sh      "$APP_DIR/setup-mode.sh"
cp /tmp/pi-setup-apply.sh  "$APP_DIR/pi-setup-apply.sh"
chmod +x "$APP_DIR/setup-mode.sh" "$APP_DIR/pi-setup-apply.sh"

# -- sudoers rules: setup helper + service restarts ---------------------------
info "Adding sudoers rules..."
cat > /etc/sudoers.d/dashboard-setup << 'SUDOERS'
Defaults:dashboard !requiretty
dashboard ALL=(ALL) NOPASSWD: /opt/dashboard/pi-setup-apply.sh
dashboard ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart dashboard-backend
dashboard ALL=(ALL) NOPASSWD: /usr/bin/systemctl start cloudflared
dashboard ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop cloudflared
dashboard ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart cloudflared
SUDOERS
chmod 440 /etc/sudoers.d/dashboard-setup

# -- Copy setup service --------------------------------------------------------
cp /tmp/dashboard-setup.service /etc/systemd/system/dashboard-setup.service

# -- cloudflared systemd service -----------------------------------------------
info "Installing cloudflared.service..."
cat > /etc/systemd/system/cloudflared.service << 'CFSVC'
[Unit]
Description=Cloudflare Tunnel for Dashboard
After=network-online.target dashboard-backend.service
Wants=network-online.target

[Service]
Type=simple
User=dashboard
Group=dashboard

# Wait up to 90 s for the dashboard backend to report healthy before starting
# cloudflared. Prevents the tunnel from opening before the app is ready.
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

# -- Disable Pi OS first-boot wizard ------------------------------------------
# Pi OS Bookworm runs userconfig.service on first boot to prompt for a user/
# password. We manage our own user, so suppress it entirely.
info "Disabling first-boot wizard..."
systemctl disable userconfig.service            2>/dev/null || true
systemctl disable pi-gen-first-boot-wizard      2>/dev/null || true
# Remove the flag files that trigger the wizard
rm -f /etc/xdg/autostart/piwiz.desktop
rm -f /usr/share/applications/piwiz.desktop
# Mark the Pi OS user-config as already done
mkdir -p /etc/userconf-pi && touch /etc/userconf-pi/done

# -- Enable cloudflared service ------------------------------------------------
info "Enabling cloudflared.service..."
systemctl enable cloudflared.service

# -- Enable SSH for remote diagnostics ----------------------------------------
info "Enabling SSH..."
systemctl enable ssh.service 2>/dev/null || systemctl enable sshd.service 2>/dev/null || true
# Touch the flag file Pi OS checks for SSH on first boot
touch /boot/firmware/ssh 2>/dev/null || touch /boot/ssh 2>/dev/null || true
# Set a password for the dashboard user so SSH login works
echo "dashboard:dashboard" | chpasswd
# Explicitly allow password auth  --  Pi OS Bookworm sshd_config.d/ can override to "no"
mkdir -p /etc/ssh/sshd_config.d
echo "PasswordAuthentication yes" > /etc/ssh/sshd_config.d/10-dashboard.conf

# -- systemd services ----------------------------------------------------------
info "Enabling systemd services..."
systemctl enable dashboard-setup.service
systemctl enable dashboard-backend.service
# Statically enable getty@tty1 so systemd-getty-generator doesn't need to
# discover it at runtime -- required on Pi OS Bookworm with vc4-kms-v3d where
# the generator skips tty1 during early boot.
systemctl enable getty@tty1.service
# dashboard-display is launched from the autologin shell (see .bash_profile below)
# so we don't enable it as a service  --  it would run without a logind session and
# SDL kmsdrm would fail to acquire DRM master.
systemctl disable dashboard-display.service 2>/dev/null || true
systemctl enable nginx.service

# wpa_supplicant conflicts with NetworkManager's built-in wifi management.
# NM manages its own wpa_supplicant internally; the standalone service must be off.
systemctl disable wpa_supplicant.service 2>/dev/null || true

# -- Pi hardware configuration (boot/config.txt / firmware config) -------------
info "Configuring Pi firmware..."

# Modern Pi OS Bookworm uses /boot/firmware/config.txt
CONFIG=/boot/firmware/config.txt
[ -f "$CONFIG" ] || CONFIG=/boot/config.txt

add_config() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$CONFIG" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$CONFIG"
  elif grep -q "^#${key}=" "$CONFIG" 2>/dev/null; then
    sed -i "s|^#${key}=.*|${key}=${val}|" "$CONFIG"
  else
    echo "${key}=${val}" >> "$CONFIG"
  fi
}

# GPU memory -- 16 MB frees ~112 MB of RAM for uvicorn + display.py co-existence
add_config gpu_mem 16
# KMS/DRM video driver (used by SDL_VIDEODRIVER=kmsdrm)
grep -q "vc4-kms-v3d" "$CONFIG" || echo "dtoverlay=vc4-kms-v3d" >> "$CONFIG"
# Disable the rainbow splash screen on boot
add_config disable_splash 1
# Disable overscan (black borders) -- adjust if your monitor needs it
add_config disable_overscan 1
# HDMI force-on even with no monitor at boot time
add_config hdmi_force_hotplug 1
# Rotate display if needed (0=normal, 1=90, 2=180, 3=270)
# add_config display_rotate 0

# Pi Zero 2 W: BCM2710A1 is arm64 but defaults to 32-bit in some Pi OS builds.
# Forcing arm_64bit ensures we use the arm64 kernel.
grep -q "arm_64bit=1" "$CONFIG" || echo "arm_64bit=1" >> "$CONFIG"

# -- Increase swap to 512 MB (tight on Pi Zero 2 W with 512 MB RAM) ------------
info "Configuring swap..."
SWAP_FILE=/etc/dphys-swapfile
if [ -f "$SWAP_FILE" ]; then
  sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=512/' "$SWAP_FILE"
else
  echo "CONF_SWAPSIZE=512" > "$SWAP_FILE"
fi

# -- Console auto-login as dashboard user -------------------------------------
info "Configuring auto-login..."
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${DASH_USER} --noclear %I \$TERM
EOF

# -- Kiosk display launcher via login shell -----------------------------------
# Running display.py from .bash_profile gives it a real logind session on tty1,
# which is required for SDL kmsdrm to acquire DRM master. A plain systemd
# service (no active VT) cannot become KMS master on Pi OS Bookworm.
info "Creating kiosk .bash_profile..."
cat > /home/${DASH_USER}/.bash_profile << 'PROFILE'
[ -f ~/.bashrc ] && . ~/.bashrc

if [ "$(tty)" = "/dev/tty1" ]; then
    # XDG_RUNTIME_DIR lets libseat talk to logind so SDL kmsdrm can acquire
    # DRM master without root. systemd-logind creates this dir for active sessions
    # but agetty autologin may race; ensure it exists before SDL opens.
    export XDG_RUNTIME_DIR=/run/user/$(id -u)
    mkdir -p "$XDG_RUNTIME_DIR"
    chmod 0700 "$XDG_RUNTIME_DIR"

    # Pick whichever DRM card is readable (card0 on Pi Zero 2 W / Pi 3,
    # card1 on Pi 4/5 where card0 is the audio device).
    for _card in /dev/dri/card0 /dev/dri/card1; do
        [ -r "$_card" ] && export SDL_VIDEO_KMSDRM_DEVICE="$_card" && break
    done

    export SDL_VIDEODRIVER=kmsdrm
    export SDL_VIDEO_KMSDRM_ALLOW_MODESET=1
    export SDL_RENDER_DRIVER=software
    export SDL_AUDIODRIVER=dummy
    export PYGAME_HIDE_SUPPORT_PROMPT=1

    # Wait for the backend API to be healthy before loading Pygame.
    until curl -sf --max-time 5 http://127.0.0.1:8001/api/health >/dev/null 2>&1; do
        sleep 5
    done

    # Don't start display.py while in setup mode (hotspot active).
    # This prevents thread-pool saturation in uvicorn so the setup page
    # stays responsive. Display.py only starts after WiFi is configured.
    while true; do
        STATUS=$(curl -sf --max-time 5 http://127.0.0.1:8001/api/setup/status 2>/dev/null)
        echo "$STATUS" | grep -q '"setup_mode":false' && break
        sleep 10
    done
    # Brief warm-up so uvicorn code pages are settled before Pygame loads.
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
chown ${DASH_USER}:${DASH_USER} /home/${DASH_USER}/.bash_profile

# -- Persistent journal logging -----------------------------------------------
info "Enabling persistent journal..."
mkdir -p /var/log/journal
# journald requires setgid + systemd-journal group ownership to write here
chown root:systemd-journal /var/log/journal 2>/dev/null || true
chmod 2755 /var/log/journal
systemd-tmpfiles --create --prefix /var/log/journal 2>/dev/null || true
sed -i 's/^#\?Storage=.*/Storage=persistent/' /etc/systemd/journald.conf 2>/dev/null || \
    echo -e "[Journal]\nStorage=persistent" >> /etc/systemd/journald.conf

# WiFi is configured at runtime via the captive portal setup wizard.
# No credentials are baked into the image  --  each customer enters their own.

# -- Write example config if no config present --------------------------------
if [ ! -f "$APP_DIR/backend/dashboard_config.json" ]; then
  cp "$APP_DIR/backend/dashboard_config.example.json" \
     "$APP_DIR/backend/dashboard_config.json" 2>/dev/null || true
fi

# -- WiFi regulatory domain (required for AP mode on Pi Zero 2 W) --------------
# Without a country code the kernel refuses to start the AP radio.
info "Setting WiFi country code..."
raspi-config nonint do_wifi_country US 2>/dev/null || \
    echo "REGDOMAIN=US" > /etc/default/crda

# -- Disable cloud-init --------------------------------------------------------
# Cloud-init is not needed  --  our setup wizard handles all first-boot config.
# Leaving it enabled causes it to run on every boot and can interfere with NM.
info "Disabling cloud-init..."
touch /etc/cloud/cloud-init.disabled

# -- Hostname (temporary  --  overwritten by pi-setup-apply.sh during setup) -----
echo "dashboard-setup" > /etc/hostname
sed -i 's/127\.0\.1\.1.*/127.0.1.1\tdashboard-setup/' /etc/hosts 2>/dev/null || \
  echo "127.0.1.1 dashboard-setup" >> /etc/hosts

info "Chroot setup finished."
