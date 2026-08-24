#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="wilma-telegram.service"
SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_PATH="$SERVICE_DIR/$SERVICE_NAME"

mkdir -p "$SERVICE_DIR"
sed "s|@PROJECT_DIR@|$PROJECT_DIR|g" "$PROJECT_DIR/systemd/wilma-telegram.service" > "$SERVICE_PATH"

systemctl --user daemon-reload
systemctl --user enable --now "$SERVICE_NAME"

echo "Service installed and started."
echo "  status: systemctl --user status $SERVICE_NAME"
echo "  logs:   journalctl --user -u $SERVICE_NAME -f"
echo
echo "To start at boot without logging in, run once:"
echo "  loginctl enable-linger $USER"
