#!/usr/bin/env bash
# Pull latest from all repos, then stage + commit + push any local changes.
# Usage: bash ~/homeserver/update.sh
set -euo pipefail

BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${BLUE}[update]${NC} $*"; }
success() { echo -e "${GREEN}[update]${NC} $*"; }
warn()    { echo -e "${YELLOW}[update]${NC} $*"; }

REPOS=(
    "$HOME/homeserver"
)

TIMESTAMP="$(date '+%Y-%m-%d %H:%M')"

# Set merge as pull strategy in case of divergence
git config --global pull.rebase false

for repo in "${REPOS[@]}"; do
    if [ ! -d "$repo/.git" ]; then
        warn "Skipping $repo — not a git repo"
        continue
    fi

    name="$(basename "$repo")"
    info "── $name ──────────────────────────────────"

    cd "$repo"

    # Pull latest
    info "Pulling..."
    git pull origin main

    # Stage all changes
    if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
        success "Nothing to commit."
        continue
    fi

    git add -A
    git commit -m "Update at: $TIMESTAMP

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
    git push origin main
    success "Pushed."
done

echo ""
success "All repos updated."
