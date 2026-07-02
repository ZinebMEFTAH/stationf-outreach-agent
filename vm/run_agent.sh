#!/bin/bash
# Daily outreach agent — runs daily-agent.md skill with path substitution
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
source venv/bin/activate

# claude CLI auth for headless cron: export the subscription OAuth token from .env (created
# via `claude setup-token`) so `claude --print` can authenticate — cron has no interactive
# login. Read at runtime; no secret is hardcoded (safe for the public mirror).
_OAT="$(grep -E '^CLAUDE_CODE_OAUTH_TOKEN=' "$DIR/.env" | cut -d= -f2- || true)"
if [ -n "$_OAT" ]; then export CLAUDE_CODE_OAUTH_TOKEN="$_OAT"; fi

# Weekend guard (launchd RunAtLoad fires on any boot — skip Sat/Sun)
DOW=$(date +%u)   # 1=Mon … 7=Sun
if [ "$DOW" -ge 6 ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Weekend — skipping run_agent"
  exit 0
fi

# Catch-up dedup: skip if already completed today (prevents double-run after reboot)
STAMP="$DIR/cache/.run_agent_ran"
TODAY=$(date +%Y-%m-%d)
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$TODAY" ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Already ran today — skipping"
  exit 0
fi

mkdir -p logs cache
bash "$DIR/vm/health_check.sh" "agent" "$DIR/logs/agent.log" "$STAMP" || true

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] run_agent.sh start"

# Robust sync: pull latest CODE from origin, but ALWAYS keep our own data files
# (contacts.xlsx/cache/drafts) on conflict, and never drift onto a detached HEAD
# (the June-2026 silent-failure bug). -X ours only affects conflicting hunks, so
# code the VM never edits still updates normally.
git fetch -q origin main 2>/dev/null && git merge -q -X ours origin/main 2>/dev/null || git merge --abort 2>/dev/null || true

source "$DIR/vm/preflight_gate.sh"
preflight_gate "agent" "logs/agent.log" || exit 1

SKILL=$(sed "s|/path/to/stationf-agent|$DIR|g" .claude/commands/daily-agent.md)

if [[ "${1:-}" == "--dry-run" ]]; then
  SKILL="$SKILL

This is a DRY RUN. Generate and save drafts but do NOT call smtp_send.py with --send."
fi

claude --dangerously-skip-permissions --model claude-opus-4-8 --print "$SKILL" \
  2>&1 | tee -a logs/agent.log

git add contacts.xlsx drafts/ cache/ 2>/dev/null || true
if ! git diff --cached --quiet; then
  git commit -m "agent: $(date -u +%Y-%m-%d)"
  git push -q origin main 2>/dev/null || true
fi

echo "$TODAY" > "$STAMP"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] run_agent.sh done"
