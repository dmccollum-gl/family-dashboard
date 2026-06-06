#!/bin/bash
# update.sh  --  Update the Family Dashboard from GitHub
#
# Called by the backend via:  sudo bash /opt/dashboard/pi/update.sh
# Triggered from Settings → Updates (owner only)
#
# What it does:
#   1. git pull origin main  (downloads new code + pre-built frontend)
#   2. pip install -r requirements.txt  (via setup.sh, idempotent)
#   3. systemctl restart dashboard-backend  (via setup.sh)
#
# The backend process will be restarted mid-way; that is expected.
# ---------------------------------------------------------------------------

APP_DIR=/opt/dashboard

echo "=== Family Dashboard Update $(date) ==="
echo ""

# Verify this is a git installation
if [ ! -d "$APP_DIR/.git" ]; then
    echo "ERROR: $APP_DIR is not a git repository."
    echo "Updates only work when the dashboard was installed via the Pi image."
    exit 1
fi

if ! command -v git &>/dev/null; then
    echo "ERROR: git is not installed."
    exit 1
fi

# Pull latest code as the dashboard user (who owns the files)
echo "--- Pulling from GitHub ---"
sudo -u dashboard git -C "$APP_DIR" pull origin main
echo ""

# Re-run setup.sh (idempotent — only installs/updates what changed)
echo "--- Running setup.sh ---"
bash "$APP_DIR/pi/setup.sh"

echo ""
echo "=== Update complete $(date) ==="
