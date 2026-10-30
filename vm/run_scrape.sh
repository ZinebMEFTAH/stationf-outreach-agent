#!/bin/bash
# Scrape Station F job board + enrich contacts
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

# ⚠ PILOTE ÉTEINT = PAS DE SCRAPE (2026-10-30). Ce job ne nourrit QUE le démarchage automatique,
#   qu'elle a arrêté le 2026-10-08 — et il dépense son quota Claude (Opus, deux soirs par semaine)
#   et le quota Hunter (l'enrichissement des contacts), celui-là même dont son bouton « Prévenir
#   l'employeur » a besoin. Il ne tournait plus depuis un mois parce que preflight échouait ;
#   réparer preflight l'aurait relancé en silence. Il reprend tout seul si OUTREACH_AUTOPILOT=1.
if [ "$(python -c 'import config; print(int(config.AUTOPILOT))' 2>/dev/null)" != "1" ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] pilote automatique éteint — scrape non lancé (il ne sert que le démarchage)"
  echo "$TODAY" > "$STAMP"
  exit 0
fi

bash "$DIR/vm/health_check.sh" "scrape" "$DIR/logs/scrape.log" "$STAMP" || true

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] run_scrape.sh start"

# Robust sync (LOUD): pull latest CODE from origin, keeping our data files on conflict,
# recovering a detached HEAD, and ALERTING on any fetch/merge failure instead of failing
# silently (the recurring outage: an expired GitHub credential stranded the VM unnoticed).
# See vm/git_sync.sh. -X ours only affects conflicting hunks; code the VM never edits updates.
source "$DIR/vm/git_sync.sh"
sync_pull || true   # alerted internally; continue on stale code (SMTP work is independent of git)

source "$DIR/vm/preflight_gate.sh"
preflight_gate "scrape" "logs/scrape.log" || exit 1

SKILL=$(sed "s|/path/to/stationf-agent|$DIR|g" .claude/commands/scrape.md)

claude --dangerously-skip-permissions --model opus --print \
  "$SKILL find-contacts" \
  2>&1 | tee -a logs/scrape.log

sync_push "scrape: $(date -u +%Y-%m-%d)" contacts.xlsx cache/ || true  # alerts on push failure

echo "$TODAY" > "$STAMP"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] run_scrape.sh done"
