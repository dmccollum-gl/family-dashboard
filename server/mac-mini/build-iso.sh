#!/usr/bin/env bash
# Runs inside a Ubuntu 24.04 Docker container.
# Extracts the Ubuntu Server ISO, injects autoinstall config + provisioning
# server files, then repacks a bootable ISO.
set -euo pipefail

ARCH="${1:-amd64}"
INPUT_ISO="/input/ubuntu.iso"
WORK_DIR="/tmp/iso-build"
EXTRACT_DIR="$WORK_DIR/extracted"
OUTPUT_ISO="/output/mac-mini-provisioning-server-${ARCH}.iso"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[build-iso]${NC} $*"; }
warn()  { echo -e "${YELLOW}[build-iso]${NC} $*"; }

# -- Install tools -------------------------------------------------------------
info "Installing ISO build tools..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq xorriso mtools isolinux 7zip dosfstools

# -- Extract ISO ---------------------------------------------------------------
info "Extracting Ubuntu ISO..."
mkdir -p "$EXTRACT_DIR"
7z x -o"$EXTRACT_DIR" "$INPUT_ISO" -y >/dev/null
chmod -R u+w "$EXTRACT_DIR"

# -- Create autoinstall directory in ISO root ----------------------------------
mkdir -p "$EXTRACT_DIR/autoinstall"

info "Injecting autoinstall user-data..."
cp /mac-mini/user-data  "$EXTRACT_DIR/autoinstall/user-data"
cp /mac-mini/meta-data  "$EXTRACT_DIR/autoinstall/meta-data"

info "Injecting post-install scripts..."
cp /mac-mini/install-server.sh "$EXTRACT_DIR/autoinstall/install-server.sh"
cp /mac-mini/setup-server.py   "$EXTRACT_DIR/autoinstall/setup-server.py"
chmod +x "$EXTRACT_DIR/autoinstall/install-server.sh"

info "Bundling provisioning server source..."
cp /mac-mini/provisioning-server.tar.gz "$EXTRACT_DIR/autoinstall/"

# -- Custom GRUB config --------------------------------------------------------
# Replace the entire grub.cfg rather than patching with sed.
# Key requirements:
#   1. timeout=0  — boot immediately, no user interaction
#   2. autoinstall before ---  — subiquity needs it on the kernel cmdline
#   3. ds=nocloud\;seedfrom=  — tells cloud-init where to find user-data
#
# Ubuntu 24.04 live-server uses /casper/vmlinuz + /casper/initrd.
info "Writing custom GRUB config..."

GRUB_CFG='set default="0"
set timeout=0
set timeout_style=hidden

menuentry "Install Dashboard Provisioning Server" {
    linux   /casper/vmlinuz quiet autoinstall ds=nocloud\;seedfrom=/cdrom/autoinstall/
    initrd  /casper/initrd
}'

echo "$GRUB_CFG" > "$EXTRACT_DIR/boot/grub/grub.cfg"

# loopback.cfg is used by Ventoy / grub2 loopback — patch it too
if [ -f "$EXTRACT_DIR/boot/grub/loopback.cfg" ]; then
    echo "$GRUB_CFG" > "$EXTRACT_DIR/boot/grub/loopback.cfg"
fi

# -- Locate EFI partition image ------------------------------------------------
info "Locating EFI partition image..."
EFI_IMG=""
for candidate in \
  "$EXTRACT_DIR/[BOOT]/2-Boot-NoEmul.img" \
  "$EXTRACT_DIR/boot/grub/efi.img"
do
  if [ -f "$candidate" ]; then
    EFI_IMG="$candidate"
    info "  Found EFI image: $candidate"
    break
  fi
done

if [ -z "$EFI_IMG" ]; then
  info "  EFI image not found in extracted files; extracting from ISO partition table..."
  EFI_LINE=$(fdisk -l "$INPUT_ISO" 2>/dev/null | grep -i "EFI" || true)
  if [ -n "$EFI_LINE" ]; then
    EFI_START=$(echo "$EFI_LINE" | awk '{print $2}')
    EFI_END=$(echo   "$EFI_LINE" | awk '{print $3}')
    EFI_SECTORS=$(( EFI_END - EFI_START + 1 ))
    dd if="$INPUT_ISO" bs=512 skip="$EFI_START" count="$EFI_SECTORS" \
       of="$WORK_DIR/efi.img" 2>/dev/null
    EFI_IMG="$WORK_DIR/efi.img"
    info "  Extracted EFI partition ($EFI_SECTORS sectors at offset $EFI_START)"
  else
    echo "ERROR: Cannot locate EFI partition in ISO" >&2
    exit 1
  fi
fi

# Copy to stable working path
cp "$EFI_IMG" "$WORK_DIR/efi.img"
EFI_IMG="$WORK_DIR/efi.img"
rm -rf "$EXTRACT_DIR/[BOOT]"

# -- Patch grub.cfg INSIDE the EFI partition image ----------------------------
# The Mac Mini boots via EFI. The EFI partition has its own grub.cfg that
# searches for the ISO by its original Ubuntu label. Since we renamed it to
# DASHBOARD-PROVISION, EFI GRUB can't find the filesystem and falls back to
# the normal interactive installer. We replace the embedded grub.cfg with one
# that searches by our label instead.
info "Patching grub.cfg inside EFI partition..."
mkdir -p /tmp/efi-mount
if mount -o loop,rw "$WORK_DIR/efi.img" /tmp/efi-mount 2>/dev/null; then
  EFI_GRUB_CFG=$(find /tmp/efi-mount -name "grub.cfg" 2>/dev/null | head -1)
  if [ -n "$EFI_GRUB_CFG" ]; then
    info "  Replacing: $EFI_GRUB_CFG"
    # EFI grub.cfg needs to search for the ISO filesystem by label first,
    # then the kernel paths are relative to that filesystem.
    cat > "$EFI_GRUB_CFG" << 'EOF'
insmod part_gpt
insmod fat
insmod iso9660
search --no-floppy --label --set=root DASHBOARD-PROVISION

set default="0"
set timeout=0
set timeout_style=hidden

menuentry "Install Dashboard Provisioning Server" {
    linux   /casper/vmlinuz quiet autoinstall ds=nocloud\;seedfrom=/cdrom/autoinstall/
    initrd  /casper/initrd
}
EOF
  else
    warn "  No grub.cfg found inside EFI partition — EFI boot may not be fully automated"
  fi
  umount /tmp/efi-mount
else
  warn "  Could not mount EFI partition image — EFI boot may fall back to interactive installer"
fi

EFI_SIZE_BYTES=$(stat -c %s "$EFI_IMG")
EFI_SIZE_SECTORS=$(( EFI_SIZE_BYTES / 512 ))
info "  EFI image: ${EFI_SIZE_BYTES} bytes / ${EFI_SIZE_SECTORS} sectors"

# -- Determine BIOS El Torito boot image path ----------------------------------
ELTORITO_IMG=""
for candidate in \
  "$EXTRACT_DIR/boot/grub/i386-pc/eltorito.img" \
  "$EXTRACT_DIR/boot/grub/bios.img"
do
  if [ -f "$candidate" ]; then
    ELTORITO_IMG="/${candidate#$EXTRACT_DIR/}"
    info "Found BIOS El Torito image: $ELTORITO_IMG"
    break
  fi
done

if [ -z "$ELTORITO_IMG" ]; then
  echo "ERROR: Cannot find BIOS El Torito boot image in extracted ISO" >&2
  exit 1
fi

# -- Extract hybrid MBR --------------------------------------------------------
dd if="$INPUT_ISO" bs=1 count=432 of="$WORK_DIR/boot_hybrid.img" 2>/dev/null

# -- Repack ISO ----------------------------------------------------------------
info "Repacking ISO (this takes a minute)..."

xorriso -as mkisofs \
  -r \
  -V "DASHBOARD-PROVISION" \
  -o "$OUTPUT_ISO" \
  --grub2-mbr "$WORK_DIR/boot_hybrid.img" \
  --protective-msdos-label \
  -partition_offset 16 \
  -appended_part_as_gpt \
  --mbr-force-bootable \
  -append_partition 2 28732ac11ff8d211ba4b00a0c93ec93b "$EFI_IMG" \
  -iso_mbr_part_type a2a0d0ebe5b9334487c068b6b72699c7 \
  -c '/boot.catalog' \
  -b "$ELTORITO_IMG" \
  -no-emul-boot -boot-load-size 4 -boot-info-table --grub2-boot-info \
  -eltorito-alt-boot \
  -e "--interval:appended_partition_2:all::" \
  -no-emul-boot \
  "$EXTRACT_DIR" 2>&1 | grep -v "^$" | tail -20

info "ISO written to $OUTPUT_ISO"
