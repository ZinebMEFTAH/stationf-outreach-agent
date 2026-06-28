#!/bin/bash
# One-time setup for the cloud VM (Google Cloud Compute Engine e2-micro, Ubuntu 22.04).
# Arch-agnostic — works on the GCP x86 free tier or any Ubuntu host.
# Run from the repo root after cloning: bash vm/setup.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

echo "=== StationF Agent VM Setup ==="
echo "Repo: $REPO_DIR"
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────
echo "--- [1/7] Installing system packages ---"
sudo apt-get update -qq
sudo apt-get install -y -qq \
  python3-venv python3-dev python3-pip \
  git curl wget ca-certificates gnupg \
  libnss3 libatk-bridge2.0-0 libatk1.0-0 \
  libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
  libxdamage1 libxfixes3 libxrandr2 libgbm1 \
  libasound2 libpangocairo-1.0-0 libpango-1.0-0 \
  fonts-liberation libx11-xcb1 libxcb-dri3-0 xvfb 2>/dev/null || true

# ── 2. Node.js 20 LTS ────────────────────────────────────────────────────────
if ! command -v node &>/dev/null || [[ "$(node -e 'process.stdout.write(process.version.split(".")[0].slice(1))')" -lt 18 ]]; then
  echo "--- [2/7] Installing Node.js 20 ---"
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - 2>/dev/null
  sudo apt-get install -y -qq nodejs
else
  echo "--- [2/7] Node.js already installed: $(node --version) ---"
fi

# ── 3. Claude Code CLI ────────────────────────────────────────────────────────
echo "--- [3/7] Installing Claude Code CLI ---"
sudo npm install -g @anthropic-ai/claude-code --silent

# ── 4. Python venv + dependencies ────────────────────────────────────────────
echo "--- [4/7] Setting up Python venv ---"
python3 -m venv venv
source venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

# ── 5. Playwright Chromium ───────────────────────────────────────────────────
echo "--- [5/7] Installing Playwright Chromium ---"
playwright install chromium --with-deps 2>/dev/null || \
  python -m playwright install chromium --with-deps

# ── 6. Directories ───────────────────────────────────────────────────────────
echo "--- [6/7] Creating directories ---"
mkdir -p logs drafts cache

# ── 7. .env template ─────────────────────────────────────────────────────────
echo "--- [7/7] Writing .env template ---"
if [ ! -f .env ]; then
  cat > .env << 'ENVEOF'
EMAIL_ADDRESS=
EMAIL_APP_PASSWORD=
FROM_NAME=Zineb Meftah
INTERNAL_ALERT_EMAIL=you@example.com
IMAP_SERVER=imap.gmail.com
IMAP_PORT=993
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
ENVEOF
  echo "  Created .env — fill in credentials before first run."
else
  echo "  .env already exists — skipping."
fi

# ── Git config ────────────────────────────────────────────────────────────────
git config user.name  "Zineb Outreach Agent"
git config user.email "agent@stationf-agent"
# 'ours' merge driver so .gitattributes `merge=ours` keeps the VM's data files on conflict.
git config merge.ours.driver true

# ── Make wrapper scripts executable ──────────────────────────────────────────
chmod +x "$REPO_DIR/vm"/run_*.sh

# ── Install crontab ───────────────────────────────────────────────────────────
# Generate crontab with real paths substituted
sed "s|__REPO_DIR__|$REPO_DIR|g" "$REPO_DIR/vm/crontab.txt" > /tmp/stationf_crontab
crontab /tmp/stationf_crontab
rm /tmp/stationf_crontab

# ── Verify the install with the pre-flight self-test ─────────────────────────
echo ""
echo "--- Running pre-flight self-test to verify the install ---"
python preflight.py || echo "  ⚠  preflight reported issues — review above before relying on cron runs"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║           Setup complete — 2 steps remain            ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║                                                      ║"
echo "║  1. Fill in your credentials:                        ║"
echo "║     nano $REPO_DIR/.env"
echo "║     (EMAIL_ADDRESS, EMAIL_APP_PASSWORD)              ║"
echo "║                                                      ║"
echo "║  2. Authenticate Claude with your subscription:      ║"
echo "║     claude auth login                                ║"
echo "║     (Copy the URL → open in browser → sign in)       ║"
echo "║                                                      ║"
echo "║  Then verify with a dry run:                         ║"
echo "║     bash vm/run_agent.sh --dry-run                   ║"
echo "║                                                      ║"
echo "║  Crontab installed:                                  ║"
echo "║     crontab -l                                       ║"
echo "╚══════════════════════════════════════════════════════╝"
