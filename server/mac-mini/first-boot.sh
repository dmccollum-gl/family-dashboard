#!/usr/bin/env bash
# first-boot.sh
# Interactive setup wizard that runs on the Mac Mini's first boot.
# Collects all API credentials, writes /opt/provisioning/.env,
# creates the Mac Mini's own Cloudflare tunnel for provision.mccollumtechnology.com,
# and starts both systemd services.
#
# Runs once only — guarded by ConditionPathExists=!/opt/provisioning/.configured
set -euo pipefail

APP_DIR=/opt/provisioning
VENV="$APP_DIR/.venv"
ENV_FILE="$APP_DIR/.env"
TOKEN_FILE="$APP_DIR/.tunnel-token"
PROVISION_USER=provision

# ── Colors ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'
RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'

header() {
  echo ""
  echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════════╗${NC}"
  echo -e "${BOLD}${CYAN}║     Dashboard Provisioning Server — First Boot       ║${NC}"
  echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════╝${NC}"
  echo ""
}

section() { echo -e "\n${BOLD}${GREEN}── $* ──${NC}"; }
info()     { echo -e "${GREEN}[setup]${NC} $*"; }
warn()     { echo -e "${YELLOW}[setup]${NC} $*"; }
error()    { echo -e "${RED}[setup]${NC} $*" >&2; }
ask()      { echo -e "${CYAN}$*${NC}"; }

# ── Prompt helper: read a value, with optional default ────────────────────────
prompt() {
  local var_name="$1"
  local prompt_text="$2"
  local default="${3:-}"
  local value=""

  while true; do
    if [[ -n "$default" ]]; then
      ask "  $prompt_text [$default]: "
    else
      ask "  $prompt_text: "
    fi
    read -r value
    value="${value:-$default}"
    if [[ -n "$value" ]]; then
      eval "$var_name='$value'"
      return
    fi
    warn "  This field is required."
  done
}

prompt_secret() {
  local var_name="$1"
  local prompt_text="$2"
  local value=""

  while true; do
    ask "  $prompt_text: "
    read -rs value
    echo ""
    if [[ -n "$value" ]]; then
      eval "$var_name='$value'"
      return
    fi
    warn "  This field is required."
  done
}

# ── Wait for network ──────────────────────────────────────────────────────────
wait_network() {
  echo -n "  Waiting for network"
  for i in $(seq 1 30); do
    if curl -sf --max-time 3 https://1.1.1.1 >/dev/null 2>&1; then
      echo " ✓"
      return
    fi
    echo -n "."
    sleep 2
  done
  echo ""
  warn "Network not reachable after 60s. Check ethernet connection."
  warn "You can re-run this wizard with: sudo /usr/local/bin/provisioning-setup"
}

# ── Generate Fernet key ───────────────────────────────────────────────────────
generate_fernet_key() {
  "$VENV/bin/python3" -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
}

# ── Create Cloudflare tunnel for this Mac Mini via API ────────────────────────
create_mac_tunnel() {
  local api_token="$1"
  local account_id="$2"
  local zone_id="$3"
  local base_domain="$4"

  local tunnel_name="provisioning-server"
  local tunnel_hostname="provision.$base_domain"

  info "Creating Cloudflare tunnel '$tunnel_name'..."

  # Generate a random 32-byte tunnel secret
  local secret
  secret=$(python3 -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())")

  # Create the tunnel
  local response
  response=$(curl -sf -X POST \
    "https://api.cloudflare.com/client/v4/accounts/${account_id}/cfd_tunnel" \
    -H "Authorization: Bearer ${api_token}" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${tunnel_name}\",\"tunnel_secret\":\"${secret}\"}" 2>&1)

  local success
  success=$(echo "$response" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('success','false'))" 2>/dev/null || echo "false")

  if [[ "$success" != "True" && "$success" != "true" ]]; then
    error "Failed to create tunnel: $(echo "$response" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('errors',[{}])[0].get('message','unknown'))" 2>/dev/null)"
    return 1
  fi

  local tunnel_id token
  tunnel_id=$(echo "$response" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['id'])")
  token=$(echo "$response" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['token'])")

  info "Tunnel created: $tunnel_id"

  # Configure ingress rules: provision.mccollumtechnology.com → http://127.0.0.1:8080
  info "Configuring tunnel ingress..."
  curl -sf -X PUT \
    "https://api.cloudflare.com/client/v4/accounts/${account_id}/cfd_tunnel/${tunnel_id}/configurations" \
    -H "Authorization: Bearer ${api_token}" \
    -H "Content-Type: application/json" \
    -d "{\"config\":{\"ingress\":[{\"hostname\":\"${tunnel_hostname}\",\"service\":\"http://127.0.0.1:8080\"},{\"service\":\"http_status:404\"}]}}" \
    >/dev/null

  # Create DNS CNAME: provision.mccollumtechnology.com → {id}.cfargotunnel.com
  info "Creating DNS record: $tunnel_hostname → ${tunnel_id}.cfargotunnel.com..."
  curl -sf -X POST \
    "https://api.cloudflare.com/client/v4/zones/${zone_id}/dns_records" \
    -H "Authorization: Bearer ${api_token}" \
    -H "Content-Type: application/json" \
    -d "{\"type\":\"CNAME\",\"name\":\"${tunnel_hostname}\",\"content\":\"${tunnel_id}.cfargotunnel.com\",\"proxied\":true,\"ttl\":1}" \
    >/dev/null

  # Save the token so cloudflared-provisioning.service can read it
  echo -n "$token" > "$TOKEN_FILE"
  chown "$PROVISION_USER:$PROVISION_USER" "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"

  info "Tunnel token saved to $TOKEN_FILE"
  echo "$tunnel_id"
}

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

clear
header

echo "  This wizard will collect your API credentials and start the"
echo "  provisioning server. It runs once and will not appear again."
echo ""
echo "  You will need:"
echo "    • Cloudflare API token (Tunnel:Edit + DNS:Edit permissions)"
echo "    • Cloudflare Account ID and Zone ID"
echo "    • Google Cloud Project ID and OAuth2 Client ID"
echo "    • Path or content of a Google service account JSON key file"
echo ""
read -rp "  Press ENTER to begin, or Ctrl-C to exit and run later... "

wait_network

# ── Cloudflare ────────────────────────────────────────────────────────────────
section "Cloudflare"
echo "  Found at: dash.cloudflare.com"
echo ""
prompt_secret CF_API_TOKEN    "Cloudflare API Token"
prompt        CF_ACCOUNT_ID   "Cloudflare Account ID"
prompt        CF_ZONE_ID      "Cloudflare Zone ID for mccollumtechnology.com"
prompt        BASE_DOMAIN     "Base domain" "mccollumtechnology.com"

# ── Google Cloud ──────────────────────────────────────────────────────────────
section "Google Cloud"
echo "  Found at: console.cloud.google.com → APIs & Services → Credentials"
echo ""
prompt GCP_PROJECT_ID   "Google Cloud Project ID"
prompt OAUTH_CLIENT_ID  "OAuth2 Client ID (ending in .apps.googleusercontent.com)"

echo ""
echo "  Service account key: enter the FULL PATH to your downloaded JSON key file."
echo "  Example: /home/provision/my-service-account.json"
echo ""
while true; do
  prompt SA_JSON_PATH "Service account key file path"
  if [[ -f "$SA_JSON_PATH" ]]; then
    # Read the file content and inline it as a single-line JSON string
    SA_JSON=$(python3 -c "import json; print(json.dumps(json.load(open('$SA_JSON_PATH'))))")
    info "  Service account loaded: $(python3 -c "import json; d=json.load(open('$SA_JSON_PATH')); print(d.get('client_email','?'))")"
    break
  else
    warn "  File not found: $SA_JSON_PATH"
  fi
done

# ── Admin key ─────────────────────────────────────────────────────────────────
section "Admin API Key"
echo "  This protects the /api/provision/activation-codes and"
echo "  /api/provision/tunnels admin endpoints."
echo ""
DEFAULT_ADMIN_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
echo "  Auto-generated key (press ENTER to accept, or type your own):"
prompt ADMIN_API_KEY "Admin API Key" "$DEFAULT_ADMIN_KEY"

# ── Generate encryption key ───────────────────────────────────────────────────
section "Encryption Key"
info "Generating Fernet key for tunnel token encryption at rest..."
FERNET_KEY=$(generate_fernet_key)
info "Generated: ${FERNET_KEY:0:10}..."

# ── Create Mac Mini's own Cloudflare tunnel ───────────────────────────────────
section "Creating Mac Mini Tunnel"
echo "  Creating a Cloudflare tunnel so Pi devices can reach this server"
echo "  at https://provision.$BASE_DOMAIN from anywhere on the internet."
echo ""

MAC_TUNNEL_ID=$(create_mac_tunnel "$CF_API_TOKEN" "$CF_ACCOUNT_ID" "$CF_ZONE_ID" "$BASE_DOMAIN") || {
  error "Tunnel creation failed. Check your API token and try again."
  warn "You can re-run: sudo /usr/local/bin/provisioning-setup"
  exit 1
}

# ── Write .env ────────────────────────────────────────────────────────────────
section "Writing Configuration"
info "Writing $ENV_FILE..."

cat > "$ENV_FILE" << EOF
# Generated by first-boot wizard on $(date)
CLOUDFLARE_API_TOKEN=${CF_API_TOKEN}
CLOUDFLARE_ACCOUNT_ID=${CF_ACCOUNT_ID}
CLOUDFLARE_ZONE_ID=${CF_ZONE_ID}
BASE_DOMAIN=${BASE_DOMAIN}
GOOGLE_CLOUD_PROJECT_ID=${GCP_PROJECT_ID}
GOOGLE_OAUTH_CLIENT_ID=${OAUTH_CLIENT_ID}
GOOGLE_SERVICE_ACCOUNT_JSON=${SA_JSON}
ADMIN_API_KEY=${ADMIN_API_KEY}
DATABASE_URL=sqlite:///./server.db
FERNET_KEY=${FERNET_KEY}
EOF

chown "$PROVISION_USER:$PROVISION_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"
info ".env written."

# ── Start services ────────────────────────────────────────────────────────────
section "Starting Services"
systemctl daemon-reload
systemctl start provisioning-server.service
info "Waiting for provisioning server to be ready..."
for i in $(seq 1 30); do
  sleep 2
  if curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1; then
    info "Provisioning server is up!"
    break
  fi
  echo -n "."
done
echo ""

systemctl start cloudflared-provisioning.service
info "cloudflared-provisioning started."

# ── Mark as configured ────────────────────────────────────────────────────────
touch "$APP_DIR/.configured"
chown "$PROVISION_USER:$PROVISION_USER" "$APP_DIR/.configured"

# ── Change default password ───────────────────────────────────────────────────
section "Change Login Password"
echo "  The default login password is 'dashboard'. Set a new one now."
echo ""
passwd provision

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║              Setup Complete!                         ║${NC}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Provisioning server:${NC}  https://provision.$BASE_DOMAIN"
echo -e "  ${BOLD}Health check:${NC}         https://provision.$BASE_DOMAIN/health"
echo ""
echo -e "  ${BOLD}Admin API key:${NC}        $ADMIN_API_KEY"
echo ""
echo "  Generate activation codes:"
echo "    curl -X POST https://provision.$BASE_DOMAIN/api/provision/activation-codes \\"
echo "      -H \"X-Admin-Key: $ADMIN_API_KEY\" \\"
echo "      -H \"Content-Type: application/json\" \\"
echo "      -d '{\"count\": 10}'"
echo ""
echo "  Set on each Pi:"
echo "    PROVISIONING_SERVER_URL=https://provision.$BASE_DOMAIN"
echo ""
echo -e "  ${YELLOW}The tunnel may take 1-2 minutes to fully propagate DNS.${NC}"
echo ""

# ── Disable this service (runs only once) ─────────────────────────────────────
systemctl disable provisioning-setup.service
