#!/bin/bash
# Enrich any remaining generic contact@ emails with named decision-makers.
# Runs after scraping (08:30 Paris) so the agent at 09:00 always has real people to email.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
source venv/bin/activate

# claude CLI auth for headless cron: export the subscription OAuth token from .env (created
# via `claude setup-token`) so `claude --print` can authenticate — cron has no interactive
# login. Read at runtime; no secret is hardcoded (safe for the public mirror).
_OAT="$(grep -E '^CLAUDE_CODE_OAUTH_TOKEN=' "$DIR/.env" | cut -d= -f2- || true)"
if [ -n "$_OAT" ]; then export CLAUDE_CODE_OAUTH_TOKEN="$_OAT"; fi

DOW=$(date +%u)
if [ "$DOW" -ge 6 ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Weekend — skipping run_find_contacts"
  exit 0
fi

STAMP="$DIR/cache/.run_find_contacts_ran"
TODAY=$(date +%Y-%m-%d)
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$TODAY" ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Already ran today — skipping"
  exit 0
fi

mkdir -p logs cache
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] run_find_contacts.sh start"

# Robust sync: pull latest CODE from origin, but ALWAYS keep our own data files
# (contacts.xlsx/cache/drafts) on conflict, and never drift onto a detached HEAD
# (the June-2026 silent-failure bug). -X ours only affects conflicting hunks, so
# code the VM never edits still updates normally.
git fetch -q origin main 2>/dev/null && git merge -q -X ours origin/main 2>/dev/null || git merge --abort 2>/dev/null || true

source "$DIR/vm/preflight_gate.sh"
preflight_gate "find_contacts" "logs/find_contacts.log" || exit 1

SKILL=$(sed "s|/path/to/stationf-agent|$DIR|g" .claude/commands/find-contacts.md)

# Daily enrichment volume — single source of truth in config.ENRICH_CAP (fallback 15 if unreadable).
ENRICH_LIMIT="$(python -c 'import config; print(int(config.ENRICH_CAP))' 2>/dev/null || echo 15)"

claude --dangerously-skip-permissions --model claude-opus-4-8 --print \
  "$SKILL

Arguments: --all --limit $ENRICH_LIMIT" \
  2>&1 | tee -a logs/find_contacts.log

git add contacts.xlsx 2>/dev/null || true
if ! git diff --cached --quiet; then
  git commit -m "contacts: enrich $(date -u +%Y-%m-%d)"
  git push -q origin main 2>/dev/null || true
fi

echo "$TODAY" > "$STAMP"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] run_find_contacts.sh done"
