#!/usr/bin/env bash
# Start the dashboard backend + frontend for local development.
# Usage: bash start-dev.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV="$ROOT/.venv"

# ── Backend setup ─────────────────────────────────────────────────────────────
if [ ! -d "$VENV" ]; then
  echo "Creating Python venv..."
  python3 -m venv "$VENV"
fi

echo "Installing/updating backend dependencies..."
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$BACKEND/requirements.txt"

# ── Frontend setup ────────────────────────────────────────────────────────────
if [ ! -d "$FRONTEND/node_modules" ]; then
  echo "Installing frontend dependencies..."
  npm --prefix "$FRONTEND" install
fi

# ── Start backend ─────────────────────────────────────────────────────────────
echo "Starting backend on http://localhost:8001 ..."
cd "$BACKEND"
"$VENV/bin/uvicorn" main:app --reload --port 8001 &
BACKEND_PID=$!

cleanup() {
  echo ""
  echo "Stopping backend (PID $BACKEND_PID)..."
  kill "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Give uvicorn a moment to bind before Vite starts
sleep 1

# ── Start frontend (foreground) ───────────────────────────────────────────────
echo "Starting frontend on http://localhost:5173 ..."
npm --prefix "$FRONTEND" run dev
