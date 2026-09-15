#!/bin/bash
# NIGHT PREP — the main run. Does all the thinking, SENDS NOTHING: it queues finished mail in
# outbox.py, and dispatch.py transmits it during the day in pure Python.
#
# Runs 23:00 Paris (21:00 UTC) Sun-Thu, preparing the NEXT weekday. Claude here spends from
# Zineb's own subscription quota, so it must stay inside the 22:00-03:00 window: by 08:00 the
# rolling 5-hour allowance has fully elapsed and her working day starts with all of it.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
source venv/bin/activate

# claude CLI auth for headless cron: export the subscription OAuth token from .env (created
# via `claude setup-token`) so `claude --print` can authenticate — cron has no interactive
# login. Read at runtime; no secret is hardcoded (safe for the public mirror).
_OAT="$(grep -E '^CLAUDE_CODE_OAUTH_TOKEN=' "$DIR/.env" | cut -d= -f2- || true)"
if [ -n "$_OAT" ]; then export CLAUDE_CODE_OAUTH_TOKEN="$_OAT"; fi

# Weekend guard. NOTE this one differs from the other runners: night prep fires on SUNDAY
# evening to prepare Monday's sends, so Sunday must be ALLOWED. What we skip is Friday and
# Saturday night, which would prepare mail for a weekend nobody sends on.
DOW=$(date +%u)   # 1=Mon … 7=Sun
if [ "$DOW" = "5" ] || [ "$DOW" = "6" ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Fri/Sat night — no weekday to prepare, skipping"
  exit 0
fi

# Catch-up dedup: skip if already completed today (prevents double-run after reboot)
STAMP="$DIR/cache/.run_night_prep_ran"
TODAY=$(date +%Y-%m-%d)
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$TODAY" ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Already ran today — skipping"
  exit 0
fi

mkdir -p logs cache
bash "$DIR/vm/health_check.sh" "night_prep" "$DIR/logs/agent.log" "$STAMP" || true

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] run_night_prep.sh start"

# Robust sync (LOUD): pull latest CODE from origin, keeping our data files on conflict,
# recovering a detached HEAD, and ALERTING on any fetch/merge failure instead of failing
# silently (the recurring outage: an expired GitHub credential stranded the VM unnoticed).
# See vm/git_sync.sh. -X ours only affects conflicting hunks; code the VM never edits updates.
source "$DIR/vm/git_sync.sh"
sync_pull || true   # alerted internally; continue on stale code (SMTP work is independent of git)

source "$DIR/vm/preflight_gate.sh"
preflight_gate "night_prep" "logs/night_prep.log" || exit 1

SKILL=$(sed "s|/path/to/stationf-agent|$DIR|g" .claude/commands/daily-agent.md)

# QUEUE MODE is the whole point of this runner: draft everything, transmit nothing. The mail
# leaves during the day via dispatch.py, which spends no Claude quota.
SKILL="$SKILL

MODE: --queue. Do the full protocol, but do NOT call smtp_send.py for cold or follow-up mail.
Queue each finished, linted message with outbox.py (see QUEUE MODE at the top of the skill),
spreading send_after across the morning. Internal alerts still send immediately."

if [[ "${1:-}" == "--dry-run" ]]; then
  SKILL="$SKILL

This is a DRY RUN. Generate and save drafts but do NOT queue them either."
fi

claude --dangerously-skip-permissions --model claude-opus-4-8 --print "$SKILL" \
  2>&1 | tee -a logs/night_prep.log

PUSH_OK=1
sync_push "night-prep: $(date -u +%Y-%m-%d)" contacts.xlsx drafts/ outbox/ cache/ || PUSH_OK=0

echo "$TODAY" > "$STAMP"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] run_night_prep.sh done"

# Heartbeat / dead-man's switch: ping an external monitor (healthchecks.io) ONLY on a
# CONFIRMED push. NIGHT PREP is now the keystone run (it used to be run_agent.sh) — if this does
# not happen, there is nothing for dispatch.py to send all day. Update the healthchecks.io schedule
# to `0 21 * * 0-4` UTC to match, or it will alarm every night at the old time. The agent is the keystone run — if the VM stops running, OR its push fails
# (e.g. an expired GitHub credential), the ping is SKIPPED → healthchecks.io alerts Zineb,
# independent of the VM's own Gmail/Claude/git auth (the recurring blind spot). Previously this
# pinged on local success even when the push failed, masking the exact outage we keep hitting.
# Configure the check at healthchecks.io to email on a missed ping. Inert unless HEALTHCHECK_URL
# is set in .env; read at runtime so no secret is hardcoded (safe for the public mirror).
_HC="$(grep -E '^HEALTHCHECK_URL=' "$DIR/.env" | cut -d= -f2- || true)"
if [ -n "$_HC" ] && [ "$PUSH_OK" = "1" ]; then
  curl -fsS -m 10 --retry 3 "$_HC" >/dev/null 2>&1 || true
elif [ -n "$_HC" ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] push failed — skipping healthcheck ping so the dead-man's switch fires"
fi
