#!/usr/bin/env bash
# Runs inside a privileged debian:bookworm-slim Docker container.
# Mounts the Pi OS .img, chroots into the ARM64 filesystem, runs chroot-setup.sh.
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'
info()  { echo -e "${GREEN}  [docker]${NC} $*"; }
error() { echo -e "${RED}  [docker] ERROR:${NC} $*" >&2; exit 1; }

# -- Install host tools --------------------------------------------------------
info "Installing host tools..."
apt-get update -qq

CONTAINER_ARCH="$(uname -m)"
info "Container architecture: $CONTAINER_ARCH"

if [ "$CONTAINER_ARCH" = "aarch64" ] || [ "$CONTAINER_ARCH" = "arm64" ]; then
  # ARM64 container (Apple Silicon / native ARM host) -- no QEMU needed;
  # the Pi OS arm64 image will chroot natively.
  apt-get install -y -qq \
    kpartx parted e2fsprogs dosfstools \
    rsync util-linux \
    python3-venv python3-pip python3-dev \
    libsdl2-2.0-0
  info "Native ARM64 -- no QEMU required"

  # Download wheels here in the container (reliable network), then install
  # inside the chroot using the Pi OS's own Python. This avoids the version
  # mismatch that occurs when the container's Python differs from Pi OS's Python.
  info "Pre-downloading Python wheels in container (reliable network)..."
  python3 -m venv /tmp/dl-venv
  /tmp/dl-venv/bin/pip install --quiet --upgrade pip
  mkdir -p /tmp/pi-wheels
  /tmp/dl-venv/bin/pip download --quiet --prefer-binary \
    --dest /tmp/pi-wheels \
    -r /app/backend/requirements.txt \
    pygame uvloop httptools qrcode
  info "Wheels downloaded to /tmp/pi-wheels ($(ls /tmp/pi-wheels | wc -l) files)."
else
  # x86_64 container -- need QEMU to execute ARM64 binaries in the chroot.
  # Cannot copy x86 packages into an ARM64 chroot, so pip runs inside the
  # chroot under QEMU emulation instead.
  apt-get install -y -qq \
    qemu-user-static binfmt-support \
    kpartx parted e2fsprogs dosfstools \
    rsync util-linux
  update-binfmts --enable || true
  info "QEMU binfmt: $(ls /proc/sys/fs/binfmt_misc/qemu-aarch64 2>/dev/null && echo OK || echo not registered)"
fi

# -- Mount the Pi OS image -----------------------------------------------------
info "Attaching /pi.img as loop device..."
# Use plain losetup (no -P); kpartx is more reliable for partition devices
# inside Docker Desktop's Linux VM on Apple Silicon.
LOOP=$(losetup -f --show /pi.img)
info "Loop device: $LOOP"

# kpartx creates /dev/mapper/<loop>p1 and <loop>p2  --  works in all containers
info "Creating partition device mappings via kpartx..."
kpartx -av "$LOOP"
sleep 1

LOOP_NAME=$(basename "$LOOP")
BOOT_PART="/dev/mapper/${LOOP_NAME}p1"
ROOT_PART="/dev/mapper/${LOOP_NAME}p2"
[ -b "$BOOT_PART" ] || error "Boot partition $BOOT_PART not found"
[ -b "$ROOT_PART" ] || error "Root partition $ROOT_PART not found"

# Expand the root partition to fill the extra space we appended
info "Expanding root partition to fill image..."
parted -s "$LOOP" resizepart 2 100%
# Refresh kpartx mappings so ROOT_PART reflects the new partition size
kpartx -u "$LOOP"
sleep 1
e2fsck -f -y "$ROOT_PART" >/dev/null 2>&1 || true
resize2fs "$ROOT_PART" >/dev/null 2>&1
info "Root partition expanded."

# -- Mount filesystems ---------------------------------------------------------
MNT=/mnt/pi
mkdir -p "$MNT"
mount "$ROOT_PART" "$MNT"

# Pi OS Bookworm mounts the FAT32 boot partition at /boot/firmware/ (not /boot/).
# mkdir -p is safe even if the directory already exists in the ext4 root.
mkdir -p "$MNT/boot/firmware"
mount "$BOOT_PART" "$MNT/boot/firmware"

# Kernel pseudo-filesystems for chroot
mount --bind /proc    "$MNT/proc"
mount --bind /sys     "$MNT/sys"
mount --bind /dev     "$MNT/dev"
mount --bind /dev/pts "$MNT/dev/pts"

# DNS inside chroot
cp /etc/resolv.conf "$MNT/etc/resolv.conf"

# -- Install QEMU binary for ARM64 translation ---------------------------------
# Only needed on x86_64 containers; ARM64 containers chroot natively
if [ "$CONTAINER_ARCH" != "aarch64" ] && [ "$CONTAINER_ARCH" != "arm64" ]; then
  info "Copying qemu-aarch64-static for ARM64 emulation..."
  cp /usr/bin/qemu-aarch64-static "$MNT/usr/bin/qemu-aarch64-static"
else
  info "ARM64 container -- chroot will run natively, no QEMU binary needed"
fi

# -- Copy app source into chroot -----------------------------------------------
info "Copying application files..."
mkdir -p "$MNT/opt/dashboard/backend"
rsync -a --exclude '__pycache__' --exclude '*.pyc' \
  /app/backend/ "$MNT/opt/dashboard/backend/"

# Copy pre-built React frontend if present
if [ -d /app/frontend-dist ] && [ "$(ls -A /app/frontend-dist 2>/dev/null)" ]; then
  mkdir -p "$MNT/opt/dashboard/frontend-dist"
  rsync -a /app/frontend-dist/ "$MNT/opt/dashboard/frontend-dist/"
  info "Frontend dist copied."
else
  info "No pre-built frontend dist found -- skipping."
fi

# Copy service files and scripts into the chroot's /tmp for the setup script
cp /services/dashboard-backend.service  "$MNT/etc/systemd/system/"
cp /services/dashboard-display.service  "$MNT/etc/systemd/system/"
cp /services/dashboard-setup.service    "$MNT/tmp/dashboard-setup.service"
cp /chroot-setup.sh                     "$MNT/tmp/chroot-setup.sh"
cp /setup-mode.sh                       "$MNT/tmp/setup-mode.sh"
cp /pi-setup-apply.sh                   "$MNT/tmp/pi-setup-apply.sh"
chmod +x "$MNT/tmp/chroot-setup.sh" "$MNT/tmp/setup-mode.sh" "$MNT/tmp/pi-setup-apply.sh"

# -- Copy downloaded wheels into chroot for offline install -------------------
if [ "$CONTAINER_ARCH" = "aarch64" ] || [ "$CONTAINER_ARCH" = "arm64" ]; then
  info "Copying wheels into chroot for offline install..."
  mkdir -p "$MNT/tmp/pi-wheels"
  cp /tmp/pi-wheels/*.whl "$MNT/tmp/pi-wheels/" 2>/dev/null || true
  info "Wheels staged in chroot ($(ls $MNT/tmp/pi-wheels | wc -l) files)."
fi

# -- Run setup inside the ARM64 chroot ----------------------------------------
info "Verifying chroot target filesystem..."
ls "$MNT/bin" "$MNT/usr/bin" >/dev/null 2>&1 || {
  info "WARNING: /bin or /usr/bin missing  --  listing $MNT top-level:"
  ls "$MNT"
  error "Pi OS root filesystem not found at $MNT  --  aborting"
}
# On Debian Bookworm /bin is a symlink to usr/bin; resolve it explicitly
BASH_BIN="$MNT/usr/bin/bash"
[ -f "$BASH_BIN" ] || BASH_BIN="$MNT/bin/bash"
[ -f "$BASH_BIN" ] || error "Cannot find bash in chroot at $MNT"
info "Found bash at ${BASH_BIN#$MNT}"
info "Entering ARM64 chroot..."
chroot "$MNT" /usr/bin/bash /tmp/chroot-setup.sh
info "Chroot setup complete."

# -- Cleanup -------------------------------------------------------------------
info "Unmounting..."
# Flush writes
sync

umount "$MNT/dev/pts" 2>/dev/null || true
umount "$MNT/dev"     2>/dev/null || true
umount "$MNT/sys"     2>/dev/null || true
umount "$MNT/proc"    2>/dev/null || true
umount "$MNT/boot/firmware" 2>/dev/null || true
umount "$MNT"         2>/dev/null || true
kpartx -dv "$LOOP"    2>/dev/null || true
losetup -d "$LOOP"    2>/dev/null || true

info "Image customisation done."
