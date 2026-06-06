#!/usr/bin/env bash
# build-image.sh  --  Create a flashable Family Dashboard .img for Raspberry Pi
#
# NO DOCKER REQUIRED.  Builds on macOS using only built-in tools (curl, xz,
# hdiutil, diskutil).  On first boot the Pi clones the app from GitHub and
# installs itself automatically.
#
# Targets:  Pi Zero 2W, Pi 3, Pi 4, Pi 5  (all arm64/AArch64)
#
# Requirements on this Mac:
#   curl, xz   (pre-installed on macOS)
#   npm        (only needed to build the React frontend if not already built)
#
# Usage:
#   bash pi/build-image.sh
#   bash pi/build-image.sh --no-cache   # force re-download of base Pi OS image
#
# ── How to flash ──────────────────────────────────────────────────────────────
#   1. Open Raspberry Pi Imager  (raspberrypi.com/software)
#   2. Choose OS → "Use custom" → select pi/output/family-dashboard.img
#   3. Choose your SD card
#   4. Click "Next" → "Edit Settings":
#        ✓ Set WiFi SSID and password  (REQUIRED — Pi needs internet for install)
#        ✓ Set hostname (e.g. family-dashboard)
#        ✓ Enable SSH  (optional but handy for debugging)
#        ✓ Set username: dashboard  password: dashboard
#   5. Write to SD card
#
# ── First-boot sequence ───────────────────────────────────────────────────────
#   Boot 1: Raspberry Pi Imager applies your WiFi/hostname/SSH settings, reboots
#   Boot 2: Dashboard first-boot hook runs → installs the dashboard-install
#           service → that service clones the GitHub repo and runs setup.sh
#           (~10-15 min on Pi 3/4/5, ~20-30 min on Pi Zero 2W)
#   After:  Dashboard is live!  http://<hostname>.local  or  http://<pi-ip>
#           HDMI screen shows the kiosk display
#
# ── Updating after install ────────────────────────────────────────────────────
#   Settings → Updates (owner only) — pulls latest code from GitHub
# -----------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="$SCRIPT_DIR/output"
CACHE_DIR="$SCRIPT_DIR/.cache"
OUTPUT_IMAGE="$OUTPUT_DIR/family-dashboard.img"

BASE_IMAGE_URL="https://downloads.raspberrypi.com/raspios_lite_arm64_latest"
REPO_URL="https://github.com/dmccollum-gl/family-dashboard.git"

NO_CACHE=false
for arg in "$@"; do
  case "$arg" in
    --no-cache) NO_CACHE=true ;;
    --help|-h)
      grep '^#' "$0" | head -60 | sed 's/^# \?//'
      exit 0 ;;
  esac
done

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}==>${NC} $*"; }
warn()  { echo -e "${YELLOW}WARN:${NC} $*"; }
error() { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }
step()  { echo -e "\n${GREEN}────────────────────────────────────────${NC}"; info "$*"; }

# -- Preflight ─────────────────────────────────────────────────────────────────
step "Checking requirements"
command -v curl &>/dev/null || error "curl not found"
command -v xz   &>/dev/null || error "xz not found  (brew install xz)"
[ "$(uname -s)" = "Darwin" ] || error "This script requires macOS (uses hdiutil / diskutil)"
mkdir -p "$OUTPUT_DIR" "$CACHE_DIR"

# -- Build React frontend on the host -----------------------------------------
step "Building React frontend"
FRONTEND_DIST="$PROJECT_DIR/frontend/dist"
if command -v npm &>/dev/null && [ -d "$PROJECT_DIR/frontend" ]; then
  pushd "$PROJECT_DIR/frontend" >/dev/null
  npm install --silent
  npm run build
  popd >/dev/null
  info "Frontend built → frontend/dist/"
elif [ -d "$FRONTEND_DIST" ] && [ "$(ls -A "$FRONTEND_DIST" 2>/dev/null)" ]; then
  info "Using existing frontend/dist/"
else
  warn "npm not found and no existing dist/ — web UI will not be included in git clone"
fi

# -- Download base image ──────────────────────────────────────────────────────
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
  xz -d --keep "$XZ_FILE"
  # Rename whatever .img the extraction produced
  EXTRACTED=$(find "$CACHE_DIR" -name "*.img" ! -name "raspios-lite-arm64.img" | head -1)
  [ -n "$EXTRACTED" ] && mv "$EXTRACTED" "$BASE_IMG"
fi
[ -f "$BASE_IMG" ] || error "Base image not found after extraction"
info "Base image: $BASE_IMG  ($(du -sh "$BASE_IMG" | cut -f1))"

# -- Copy working image ───────────────────────────────────────────────────────
step "Preparing output image"
cp "$BASE_IMG" "$OUTPUT_IMAGE"
info "Output: $OUTPUT_IMAGE  ($(du -sh "$OUTPUT_IMAGE" | cut -f1))"

# -- Mount the FAT32 boot partition ───────────────────────────────────────────
step "Mounting boot partition (FAT32)"
DISK=$(hdiutil attach \
  -imagekey diskimage-class=CRawDiskImage \
  -nomount "$OUTPUT_IMAGE" 2>&1 \
  | awk '/^\/dev\/disk[0-9]+[[:space:]]/{print $1; exit}')
[ -n "$DISK" ] || error "hdiutil attach failed — could not get a disk device"
info "Loop disk: $DISK"

BOOT_PART="${DISK}s1"
[ -b "$BOOT_PART" ] || {
  hdiutil detach "$DISK" 2>/dev/null || true
  error "Boot partition $BOOT_PART not found (partitions: $(diskutil list "$DISK" 2>&1 | tr '\n' ' '))"
}

BOOT_MNT=$(mktemp -d /tmp/pi-boot-XXXXXX)
diskutil mount -mountPoint "$BOOT_MNT" "$BOOT_PART" \
  || { hdiutil detach "$DISK" 2>/dev/null || true; error "Failed to mount $BOOT_PART"; }
info "Boot partition mounted at $BOOT_MNT"

# -- Inject first-boot scripts ────────────────────────────────────────────────
step "Injecting first-boot bootstrap"

# Copy the dashboard-firstboot.sh onto the FAT32 boot partition
cp "$SCRIPT_DIR/dashboard-firstboot.sh" "$BOOT_MNT/dashboard-firstboot.sh"
info "Copied dashboard-firstboot.sh to boot partition"

# Write the GitHub repo URL (allows end-users to fork and point at their own repo)
echo "$REPO_URL" > "$BOOT_MNT/dashboard-repo.txt"
info "Wrote dashboard-repo.txt: $REPO_URL"

# Modify cmdline.txt to trigger our firstboot script via systemd.run
CMDLINE="$BOOT_MNT/cmdline.txt"
[ -f "$CMDLINE" ] || { diskutil unmount "$BOOT_MNT"; hdiutil detach "$DISK"; error "cmdline.txt not found in boot partition"; }

CURRENT_CMDLINE=$(tr -d '\n' < "$CMDLINE")
TRIGGER="systemd.run=/boot/firmware/dashboard-firstboot.sh systemd.run_success_action=none"

# Remove any existing dashboard trigger to avoid duplication on rebuild
CLEAN_CMDLINE=$(echo "$CURRENT_CMDLINE" \
  | sed 's|systemd\.run=/boot/firmware/dashboard-firstboot\.sh||g' \
  | sed 's|systemd\.run_success_action=none||g' \
  | sed 's/  */ /g' | sed 's/^ //; s/ $//')

# Append our trigger (all on a single line — required by Pi OS)
printf '%s %s\n' "$CLEAN_CMDLINE" "$TRIGGER" > "$CMDLINE"
info "cmdline.txt updated: $(cat "$CMDLINE")"

# -- Unmount ──────────────────────────────────────────────────────────────────
step "Unmounting"
diskutil unmount "$BOOT_MNT"
hdiutil detach "$DISK"
rmdir "$BOOT_MNT" 2>/dev/null || true
info "Done."

# -- Summary ──────────────────────────────────────────────────────────────────
SIZE=$(du -sh "$OUTPUT_IMAGE" | cut -f1)
echo ""
echo "════════════════════════════════════════════════"
echo " Image ready: pi/output/family-dashboard.img"
echo " Size: $SIZE"
echo "════════════════════════════════════════════════"
echo ""
echo " Flash instructions:"
echo "   1. Open Raspberry Pi Imager"
echo "   2. Choose OS → Use custom → pi/output/family-dashboard.img"
echo "   3. Choose your SD card"
echo "   4. Click Next → Edit Settings:"
echo "        WiFi SSID / password  (required for auto-install)"
echo "        Hostname: family-dashboard"
echo "        Enable SSH  (optional)"
echo "        Username: dashboard  Password: dashboard"
echo "   5. Write"
echo ""
echo " First-boot (~2-3 minutes to connect, then 10-15 min to install):"
echo "   Boot 1: Raspberry Pi Imager WiFi setup → auto-reboot"
echo "   Boot 2: Clones GitHub repo & installs dashboard (~10-15 min)"
echo "   After:  http://family-dashboard.local  or  http://<pi-ip>"
echo ""
echo " Updating (from Settings → Updates):"
echo "   git pull + re-runs setup.sh — keeps your data intact"
echo ""
