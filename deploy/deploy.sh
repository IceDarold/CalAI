#!/usr/bin/env bash
# CalAI deploy script — run on server after git pull or via GH Actions
set -euo pipefail

APP_DIR="/root/calai"
VENV_DIR="$APP_DIR/venv"

echo "=== CalAI Deploy ==="
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting deployment..."

cd "$APP_DIR"

echo "[1/5] Pulling latest code..."
git pull origin main

echo "[1.5/5] Ensuring data directories..."
mkdir -p "$APP_DIR/data/photos"

echo "[2/5] Installing dependencies..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install -r requirements.txt

echo "[3/5] Copying systemd service..."
cp deploy/calai-bot.service /etc/systemd/system/calai-bot.service
systemctl daemon-reload

echo "[4/5] Restarting service..."
systemctl enable calai-bot.service
systemctl restart calai-bot.service

echo "=== Deploy complete ==="
systemctl status calai-bot.service --no-pager -l || true
