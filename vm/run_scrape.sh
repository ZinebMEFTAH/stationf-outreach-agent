#!/bin/bash
# Scrape Station F job board + enrich contacts
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
source venv/bin/activate

DOW=$(date +%u)
if [ "$DOW" -ge 6 ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Weekend — skipping run_scrape"
  exit 0
fi

STAMP="$DIR/cache/.run_scrape_ran"
TODAY=$(date +%Y-%m-%d)
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$TODAY" ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Already ran today — skipping"
  exit 0
fi

mkdir -p logs cache
bash "$DIR/vm/health_check.sh" "scrape" "$DIR/logs/scrape.log" "$STAMP" || true

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] run_scrape.sh start"

git pull --rebase -q origin main 2>/dev/null || true

source "$DIR/vm/preflight_gate.sh"
preflight_gate "scrape" "logs/scrape.log" || exit 1

SKILL=$(sed "s|/path/to/stationf-agent|$DIR|g" .claude/commands/scrape.md)

claude --dangerously-skip-permissions --model claude-opus-4-8 --print \
  "$SKILL find-contacts" \
  2>&1 | tee -a logs/scrape.log

git add contacts.xlsx cache/ 2>/dev/null || true
if ! git diff --cached --quiet; then
  git commit -m "scrape: $(date -u +%Y-%m-%d)"
  git push -q origin main 2>/dev/null || true
fi

echo "$TODAY" > "$STAMP"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] run_scrape.sh done"
