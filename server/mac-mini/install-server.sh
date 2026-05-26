#!/usr/bin/env bash
# install-server.sh
# Runs inside the target OS during autoinstall late-commands.
# Sets up systemd services for the provisioning server and cloudflared.
set -euo pipefail

APP_DIR=/opt/provisioning
VENV="$APP_DIR/.venv"
SETUP_DIR="$APP_DIR/mac-mini"
PROVISION_USER=provision

GREEN='\033[0;32m'; NC='\033[0m'
info() { echo -e "${GREEN}[install]${NC} $*"; }

# -- provisioning-server systemd service --------------------------------------
info "Creating provisioning-server.service..."
cat > /etc/systemd/system/provisioning-server.service << 'SVC'
[Unit]
Description=Dashboard Tunnel Provisioning Server
After=network-online.target
Wants=network-online.target
ConditionPathExists=/opt/provisioning/.env

[Service]
Type=simple
User=provision
Group=provision
WorkingDirectory=/opt/provisioning

Environment="PATH=/opt/provisioning/.venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PYTHONUNBUFFERED=1"

ExecStart=/opt/provisioning/.venv/bin/uvicorn main:app \
    --host 127.0.0.1 \
    --port 8080 \
    --workers 1 \
    --log-level info

Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=provisioning-server

[Install]
WantedBy=multi-user.target
SVC

# -- cloudflared systemd service (for provision.mccollumtechnology.com) -------
info "Creating cloudflared.service..."
cat > /etc/systemd/system/cloudflared-provisioning.service << 'SVC'
[Unit]
Description=Cloudflare Tunnel — provision.mccollumtechnology.com
After=network-online.target provisioning-server.service
Wants=network-online.target
ConditionPathExists=/opt/provisioning/.tunnel-token

[Service]
Type=simple
User=provision
Group=provision

# Wait for provisioning server to be healthy before opening the tunnel
ExecStartPre=/bin/bash -c 'for i in $(seq 1 30); do curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1 && exit 0; sleep 2; done; exit 1'
ExecStart=/bin/bash -c 'exec /usr/bin/cloudflared tunnel run --token "$(cat /opt/provisioning/.tunnel-token)"'

Restart=on-failure
RestartSec=30
StartLimitIntervalSec=600
StartLimitBurst=3

StandardOutput=journal
StandardError=journal
SyslogIdentifier=cloudflared-provisioning

[Install]
WantedBy=multi-user.target
SVC

# -- nginx reverse proxy (optional, for TLS termination if not using CF) -------
info "Configuring nginx..."
cat > /etc/nginx/sites-available/provisioning << 'NGINX'
server {
    listen 8080;
    server_name _;

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 60s;
    }
}
NGINX
# Don't enable nginx by default — cloudflared handles external routing.
# Enable it manually if you need direct HTTPS via Let's Encrypt.

# -- HTTP setup wizard service (runs on port 80 until .configured exists) ------
info "Creating provisioning-setup-http.service..."
cat > /etc/systemd/system/provisioning-setup-http.service << 'SVC'
[Unit]
Description=Dashboard Provisioning Server — HTTP Setup Wizard
After=network-online.target
Wants=network-online.target
ConditionPathExists=!/opt/provisioning/.configured

[Service]
Type=simple
User=root
WorkingDirectory=/opt/provisioning
Environment="APP_DIR=/opt/provisioning"
Environment="PATH=/opt/provisioning/.venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/opt/provisioning/.venv/bin/python3 /opt/provisioning/mac-mini/setup-server.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=provisioning-setup

[Install]
WantedBy=multi-user.target
SVC

# -- Disable nginx default site so setup wizard can own port 80 ---------------
info "Disabling nginx default site..."
rm -f /etc/nginx/sites-enabled/default
# Ubuntu 24.04 nginx package enables itself in /lib/systemd/system/multi-user.target.wants/
rm -f /lib/systemd/system/multi-user.target.wants/nginx.service
rm -f /etc/systemd/system/multi-user.target.wants/nginx.service

# -- Copy setup-server.py into place ------------------------------------------
info "Installing setup-server.py..."
mkdir -p "$APP_DIR/mac-mini"
cp /usr/local/bin/provisioning-setup-server "$APP_DIR/mac-mini/setup-server.py" 2>/dev/null || true

# -- Enable services (via symlinks — safe in chroot without running systemd) ---
info "Enabling services..."
mkdir -p /etc/systemd/system/multi-user.target.wants
ln -sf /etc/systemd/system/provisioning-setup-http.service \
       /etc/systemd/system/multi-user.target.wants/provisioning-setup-http.service
ln -sf /etc/systemd/system/provisioning-server.service \
       /etc/systemd/system/multi-user.target.wants/provisioning-server.service
ln -sf /etc/systemd/system/cloudflared-provisioning.service \
       /etc/systemd/system/multi-user.target.wants/cloudflared-provisioning.service

# -- Ownership (after all service files written so abort here doesn't matter) --
info "Setting ownership..."
chown -R "$PROVISION_USER:$PROVISION_USER" "$APP_DIR" 2>/dev/null || \
    find "$APP_DIR" -not -path '*/.venv/*' -not -name '.venv' \
        -exec chown "$PROVISION_USER:$PROVISION_USER" {} + 2>/dev/null || true

info "install-server.sh complete."
