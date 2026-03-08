#!/usr/bin/env bash
# Recreate the homeserver environment from scratch.
# Run as: bash setup.sh
set -e

HOMESERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Creating virtualenv…"
python3 -m venv "$HOMESERVER_DIR/venv"

echo "==> Installing Python dependencies…"
"$HOMESERVER_DIR/venv/bin/pip" install --upgrade pip -q
"$HOMESERVER_DIR/venv/bin/pip" install -r "$HOMESERVER_DIR/requirements.txt"

echo "==> Installing Playwright Chromium…"
"$HOMESERVER_DIR/venv/bin/playwright" install --with-deps chromium

echo "==> Creating config from example (if not already present)…"
if [ ! -f "$HOMESERVER_DIR/config/settings.json" ]; then
    cp "$HOMESERVER_DIR/config/settings.example.json" "$HOMESERVER_DIR/config/settings.json"
    chmod 600 "$HOMESERVER_DIR/config/settings.json"
    echo "    Created config/settings.json — fill in your credentials."
fi
chmod 600 "$HOMESERVER_DIR/config/jobs.json"
chmod 700 "$HOMESERVER_DIR/config"
mkdir -p "$HOMESERVER_DIR/logs"

echo "==> Installing systemd service…"
sudo cp "$HOMESERVER_DIR/homeserver.service" /etc/systemd/system/homeserver.service
sudo systemctl daemon-reload
sudo systemctl enable homeserver
sudo systemctl restart homeserver

echo ""
echo "Done. Next steps:"
echo "  1. Edit config/settings.json with your credentials"
echo "  2. Add cron jobs: crontab -e"
echo "     30 0 * * SAT /home/gmac/homeserver/venv/bin/python /home/gmac/homeserver/scripts/gym_book.py >> /home/gmac/homeserver/logs/gym_book.log 2>&1"
echo "     30 0 * * MON /home/gmac/homeserver/venv/bin/python /home/gmac/homeserver/scripts/gym_book.py >> /home/gmac/homeserver/logs/gym_book.log 2>&1"
