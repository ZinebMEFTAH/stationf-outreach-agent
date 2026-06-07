#!/bin/bash
# Enrich any remaining generic contact@ emails with named decision-makers.
# Runs after scraping (08:30 Paris) so the agent at 09:00 always has real people to email.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
source venv/bin/activate

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

git pull --rebase -q origin main 2>/dev/null || true

source "$DIR/vm/preflight_gate.sh"
preflight_gate "find_contacts" "logs/find_contacts.log" || exit 1

SKILL=$(sed "s|/path/to/stationf-agent|$DIR|g" .claude/commands/find-contacts.md)

claude --dangerously-skip-permissions --model claude-opus-4-8 --print \
  "$SKILL

Arguments: --all --limit 8" \
  2>&1 | tee -a logs/find_contacts.log

git add contacts.xlsx 2>/dev/null || true
if ! git diff --cached --quiet; then
  git commit -m "contacts: enrich $(date -u +%Y-%m-%d)"
  git push -q origin main 2>/dev/null || true
fi

echo "$TODAY" > "$STAMP"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] run_find_contacts.sh done"
