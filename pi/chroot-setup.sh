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
  libsdl2-dev libsdl2-image-dev libsdl2-ttf-dev libsdl2-mixer-dev \
  libopenjp2-7 libtiff6 libfreetype6 \
  sqlite3 libsqlite3-dev \
  fonts-dejavu-core fonts-liberation \
  nginx \
  git curl wget ca-certificates \
  dnsutils iputils-ping \
  raspi-config

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
info "Creating Python venv..."
python3 -m venv "$VENV"

info "Installing Python packages..."
"$VENV/bin/pip" install --upgrade --quiet pip wheel

# Backend requirements from the copied source
"$VENV/bin/pip" install --quiet \
  -r "$APP_DIR/backend/requirements.txt"

# pygame-ce has a prebuilt arm64 wheel -- no SDL2 compile needed
"$VENV/bin/pip" install --quiet pygame-ce

# Swap default uvicorn loop to uvloop for better throughput on Pi
"$VENV/bin/pip" install --quiet uvloop httptools

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

    # Serve static React build
    location / {
        try_files $uri $uri/ /index.html;
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

# -- udev rule: allow render group to access DRI devices (KMS/DRM) -------------
info "Adding udev rule for DRI access..."
cat > /etc/udev/rules.d/99-dashboard-dri.rules << 'UDEV'
SUBSYSTEM=="drm", GROUP="render", MODE="0660"
SUBSYSTEM=="drm", KERNEL=="renderD*", GROUP="render", MODE="0660"
UDEV

# -- systemd services ----------------------------------------------------------
info "Enabling systemd services..."
systemctl enable dashboard-backend.service
systemctl enable dashboard-display.service
systemctl enable nginx.service

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

# GPU memory -- 128 MB is a reasonable balance for display.py on Pi Zero 2 W
add_config gpu_mem 128
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

# -- First-boot expansion service ---------------------------------------------
# Runs once on first boot to resize the root filesystem to fill the SD card
info "Adding first-boot resize service..."
cat > /etc/systemd/system/dashboard-firstboot.service << 'FIRSTBOOT'
[Unit]
Description=First-boot filesystem expansion
After=local-fs.target
ConditionPathExists=/opt/dashboard/.firstboot-pending
DefaultDependencies=no
Before=sysinit.target

[Service]
Type=oneshot
ExecStart=/opt/dashboard/firstboot.sh
RemainAfterExit=yes

[Install]
WantedBy=sysinit.target
FIRSTBOOT

cat > "$APP_DIR/firstboot.sh" << 'FBSH'
#!/bin/bash
# Run once: expand root fs to fill the SD card
ROOT_DEV=$(findmnt -n -o SOURCE /)
DISK=$(lsblk -no PKNAME "$ROOT_DEV")
PART_NUM=$(lsblk -no MAJ:MIN "$ROOT_DEV" | awk -F: '{print $2}')
echo "Expanding root partition on /dev/$DISK..."
parted -s /dev/$DISK resizepart 2 100% || true
resize2fs "$ROOT_DEV" || true
rm -f /opt/dashboard/.firstboot-pending
echo "First-boot expansion complete."
FBSH
chmod +x "$APP_DIR/firstboot.sh"
touch "$APP_DIR/.firstboot-pending"
chown "$DASH_USER:$DASH_USER" "$APP_DIR/firstboot.sh"

systemctl enable dashboard-firstboot.service

# -- Write example config if no config present --------------------------------
if [ ! -f "$APP_DIR/backend/dashboard_config.json" ]; then
  cp "$APP_DIR/backend/dashboard_config.example.json" \
     "$APP_DIR/backend/dashboard_config.json" 2>/dev/null || true
fi

# -- Hostname ------------------------------------------------------------------
echo "family-dashboard" > /etc/hostname
sed -i 's/127\.0\.1\.1.*/127.0.1.1\tfamily-dashboard/' /etc/hosts 2>/dev/null || \
  echo "127.0.1.1 family-dashboard" >> /etc/hosts

info "Chroot setup finished."
