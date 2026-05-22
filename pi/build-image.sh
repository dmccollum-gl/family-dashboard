#!/usr/bin/env bash
# build-image.sh  --  Produce a flashable Family Dashboard .img for Raspberry Pi
#
# Uses Docker to chroot into the Pi OS ARM64 filesystem and pre-install all
# dashboard dependencies. No first-boot package installation needed.
#
# Targets:  Pi Zero 2 W, Pi 3 B/B+, Pi 4, Pi 5  (arm64 / AArch64)
#
# Requirements on this Mac:
#   curl, xz      (pre-installed)
#   Docker Desktop (running) -- docker.com/products/docker-desktop/
#   npm           (for building the React frontend)
#
# Usage:
#   bash pi/build-image.sh
#   bash pi/build-image.sh --no-cache   # force re-download of base image
#
# Flash the result:
#   1. Open Raspberry Pi Imager
#   2. Choose OS -> Use custom -> pi/output/family-dashboard.img
#   3. Click Next, then "Edit Settings":
#        Set WiFi SSID and password
#        Set hostname (e.g. family-dashboard)
#        Enable SSH if you want remote access
#        Set username/password for the pi user
#   4. Write to SD card
#
# Boot sequence after flashing:
#   Boot 1: Imager applies your WiFi/hostname/SSH settings, auto-reboots (~1 min)
#   Boot 2: Dashboard starts automatically
#            Web UI: http://family-dashboard.local  or  http://<pi-ip>
#            HDMI:   Full-screen dashboard display
# -----------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="$SCRIPT_DIR/output"
CACHE_DIR="$SCRIPT_DIR/.cache"
OUTPUT_IMAGE="$OUTPUT_DIR/family-dashboard-v2.img"

BASE_IMAGE_URL="https://downloads.raspberrypi.com/raspios_lite_arm64_latest"
EXPAND_MB=2048   # extra space added on top of the base image for app files

NO_CACHE=false
for arg in "$@"; do
  case "$arg" in
    --no-cache) NO_CACHE=true ;;
    --help|-h)
      head -35 "$0" | grep '^#' | sed 's/^# \?//'
      exit 0 ;;
  esac
done

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}==>${NC} $*"; }
warn()  { echo -e "${YELLOW}WARN:${NC} $*"; }
error() { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }
step()  { echo -e "\n${GREEN}----------------------------------------${NC}"; info "$*"; }

# -- Preflight -----------------------------------------------------------------
step "Checking requirements"
command -v curl   &>/dev/null || error "curl not found"
command -v xz     &>/dev/null || error "xz not found  (brew install xz)"
command -v docker &>/dev/null || error "Docker not found -- install Docker Desktop from https://www.docker.com/products/docker-desktop/"
docker info &>/dev/null 2>&1  || error "Docker daemon not running -- open Docker Desktop and try again"
mkdir -p "$OUTPUT_DIR" "$CACHE_DIR"

# -- Build React frontend on the host ------------------------------------------
step "Building React frontend"
FRONTEND_DIST="$PROJECT_DIR/frontend/dist"
if command -v npm &>/dev/null && [ -d "$PROJECT_DIR/frontend" ]; then
  pushd "$PROJECT_DIR/frontend" >/dev/null
  npm install --silent
  npm run build
  popd >/dev/null
  info "Frontend built -> frontend/dist/"
elif [ -d "$FRONTEND_DIST" ] && [ "$(ls -A "$FRONTEND_DIST" 2>/dev/null)" ]; then
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

# -- Customize with Docker -----------------------------------------------------
step "Customizing image with Docker"
info "Chrooting into Pi OS ARM64 filesystem to pre-install dashboard."
info "First run takes 15-30 minutes (downloads ARM packages inside emulation)."

DOCKER_ARGS=(
  --rm
  --privileged
  -v "${OUTPUT_IMAGE}:/pi.img"
  -v "${PROJECT_DIR}/backend:/app/backend:ro"
  -v "${SCRIPT_DIR}/services:/services:ro"
  -v "${SCRIPT_DIR}/chroot-setup.sh:/chroot-setup.sh:ro"
  -v "${SCRIPT_DIR}/docker-customize.sh:/docker-customize.sh:ro"
  -v "${SCRIPT_DIR}/setup-mode.sh:/setup-mode.sh:ro"
  -v "${SCRIPT_DIR}/pi-setup-apply.sh:/pi-setup-apply.sh:ro"
)

if [ -d "$FRONTEND_DIST" ] && [ "$(ls -A "$FRONTEND_DIST" 2>/dev/null)" ]; then
  DOCKER_ARGS+=(-v "${FRONTEND_DIST}:/app/frontend-dist:ro")
fi

docker run "${DOCKER_ARGS[@]}" debian:bookworm-slim bash /docker-customize.sh

info "Docker customization complete."

# -- Summary ------------------------------------------------------------------
SIZE=$(du -sh "$OUTPUT_IMAGE" | cut -f1)
echo ""
echo "============================================"
echo " Image ready: pi/output/family-dashboard-v2.img"
echo " Size: $SIZE"
echo "============================================"
echo ""
echo " Flash with Raspberry Pi Imager:"
echo "   1. Choose OS -> Use custom -> pi/output/family-dashboard.img"
echo "   2. Choose your SD card"
echo "   3. Click Next -> Write  (no customisation step needed)"
echo ""
echo " First-boot setup:"
echo "   1. Connect to the 'Dashboard-Setup' WiFi hotspot"
echo "   2. Open http://10.42.0.1 in a browser"
echo "      (or your device may show a 'Sign in to network' popup automatically)"
echo "   3. Enter WiFi, device name, city, and activation code"
echo "   4. Pi reboots and connects to your network"
echo "   5. Access the dashboard at http://<device-name>.local"
echo ""
