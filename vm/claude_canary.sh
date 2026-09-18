#!/bin/bash
# Daily Claude canary — 07:30 Paris.
#
# WHY THIS EXISTS. On 2026-09-17 the speculative run failed with "You've hit your session limit ·
# resets 2:10am (UTC)". It failed silently: the run exited, cron logged it, and nothing told
# anyone. Zineb only found it days later while reading commit timestamps by hand. Every other
# silent-decay failure in this system has the same shape — a dead Hunter key (10-day cold-send
# outage), a revoked Gmail app password (28 days). The pattern is always "the thing that would
# report the failure is the thing that failed".
#
# So: one trivial prompt a day that proves three things at once —
#   1. the CLI is still AUTHENTICATED (the OAuth token has not expired or been revoked),
#   2. the subscription has QUOTA (no session-limit wall),
#   3. the VM is UP at all (the 2026-09-17 instance stop went unnoticed for ~37 hours).
# Anything else is out of scope. It must stay this small: see the preflight guard, which
# allows this one script inside the working day precisely BECAUSE it is trivial.
#
# WHAT IT COSTS. A few tokens. But note it also OPENS a 5-hour session window at 07:30, so the
# allowance resets ~12:30 rather than 5h after Zineb's own first message. That is the trade she
# asked for (2026-09-18): a predictable reset she can plan around, over a floating one.
set -uo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
source venv/bin/activate

_OAT="$(grep -E '^CLAUDE_CODE_OAUTH_TOKEN=' "$DIR/.env" | cut -d= -f2- || true)"
if [ -n "$_OAT" ]; then export CLAUDE_CODE_OAUTH_TOKEN="$_OAT"; fi

mkdir -p logs
LOG="$DIR/logs/canary.log"
STAMP_OK="$DIR/cache/.canary_last_ok"
STAMP_ALERT="$DIR/cache/.canary_last_alert"
mkdir -p cache

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] canary start" >> "$LOG"

# --print: non-interactive. Deliberately the cheapest possible prompt.
OUT="$(timeout 120 claude --print 'Reply with exactly: OK' 2>&1 || true)"
echo "$OUT" | head -5 >> "$LOG"

FAIL=""
if [ -z "$OUT" ]; then
  FAIL="the claude CLI returned nothing (timeout, or the binary is missing)"
elif echo "$OUT" | grep -qiE "session limit|usage limit|rate limit"; then
  FAIL="Claude QUOTA is exhausted at 07:30 — the night jobs are eating the allowance: $(echo "$OUT" | head -2 | tr '\n' ' ')"
elif echo "$OUT" | grep -qiE "unauthor|authenticat|invalid.*token|login|expired"; then
  FAIL="Claude AUTH is broken — the OAuth token looks expired or revoked. Re-run 'claude setup-token' on the VM and update CLAUDE_CODE_OAUTH_TOKEN in .env: $(echo "$OUT" | head -2 | tr '\n' ' ')"
elif ! echo "$OUT" | grep -qiE "\bOK\b"; then
  FAIL="the canary prompt did not come back as expected: $(echo "$OUT" | head -2 | tr '\n' ' ')"
fi

if [ -z "$FAIL" ]; then
  date +%Y-%m-%d > "$STAMP_OK"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] canary OK" >> "$LOG"
  exit 0
fi

# Alert, but not every single morning for a standing fault — once a day is the point, a flood
# is how an alert becomes wallpaper. --kind alert: raw, uncounted, never logged to the tracker.
TODAY=$(date +%Y-%m-%d)
if [ ! -f "$STAMP_ALERT" ] || [ "$(cat "$STAMP_ALERT")" != "$TODAY" ]; then
  python smtp_send.py \
    --to "you@example.com" \
    --kind alert \
    --subject "[CLAUDE CANARY] agent cannot reach Claude — ${TODAY}" \
    --body "The 07:30 canary could not get a normal reply from the Claude CLI on the VM.

Reason: ${FAIL}

Last successful canary: $(cat "$STAMP_OK" 2>/dev/null || echo 'never recorded')

This means tonight's Claude jobs (night-prep, contact enrichment, scraping) will probably
fail too, and they fail QUIETLY — no email, just a log line. Check logs/canary.log and
logs/cron.log on the VM." \
    --send >/dev/null 2>&1 || true
  echo "$TODAY" > "$STAMP_ALERT"
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] canary FAIL: ${FAIL}" >> "$LOG"
exit 1
