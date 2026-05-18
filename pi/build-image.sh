#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# build-image.sh  --  Produce a flashable Family Dashboard .img for Raspberry Pi
#
# Uses macOS-native hdiutil -- no Docker required.
# Package installation happens automatically on first boot via firstrun.sh.
#
# Targets:  Pi Zero 2 W, Pi 3 B/B+, Pi 4, Pi 5  (arm64 / AArch64)
#           Any generic arm64 Debian/Ubuntu system (use setup.sh instead)
#
# Requirements on this Mac:
#   curl, xz  (pre-installed)
#   hdiutil   (pre-installed, part of macOS)
#
# Usage:
#   bash pi/build-image.sh
#   bash pi/build-image.sh --no-cache   # force re-download of base image
#
# Flash the result:
#   Raspberry Pi Imager -> "Use custom" -> pi/output/family-dashboard.img
#   (use the gear icon in Imager to pre-configure WiFi, hostname, SSH)
#
# First-boot note:
#   On first power-on the Pi runs apt-get + pip to finish setup (~15 min).
#   The display will show the boot console during this time, then reboot
#   and launch the dashboard automatically.
# -----------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="$SCRIPT_DIR/output"
CACHE_DIR="$SCRIPT_DIR/.cache"
OUTPUT_IMAGE="$OUTPUT_DIR/family-dashboard.img"

BASE_IMAGE_URL="https://downloads.raspberrypi.com/raspios_lite_arm64_latest"
EXPAND_MB=2048   # extra space added on top of the ~2.5 GB base image

NO_CACHE=false
for arg in "$@"; do
  case "$arg" in
    --no-cache) NO_CACHE=true ;;
    --help|-h)
      head -25 "$0" | grep '^#' | sed 's/^# \?//'
      exit 0 ;;
  esac
done

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}==>${NC} $*"; }
warn()  { echo -e "${YELLOW}WARN:${NC} $*"; }
error() { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }
step()  { echo -e "\n${GREEN}----------------------------------------${NC}"; info "$*"; }

cleanup() {
  if [ -n "${DISK_DEV:-}" ]; then
    hdiutil detach "$DISK_DEV" -force 2>/dev/null || true
  fi
}
trap cleanup EXIT

# -- Preflight -----------------------------------------------------------------
step "Checking requirements"
command -v curl    &>/dev/null || error "curl not found"
command -v xz      &>/dev/null || error "xz not found  (brew install xz)"
command -v hdiutil &>/dev/null || error "hdiutil not found -- this script requires macOS"
mkdir -p "$OUTPUT_DIR" "$CACHE_DIR"

# -- WiFi credentials ----------------------------------------------------------
# Pi OS Bookworm uses cloud-init (network-config) -- NOT wpa_supplicant.conf.
# We write credentials into the FAT32 boot partition so the Pi connects on
# first boot (needed for apt-get in firstrun.sh).
step "WiFi configuration"
echo "Enter WiFi credentials for the Pi (leave blank to skip -- configure manually later)."
printf "  WiFi network name (SSID): "
read -r WIFI_SSID
if [ -n "$WIFI_SSID" ]; then
  printf "  WiFi password: "
  read -rs WIFI_PASSWORD
  echo ""
  info "WiFi will be pre-configured for: $WIFI_SSID"
else
  warn "No WiFi entered. Edit network-config on the SD card before first boot."
  warn "See: pi/README-wifi.md for instructions."
fi

# -- Build React frontend on the host ------------------------------------------
step "Building React frontend"
FRONTEND_DIST="$PROJECT_DIR/frontend/dist"
if command -v npm &>/dev/null && [ -d "$PROJECT_DIR/frontend" ]; then
  pushd "$PROJECT_DIR/frontend" >/dev/null
  npm install --silent
  npm run build
  popd >/dev/null
  info "Frontend built -> frontend/dist/"
elif [ -d "$FRONTEND_DIST" ]; then
  info "Using existing frontend/dist/"
else
  warn "npm not found and no existing dist/ -- web UI will not be included"
fi

# -- Download base image -------------------------------------------------------
step "Obtaining Raspberry Pi OS Lite 64-bit base image"
XZ_FILE="$CACHE_DIR/raspios-lite-arm64.img.xz"
BASE_IMG="$CACHE_DIR/raspios-lite-arm64.img"

if [ "$NO_CACHE" = true ]; then
  rm -f "$XZ_FILE" "$BASE_IMG"
fi

if [ ! -f "$BASE_IMG" ]; then
  if [ ! -f "$XZ_FILE" ]; then
    info "Downloading (~500 MB)..."
    curl -L --progress-bar -o "$XZ_FILE" "$BASE_IMAGE_URL"
  fi
  info "Extracting..."
  # xz names output by stripping .xz; may produce a date-named file
  xz -d --keep "$XZ_FILE"
  EXTRACTED=$(find "$CACHE_DIR" -name "*.img" ! -name "raspios-lite-arm64.img" | head -1)
  [ -n "$EXTRACTED" ] && mv "$EXTRACTED" "$BASE_IMG"
fi
[ -f "$BASE_IMG" ] || error "Base image not found after extraction"
info "Base image ready: $BASE_IMG"

# -- Expand and copy working image ---------------------------------------------
step "Preparing working image (+${EXPAND_MB} MB)"
cp "$BASE_IMG" "$OUTPUT_IMAGE"
dd if=/dev/zero bs=1M count="$EXPAND_MB" >> "$OUTPUT_IMAGE" 2>/dev/null
info "Image size: $(du -sh "$OUTPUT_IMAGE" | cut -f1)"

# -- Mount the FAT32 boot partition via hdiutil --------------------------------
step "Mounting FAT32 boot partition"
# Attach the raw image; -nomount so we choose what to mount
ATTACH_OUT=$(hdiutil attach -imagekey diskimage-class=CRawDiskImage \
  -nomount "$OUTPUT_IMAGE" 2>&1)
info "hdiutil attach output:"
echo "$ATTACH_OUT"

# The disk device is the first /dev/diskN line
DISK_DEV=$(echo "$ATTACH_OUT" | grep -oE '/dev/disk[0-9]+' | head -1)
[ -n "$DISK_DEV" ] || error "Could not find disk device from hdiutil output"
info "Disk device: $DISK_DEV"

# Mount the FAT32 partition (slice 1 = s1)
BOOT_MNT=$(mktemp -d)
hdiutil mount "${DISK_DEV}s1" -mountpoint "$BOOT_MNT" \
  || error "Could not mount FAT32 boot partition ${DISK_DEV}s1"
info "Boot partition mounted at: $BOOT_MNT"

# -- Stage application files in the boot partition ----------------------------
step "Staging application files in boot partition"
STAGE_DIR="$BOOT_MNT/dashboard-stage"
mkdir -p "$STAGE_DIR/backend"
mkdir -p "$STAGE_DIR/services"

# Backend Python source (exclude caches and local DB)
rsync -a --quiet \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'dashboard.db*' \
  --exclude '.env' \
  "$PROJECT_DIR/backend/" \
  "$STAGE_DIR/backend/"

# Pre-built React frontend
if [ -d "$FRONTEND_DIST" ] && [ "$(ls -A "$FRONTEND_DIST" 2>/dev/null)" ]; then
  mkdir -p "$STAGE_DIR/frontend-dist"
  rsync -a --quiet "$FRONTEND_DIST/" "$STAGE_DIR/frontend-dist/"
  info "Frontend dist staged."
fi

# Systemd service files
cp "$SCRIPT_DIR/services/dashboard-backend.service" "$STAGE_DIR/services/"
cp "$SCRIPT_DIR/services/dashboard-display.service" "$STAGE_DIR/services/"

info "App files staged in $STAGE_DIR"
du -sh "$STAGE_DIR"

# -- Write network-config (cloud-init WiFi for Pi OS Bookworm) -----------------
# Pi OS Bookworm uses NetworkManager + cloud-init, NOT wpa_supplicant.conf.
# The network-config file in the FAT32 boot partition is processed before
# firstrun.sh runs, so WiFi is available when apt-get/pip execute.
step "Writing network-config"
if [ -n "${WIFI_SSID:-}" ] && [ -n "${WIFI_PASSWORD:-}" ]; then
  cat > "$BOOT_MNT/network-config" << NETCFG
version: 2
ethernets:
  eth0:
    dhcp4: true
    optional: true
wifis:
  wlan0:
    dhcp4: true
    optional: true
    access-points:
      "${WIFI_SSID}":
        password: "${WIFI_PASSWORD}"
NETCFG
  info "network-config written for SSID: $WIFI_SSID"
elif [ -n "${WIFI_SSID:-}" ]; then
  # Open network (no password)
  cat > "$BOOT_MNT/network-config" << NETCFG
version: 2
ethernets:
  eth0:
    dhcp4: true
    optional: true
wifis:
  wlan0:
    dhcp4: true
    optional: true
    access-points:
      "${WIFI_SSID}": {}
NETCFG
  info "network-config written for open network: $WIFI_SSID"
else
  info "No WiFi entered -- leaving existing network-config unchanged."
fi

# -- Write firstrun.sh to the boot partition -----------------------------------
step "Writing firstrun.sh"

cat > "$BOOT_MNT/firstrun.sh" << 'FIRSTRUN'
#!/bin/bash
# Runs as root on first Pi boot via systemd.
# Installs all dashboard dependencies and wires up auto-start.
set -euo pipefail

LOG=/var/log/dashboard-firstrun.log
exec > >(tee -a "$LOG") 2>&1

echo ""
echo "============================================"
echo " Family Dashboard first-run setup"
echo " $(date)"
echo "============================================"

STAGE=/boot/dashboard-stage
APP=/opt/dashboard
DASH_USER=dashboard
VENV=$APP/venv

# -- Resize root partition to fill SD card ------------------------------------
echo "=> Expanding root filesystem..."
ROOT_DEV=$(findmnt -n -o SOURCE / 2>/dev/null || echo "")
if [ -n "$ROOT_DEV" ]; then
  DISK=$(lsblk -no PKNAME "$ROOT_DEV" 2>/dev/null || echo "mmcblk0")
  PART=$(basename "$ROOT_DEV")
  # Get partition number
  PART_NUM=$(echo "$PART" | grep -oE '[0-9]+$' || echo "2")
  # Resize partition table entry
  parted -s /dev/$DISK resizepart $PART_NUM 100% 2>/dev/null || true
  # Resize filesystem
  resize2fs "$ROOT_DEV" 2>/dev/null || true
  echo "   Root filesystem expanded."
else
  echo "   Could not detect root device -- skipping resize."
fi

# -- System packages ----------------------------------------------------------
echo "=> Installing system packages (this takes a few minutes)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  python3 python3-pip python3-venv python3-dev \
  libsdl2-2.0-0 libsdl2-image-2.0-0 libsdl2-ttf-2.0-0 libsdl2-mixer-2.0-0 \
  libopenjp2-7 libtiff6 libfreetype6 \
  sqlite3 \
  fonts-dejavu-core fonts-liberation \
  nginx \
  curl ca-certificates \
  parted \
  2>&1 | tail -5
apt-get clean
echo "   System packages installed."

# -- Create dashboard user ----------------------------------------------------
echo "=> Creating '$DASH_USER' user..."
if ! id "$DASH_USER" &>/dev/null; then
  useradd -m -s /bin/bash \
    -G video,render,audio,dialout,plugdev,netdev,gpio \
    "$DASH_USER" || true
fi
for grp in video render audio gpio; do
  getent group "$grp" >/dev/null && usermod -aG "$grp" "$DASH_USER" 2>/dev/null || true
done
echo "   User ready."

# -- Copy staged files to /opt/dashboard --------------------------------------
echo "=> Installing application files..."
mkdir -p "$APP"
if [ -d "$STAGE/backend" ]; then
  rsync -a "$STAGE/backend/" "$APP/backend/"
fi
if [ -d "$STAGE/frontend-dist" ]; then
  rsync -a "$STAGE/frontend-dist/" "$APP/frontend-dist/"
fi
# Install service files
if [ -d "$STAGE/services" ]; then
  cp "$STAGE/services/"*.service /etc/systemd/system/ 2>/dev/null || true
fi
chown -R "$DASH_USER:$DASH_USER" "$APP"
echo "   Files installed."

# -- Python virtual environment -----------------------------------------------
echo "=> Creating Python virtual environment..."
sudo -u "$DASH_USER" python3 -m venv "$VENV"
sudo -u "$DASH_USER" "$VENV/bin/pip" install --upgrade --quiet pip wheel
echo "=> Installing Python packages (pygame-ce download may take a minute)..."
sudo -u "$DASH_USER" "$VENV/bin/pip" install --quiet \
  -r "$APP/backend/requirements.txt"
sudo -u "$DASH_USER" "$VENV/bin/pip" install --quiet \
  pygame-ce uvloop httptools
echo "   Python packages installed."

# -- nginx configuration ------------------------------------------------------
echo "=> Configuring nginx..."
cat > /etc/nginx/sites-available/dashboard << 'NGINX'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    root /opt/dashboard/frontend-dist;
    index index.html;
    location / { try_files $uri $uri/ /index.html; }
    location /api/ {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 60s;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/dashboard /etc/nginx/sites-enabled/dashboard
rm -f /etc/nginx/sites-enabled/default
systemctl enable nginx

# -- udev rule for KMS/DRM ----------------------------------------------------
cat > /etc/udev/rules.d/99-dashboard-dri.rules << 'UDEV'
SUBSYSTEM=="drm", GROUP="render", MODE="0660"
SUBSYSTEM=="drm", KERNEL=="renderD*", GROUP="render", MODE="0660"
UDEV

# -- Systemd services ---------------------------------------------------------
echo "=> Enabling dashboard services..."
systemctl daemon-reload
systemctl enable dashboard-backend.service  2>/dev/null || true
systemctl enable dashboard-display.service  2>/dev/null || true

# -- Pi firmware configuration ------------------------------------------------
echo "=> Configuring Pi firmware..."
CONFIG=/boot/firmware/config.txt
[ -f "$CONFIG" ] || CONFIG=/boot/config.txt

add_cfg() {
  local k="$1" v="$2"
  if grep -q "^${k}=" "$CONFIG" 2>/dev/null; then
    sed -i "s|^${k}=.*|${k}=${v}|" "$CONFIG"
  elif grep -q "^#${k}=" "$CONFIG" 2>/dev/null; then
    sed -i "s|^#${k}=.*|${k}=${v}|" "$CONFIG"
  else
    echo "${k}=${v}" >> "$CONFIG"
  fi
}
add_cfg gpu_mem 128
add_cfg disable_splash 1
add_cfg disable_overscan 1
add_cfg hdmi_force_hotplug 1
grep -q "vc4-kms-v3d" "$CONFIG" || echo "dtoverlay=vc4-kms-v3d" >> "$CONFIG"
grep -q "arm_64bit=1"  "$CONFIG" || echo "arm_64bit=1"            >> "$CONFIG"

# -- Swap (important on Pi Zero 2 W with only 512 MB RAM) --------------------
if [ -f /etc/dphys-swapfile ]; then
  sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=512/' /etc/dphys-swapfile
fi

# -- Console auto-login -------------------------------------------------------
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${DASH_USER} --noclear %I \$TERM
EOF

# -- Default dashboard config -------------------------------------------------
if [ ! -f "$APP/backend/dashboard_config.json" ] && \
   [ -f "$APP/backend/dashboard_config.example.json" ]; then
  cp "$APP/backend/dashboard_config.example.json" \
     "$APP/backend/dashboard_config.json"
  chown "$DASH_USER:$DASH_USER" "$APP/backend/dashboard_config.json"
fi

# -- Hostname -----------------------------------------------------------------
echo "family-dashboard" > /etc/hostname
sed -i 's/127\.0\.1\.1.*/127.0.1.1\tfamily-dashboard/' /etc/hosts 2>/dev/null || true

# -- Clean up staged files from boot partition --------------------------------
echo "=> Cleaning up staging files from boot partition..."
rm -rf "$STAGE"
# Remove firstrun trigger from cmdline.txt
CMDLINE=/boot/firmware/cmdline.txt
[ -f "$CMDLINE" ] || CMDLINE=/boot/cmdline.txt
sed -i 's| systemd.run=[^ ]*||g' "$CMDLINE" 2>/dev/null || true
sed -i 's| systemd.run_success_action=[^ ]*||g' "$CMDLINE" 2>/dev/null || true
sed -i 's| systemd.unit=[^ ]*||g' "$CMDLINE" 2>/dev/null || true

echo ""
echo "============================================"
echo " First-run setup complete! Rebooting..."
echo "============================================"
echo ""

sync
reboot
FIRSTRUN

chmod +x "$BOOT_MNT/firstrun.sh"
info "firstrun.sh written."

# -- Update cmdline.txt to trigger firstrun.sh --------------------------------
step "Updating cmdline.txt"

# cmdline.txt is in the FAT32 partition root
CMDLINE_FILE="$BOOT_MNT/cmdline.txt"
[ -f "$CMDLINE_FILE" ] || error "cmdline.txt not found in boot partition at $BOOT_MNT"

FIRSTRUN_TRIGGER="systemd.run=/boot/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target"

# Check if already added
if grep -q "firstrun.sh" "$CMDLINE_FILE"; then
  info "cmdline.txt already has firstrun.sh trigger"
else
  # cmdline.txt must be a single line; append to end of that line
  CURRENT=$(cat "$CMDLINE_FILE")
  echo "${CURRENT} ${FIRSTRUN_TRIGGER}" > "$CMDLINE_FILE"
  info "cmdline.txt updated."
fi

info "Boot partition contents:"
ls -lh "$BOOT_MNT/"

# -- Detach -------------------------------------------------------------------
step "Detaching disk image"
hdiutil detach "$DISK_DEV" -force
DISK_DEV=""   # prevent double-detach in trap
info "Done."

# -- Summary ------------------------------------------------------------------
SIZE=$(du -sh "$OUTPUT_IMAGE" | cut -f1)
echo ""
echo "============================================"
echo " Image ready: pi/output/family-dashboard.img"
echo " Size: $SIZE"
echo "============================================"
echo ""
echo " Flash with Raspberry Pi Imager (recommended):"
echo "   1. Choose OS -> Use custom -> family-dashboard.img"
echo "   2. Click the gear icon to set WiFi, hostname, SSH"
echo "   3. Write to SD card"
echo ""
echo " First boot takes ~15 min (installs packages)."
echo " After reboot: http://<pi-ip>  (web settings)"
echo "               HDMI shows the dashboard"
echo ""
