#!/bin/bash
# Evaluate 5 new Station F companies for speculative pitches
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
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Weekend — skipping run_speculative"
  exit 0
fi

STAMP="$DIR/cache/.run_speculative_ran"
TODAY=$(date +%Y-%m-%d)
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$TODAY" ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Already ran today — skipping"
  exit 0
fi

mkdir -p logs cache
bash "$DIR/vm/health_check.sh" "speculative" "$DIR/logs/speculative.log" "$STAMP" || true

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] run_speculative.sh start"

# Robust sync (LOUD): pull latest CODE from origin, keeping our data files on conflict,
# recovering a detached HEAD, and ALERTING on any fetch/merge failure instead of failing
# silently (the recurring outage: an expired GitHub credential stranded the VM unnoticed).
# See vm/git_sync.sh. -X ours only affects conflicting hunks; code the VM never edits updates.
source "$DIR/vm/git_sync.sh"
sync_pull || true   # alerted internally; continue on stale code (SMTP work is independent of git)

source "$DIR/vm/preflight_gate.sh"
preflight_gate "speculative" "logs/speculative.log" || exit 1

SKILL=$(sed "s|/path/to/stationf-agent|$DIR|g" .claude/commands/speculative.md)

claude --dangerously-skip-permissions --model claude-opus-4-8 --print \
  "$SKILL --batch 5" \
  2>&1 | tee -a logs/speculative.log

sync_push "speculative: batch $(date -u +%Y-%m-%d)" contacts.xlsx cache/speculative_state.json || true  # alerts on push failure

echo "$TODAY" > "$STAMP"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] run_speculative.sh done"
