#!/usr/bin/env bash
# Bootstrap script — run once on a fresh Raspberry Pi to restore the home server setup.
# Idempotent: safe to re-run on an existing installation.
set -euo pipefail

BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${BLUE}[setup]${NC} $*"; }
success() { echo -e "${GREEN}[setup]${NC} $*"; }
warn()    { echo -e "${YELLOW}[setup]${NC} $*"; }

# ── System packages ────────────────────────────────────────────────────────────
info "Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y python3-venv git logrotate

# ── Clone repos ────────────────────────────────────────────────────────────────
REPOS=(
    "git@github.com:s-jac/homeserver.git:$HOME/homeserver"
)

for entry in "${REPOS[@]}"; do
    url="${entry%%:*}"
    dir="${entry##*:}"
    if [ -d "$dir/.git" ]; then
        info "Already cloned: $dir — skipping"
    else
        info "Cloning $url → $dir"
        git clone "$url" "$dir"
    fi
done

# ── Shared virtualenv ──────────────────────────────────────────────────────────
if [ ! -d "$HOME/venv" ]; then
    info "Creating shared venv at ~/venv..."
    python3 -m venv "$HOME/venv"
fi

info "Installing Python dependencies into ~/venv..."
"$HOME/venv/bin/pip" install --quiet --upgrade pip
"$HOME/venv/bin/pip" install --quiet \
    Flask gunicorn PyJWT pytz requests aiohttp

# ── Log directory ──────────────────────────────────────────────────────────────
mkdir -p "$HOME/homeserver/logs"

# ── Systemd service ────────────────────────────────────────────────────────────
info "Installing homeserver systemd service..."
sudo cp "$HOME/homeserver/homeserver.service" /etc/systemd/system/homeserver.service
sudo systemctl daemon-reload
sudo systemctl enable homeserver
sudo systemctl start homeserver || true
success "homeserver service enabled and started"

# ── Logrotate ──────────────────────────────────────────────────────────────────
info "Installing logrotate config..."
sudo cp "$HOME/homeserver/logrotate.conf" /etc/logrotate.d/homeserver

# ── Crontab ────────────────────────────────────────────────────────────────────
info "Configuring crontab..."
CRON_SAT='30 0 * * SAT $HOME/venv/bin/python $HOME/homeserver/scripts/gym.py >> $HOME/homeserver/logs/gym.log 2>&1'
CRON_MON='30 0 * * MON $HOME/venv/bin/python $HOME/homeserver/scripts/gym.py >> $HOME/homeserver/logs/gym.log 2>&1'
(
    crontab -l 2>/dev/null | grep -v 'gym.py' || true
    echo "# Gym class bookings — 3 days before 7am class"
    echo "$CRON_SAT"
    echo "$CRON_MON"
) | crontab -
success "Crontab configured"

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
success "Installation complete!"
warn "MANUAL STEPS REQUIRED — create config.json from the sample and fill in real credentials:"
warn "  cp ~/homeserver/config/config.sample.json ~/homeserver/config/config.json"
warn "  chmod 600 ~/homeserver/config/config.json"
warn "  # Edit ~/homeserver/config/config.json with auth, gym, email, and campsite details"
warn ""
warn "Then copy jobs.json from a backup, or create it from scratch."
warn "Refer to ~/homeserver/AGENTS.md for full setup details."
