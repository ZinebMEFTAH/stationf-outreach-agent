#!/bin/bash
# One-command VM deployment from your Mac.
# Run this AFTER creating the cloud VM (Google Cloud Compute Engine) and getting its IP.
#
# Usage:
#   bash vm/deploy.sh ubuntu@<VM_PUBLIC_IP>
#
# What it does automatically:
#   1. Copies your local .env (credentials) to the VM
#   2. Clones the GitHub repo on the VM
#   3. Runs vm/setup.sh (installs Node.js, Python, Playwright, claude CLI, crontab)
#
# The only manual step left: run "claude auth login" on the VM once.

set -euo pipefail

SSH_TARGET="${1:?Error: missing VM address.  Usage: bash vm/deploy.sh ubuntu@<IP>}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_URL="https://github.com/ZinebMEFTAH/stationf-agent.git"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║       StationF Agent — VM Deployment                 ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  Target : $SSH_TARGET"
echo "║  Repo   : $REPO_URL"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── Pre-flight checks ────────────────────────────────────────────────────────
if [ ! -f "$REPO_DIR/.env" ]; then
  echo "ERROR: .env not found at $REPO_DIR/.env"
  echo "Create it with your credentials before deploying."
  exit 1
fi

# Check SSH connectivity
echo "--- Checking SSH connection ---"
if ! ssh -o ConnectTimeout=10 -o BatchMode=yes "$SSH_TARGET" true 2>/dev/null; then
  echo ""
  echo "ERROR: Cannot connect to $SSH_TARGET"
  echo ""
  echo "Make sure:"
  echo "  1. The VM is running (check the Google Cloud console)"
  echo "  2. Your SSH key is authorized on the VM"
  echo "  3. Port 22 is open in the VM's security list"
  echo ""
  echo "If you haven't added your SSH key yet, run:"
  echo "  cat ~/.ssh/id_ed25519.pub   (or id_rsa.pub)"
  echo "  # Then add it on the instance: Google Cloud > Compute Engine > VM > Edit > SSH Keys"
  exit 1
fi
echo "  SSH OK"

# ── Copy credentials ─────────────────────────────────────────────────────────
echo ""
echo "--- Copying credentials ---"
scp -q "$REPO_DIR/.env" "$SSH_TARGET:/tmp/stationf_agent_env"
echo "  .env copied"

# ── Remote setup ─────────────────────────────────────────────────────────────
echo ""
echo "--- Running remote setup (takes ~5 minutes) ---"
ssh "$SSH_TARGET" REPO_URL="$REPO_URL" bash << 'REMOTE'
set -euo pipefail

REPO="$HOME/stationf_agent"

# Clone or update repo
if [ ! -d "$REPO" ]; then
  echo "  Cloning $REPO_URL ..."
  git clone "$REPO_URL" "$REPO"
else
  echo "  Repo already exists — pulling latest ..."
  git -C "$REPO" pull -q
fi

# Move .env into place (overwrite if already there)
mv /tmp/stationf_agent_env "$REPO/.env"
echo "  Credentials installed"

# Run the full setup
bash "$REPO/vm/setup.sh"
REMOTE

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   Deployment complete — 1 step remaining             ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║                                                      ║"
echo "║  SSH into your VM and authenticate Claude once:      ║"
echo "║                                                      ║"
echo "║    ssh $SSH_TARGET"
echo "║    claude auth login"
echo "║                                                      ║"
echo "║  Copy the URL shown → open in browser → sign in      ║"
echo "║  to your claude.ai account.                          ║"
echo "║                                                      ║"
echo "║  Then test:                                          ║"
echo "║    bash ~/stationf_agent/vm/run_agent.sh --dry-run   ║"
echo "║                                                      ║"
echo "║  Crontab is already installed — runs Mon–Fri:        ║"
echo "║    09:00 Paris — outreach agent                      ║"
echo "║    12:00 Paris — inbox scan                          ║"
echo "║    14:30 Paris — scrape new jobs                     ║"
echo "║    20:00 Paris — speculative pitches                 ║"
echo "╚══════════════════════════════════════════════════════╝"
