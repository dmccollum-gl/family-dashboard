#!/usr/bin/env bash
# build-mac-mini-image.sh
# Builds a bootable USB image for Mac Mini that installs Ubuntu Server 24.04 LTS
# and automatically sets up the Dashboard Provisioning Server.
#
# Runs on macOS. Uses Docker to perform the ISO build inside a Linux environment
# (same pattern as the Pi image builder).
#
# Usage:
#   cd server
#   ./build-mac-mini-image.sh [--arch amd64|arm64]
#
# Output: server/output/mac-mini-provisioning-server.iso
# Flash:  Use Balena Etcher (GUI) or:
#   diskutil list                  # find your USB disk number
#   diskutil unmountDisk /dev/diskN
#   sudo dd if=output/mac-mini-provisioning-server.iso of=/dev/rdiskN bs=1m status=progress
#
# Target hardware:
#   Intel Mac Mini (2018-2020) → use --arch amd64  (default)
#   Apple Silicon Mac Mini (2020+) → use --arch arm64
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/output"
CACHE_DIR="$SCRIPT_DIR/../pi/.cache"   # reuse Pi image builder cache dir
ARCH="amd64"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[build]${NC} $*"; }
warn()  { echo -e "${YELLOW}[build]${NC} $*"; }
error() { echo -e "${RED}[build]${NC} $*" >&2; exit 1; }

# -- Parse args ----------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --arch) ARCH="$2"; shift 2 ;;
    *) error "Unknown argument: $1" ;;
  esac
done

[[ "$ARCH" == "amd64" || "$ARCH" == "arm64" ]] || error "--arch must be amd64 or arm64"

# -- Ubuntu ISO URLs -----------------------------------------------------------
UBUNTU_VERSION="24.04.2"
if [[ "$ARCH" == "amd64" ]]; then
  ISO_URL="https://releases.ubuntu.com/${UBUNTU_VERSION}/ubuntu-${UBUNTU_VERSION}-live-server-amd64.iso"
  ISO_CACHE="$CACHE_DIR/ubuntu-${UBUNTU_VERSION}-server-amd64.iso"
else
  ISO_URL="https://cdimage.ubuntu.com/releases/${UBUNTU_VERSION}/release/ubuntu-${UBUNTU_VERSION}-live-server-arm64.iso"
  ISO_CACHE="$CACHE_DIR/ubuntu-${UBUNTU_VERSION}-server-arm64.iso"
fi

OUTPUT_ISO="$OUTPUT_DIR/mac-mini-provisioning-server-${ARCH}.iso"

# -- Pre-flight ----------------------------------------------------------------
info "Target arch:  $ARCH"
info "Ubuntu:       $UBUNTU_VERSION"
info "Output:       $OUTPUT_ISO"
echo ""

[[ "$(uname)" == "Darwin" ]] || error "Run this script on macOS."

# Check Docker
if ! command -v docker &>/dev/null; then
  error "Docker is required. Install Docker Desktop from https://www.docker.com/products/docker-desktop/"
fi
docker info &>/dev/null 2>&1 || error "Docker is not running. Start Docker Desktop first."

mkdir -p "$OUTPUT_DIR" "$CACHE_DIR"

# -- Download Ubuntu ISO -------------------------------------------------------
if [[ ! -f "$ISO_CACHE" ]]; then
  info "Downloading Ubuntu Server ${UBUNTU_VERSION} (${ARCH})..."
  curl -L --progress-bar -C - -o "$ISO_CACHE" "$ISO_URL"
  info "Download complete."
else
  info "Using cached ISO: $ISO_CACHE"
fi

# -- Bundle provisioning server source (exclude secrets and generated files) ---
BUNDLE="$SCRIPT_DIR/mac-mini/provisioning-server.tar.gz"
info "Bundling provisioning server source..."
(cd "$SCRIPT_DIR/.." && tar czf "$BUNDLE" \
  --exclude='server/.venv' \
  --exclude='server/server.db' \
  --exclude='server/.env' \
  --exclude='server/output' \
  --exclude='server/mac-mini/provisioning-server.tar.gz' \
  --exclude='server/__pycache__' \
  --exclude='server/**/__pycache__' \
  --exclude='server/**/*.pyc' \
  --exclude='server/.cache' \
  server/
)
info "Bundle created: $BUNDLE"

# -- Build the ISO inside a Linux Docker container ----------------------------
info "Building ISO inside Docker (Ubuntu 24.04 container)..."
docker run --rm \
  --platform "linux/$ARCH" \
  -v "$ISO_CACHE:/input/ubuntu.iso:ro" \
  -v "$SCRIPT_DIR/mac-mini:/mac-mini:ro" \
  -v "$OUTPUT_DIR:/output" \
  ubuntu:24.04 \
  bash /mac-mini/build-iso.sh "$ARCH"

info ""
info "Build complete!"
info "ISO: $OUTPUT_ISO"
info ""
info "Flash to USB:"
info "  Option A (GUI): Use Balena Etcher → https://etcher.balena.io"
info "  Option B (CLI):"
info "    diskutil list                    # find your USB (e.g. /dev/disk4)"
info "    diskutil unmountDisk /dev/diskN"
info "    sudo dd if=$OUTPUT_ISO of=/dev/rdiskN bs=1m status=progress"
info ""
warn "  This will ERASE the USB drive completely."
warn "  Insert USB into Mac Mini and boot — hold Option key to select boot device."
info ""
info "On first boot the server will walk you through entering your API keys."
