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
    rsync util-linux
  info "Native ARM64 -- no QEMU required"
else
  # x86_64 container -- need QEMU to execute ARM64 binaries in the chroot
  apt-get install -y -qq \
    qemu-user-static binfmt-support \
    kpartx parted e2fsprogs dosfstools \
    rsync util-linux
  update-binfmts --enable || true
  info "QEMU binfmt: $(ls /proc/sys/fs/binfmt_misc/qemu-aarch64 2>/dev/null && echo OK || echo not registered)"
fi

# -- Mount the Pi OS image -----------------------------------------------------
info "Attaching /pi.img as loop device..."
LOOP=$(losetup -f --show -P /pi.img)
info "Loop device: $LOOP"

# Let the kernel rescan the partition table
partprobe "$LOOP" 2>/dev/null || true
sleep 1

# Identify boot (FAT32) and root (ext4) partitions
BOOT_PART="${LOOP}p1"
ROOT_PART="${LOOP}p2"
[ -b "$BOOT_PART" ] || error "Boot partition $BOOT_PART not found"
[ -b "$ROOT_PART" ] || error "Root partition $ROOT_PART not found"

# Expand the root partition to fill the extra space we appended
info "Expanding root partition to fill image..."
# Get the start sector of the root partition
START_SECTOR=$(parted -ms "$LOOP" unit s print | awk -F: '$1==2{print $2}' | tr -d 's')
parted -s "$LOOP" resizepart 2 100%
e2fsck -f -y "$ROOT_PART" >/dev/null 2>&1 || true
resize2fs "$ROOT_PART" >/dev/null 2>&1
info "Root partition expanded."

# -- Mount filesystems ---------------------------------------------------------
MNT=/mnt/pi
mkdir -p "$MNT"
mount "$ROOT_PART" "$MNT"
mount "$BOOT_PART" "$MNT/boot"

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

# Copy service files and setup script
cp /services/dashboard-backend.service "$MNT/etc/systemd/system/"
cp /services/dashboard-display.service "$MNT/etc/systemd/system/"
cp /chroot-setup.sh "$MNT/tmp/chroot-setup.sh"
chmod +x "$MNT/tmp/chroot-setup.sh"

# -- Run setup inside the ARM64 chroot ----------------------------------------
info "Entering ARM64 chroot..."
chroot "$MNT" /bin/bash /tmp/chroot-setup.sh
info "Chroot setup complete."

# -- Cleanup -------------------------------------------------------------------
info "Unmounting..."
# Flush writes
sync

umount "$MNT/dev/pts" 2>/dev/null || true
umount "$MNT/dev"     2>/dev/null || true
umount "$MNT/sys"     2>/dev/null || true
umount "$MNT/proc"    2>/dev/null || true
umount "$MNT/boot"    2>/dev/null || true
umount "$MNT"         2>/dev/null || true
losetup -d "$LOOP"    2>/dev/null || true

info "Image customisation done."
