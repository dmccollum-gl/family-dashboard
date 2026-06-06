#!/bin/bash
# dashboard-firstboot.sh
#
# Runs on the Pi's second boot via:
#   systemd.run=/boot/firmware/dashboard-firstboot.sh
# (added to cmdline.txt by build-image.sh)
#
# This script does NOT need internet — it just wires up the
# dashboard-install.service which does the actual install once
# network is available.
#
# After this script runs it removes itself from cmdline.txt so it
# never runs again.
# ---------------------------------------------------------------------------

LOG=/var/log/dashboard-firstboot.log
exec > "$LOG" 2>&1

echo "=== Dashboard first-boot bootstrap $(date) ==="

# Read the repo URL from the FAT32 boot partition (written by build-image.sh)
REPO_URL="$(cat /boot/firmware/dashboard-repo.txt 2>/dev/null \
         || cat /boot/dashboard-repo.txt 2>/dev/null \
         || echo 'https://github.com/dmccollum-gl/family-dashboard.git')"
echo "Repo URL: $REPO_URL"

# ── Create the install script ──────────────────────────────────────────────
cat > /usr/local/sbin/dashboard-install.sh << 'INSTALL_EOF'
#!/bin/bash
# dashboard-install.sh  --  First-boot installation from GitHub
# Runs as a systemd one-shot service after network is available.

LOG=/var/log/dashboard-install.log
exec > >(tee -a "$LOG") 2>&1

echo ""
echo "=== Family Dashboard Installation $(date) ==="
echo ""

REPO_URL="$(cat /boot/firmware/dashboard-repo.txt 2>/dev/null \
          || cat /boot/dashboard-repo.txt 2>/dev/null \
          || echo 'https://github.com/dmccollum-gl/family-dashboard.git')"
echo "Repo: $REPO_URL"

# ── Wait for internet ──────────────────────────────────────────────────────
echo ""
echo "Waiting for internet access (up to 10 minutes)..."
CONNECTED=0
for i in $(seq 1 120); do
    if curl -s --max-time 5 --head https://github.com > /dev/null 2>&1; then
        echo "Internet OK after $((i * 5)) seconds."
        CONNECTED=1
        break
    fi
    if (( i % 12 == 0 )); then
        echo "  Still waiting... ($((i * 5))s / 600s)"
    fi
    sleep 5
done

if [ "$CONNECTED" -eq 0 ]; then
    echo ""
    echo "ERROR: No internet after 10 minutes."
    echo "Ensure WiFi was configured in Raspberry Pi Imager and reboot."
    exit 1
fi

# ── Install git if missing ─────────────────────────────────────────────────
if ! command -v git &>/dev/null; then
    echo ""
    echo "Installing git..."
    apt-get update -qq && apt-get install -y -qq git
fi

# ── Clone the repository ───────────────────────────────────────────────────
echo ""
echo "Cloning $REPO_URL..."
rm -rf /opt/dashboard
git clone "$REPO_URL" /opt/dashboard
echo "Clone complete."

# ── Run the setup script ───────────────────────────────────────────────────
echo ""
echo "Running setup..."
bash /opt/dashboard/pi/setup.sh

echo ""
echo "=== Installation complete $(date) ==="
echo ""
IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "unknown")
echo "  Dashboard: http://$IP"
echo "  Logs:      journalctl -u dashboard-backend -f"
INSTALL_EOF

chmod +x /usr/local/sbin/dashboard-install.sh
echo "Created /usr/local/sbin/dashboard-install.sh"

# ── Create the systemd service ──────────────────────────────────────────────
cat > /etc/systemd/system/dashboard-install.service << 'SVC_EOF'
[Unit]
Description=Family Dashboard — First-Boot Installation from GitHub
After=network.target
# Only run if the app is not already installed
ConditionPathExists=!/opt/dashboard/.installed

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/dashboard-install.sh
TimeoutStartSec=1800
StandardOutput=journal+console
StandardError=journal+console

[Install]
WantedBy=multi-user.target
SVC_EOF

systemctl daemon-reload
systemctl enable dashboard-install.service
echo "dashboard-install.service enabled."

# Start it immediately (will wait internally for internet via the polling loop)
systemctl start --no-block dashboard-install.service
echo "dashboard-install.service started (non-blocking)."

# ── Remove this trigger from cmdline.txt ────────────────────────────────────
for CMDLINE_PATH in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
    if [ -f "$CMDLINE_PATH" ]; then
        sed -i 's| systemd\.run=/boot/firmware/dashboard-firstboot\.sh||g' "$CMDLINE_PATH"
        sed -i 's| systemd\.run_success_action=none||g' "$CMDLINE_PATH"
        # Also handle the case where it appears without leading space
        sed -i 's|systemd\.run=/boot/firmware/dashboard-firstboot\.sh ||g' "$CMDLINE_PATH"
        sed -i 's|systemd\.run_success_action=none ||g' "$CMDLINE_PATH"
        echo "Cleaned cmdline.txt at $CMDLINE_PATH:"
        cat "$CMDLINE_PATH"
        break
    fi
done

echo ""
echo "=== First-boot bootstrap complete $(date) ==="
echo "Installation running in the background."
echo "Track progress: journalctl -u dashboard-install -f"
