#!/bin/bash
# update.sh  --  Update the Family Dashboard from GitHub
#
# Called by the backend via:  sudo bash /opt/dashboard/pi/update.sh
# Triggered from Settings → Updates (owner only)
#
# What it does:
#   1. git fetch origin main  (download latest commits)
#   2. git reset --hard origin/main  (hard-reset to remote — discards any local
#      modifications so files SCP-deployed for testing never block updates)
#   3. pip install -r requirements.txt  (via setup.sh, idempotent)
#   4. systemctl restart dashboard-backend  (via setup.sh)
#
# The backend process will be restarted mid-way; that is expected.
# ---------------------------------------------------------------------------

APP_DIR=/opt/dashboard
REPO_URL="https://github.com/dmccollum-gl/family-dashboard.git"

echo "=== Family Dashboard Update $(date) ==="
echo ""

if ! command -v git &>/dev/null; then
    echo "ERROR: git is not installed."
    exit 1
fi

# Bootstrap a git checkout in place if this install wasn't set up from git.
# Runtime files (dashboard.db, dashboard_config.json, .env, frontend-dist/) are
# untracked/gitignored, so the reset below leaves them untouched.
if [ ! -d "$APP_DIR/.git" ]; then
    echo "--- No git repo found — linking $APP_DIR to GitHub ---"
    sudo -u dashboard git -C "$APP_DIR" init -q
    sudo -u dashboard git -C "$APP_DIR" remote add origin "$REPO_URL" 2>/dev/null \
      || sudo -u dashboard git -C "$APP_DIR" remote set-url origin "$REPO_URL"
fi

# Fetch latest commits + release tags, then hard-reset to origin/main so every
# commit (tagged or not) is picked up automatically. reset --hard (not pull)
# also lets it adopt an existing non-git tree and ignores SCP-deployed changes.
echo "--- Pulling from GitHub ---"
sudo -u dashboard git -C "$APP_DIR" fetch --tags --force origin main

echo "--- Updating to latest main ---"
sudo -u dashboard git -C "$APP_DIR" reset --hard origin/main
sudo -u dashboard git -C "$APP_DIR" branch -M main 2>/dev/null || true
echo ""

# Re-run setup.sh (idempotent — only installs/updates what changed)
echo "--- Running setup.sh ---"
bash "$APP_DIR/pi/setup.sh"

echo ""
echo "=== Update complete $(date) ==="
