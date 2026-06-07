#!/bin/bash
# Pre-run health check for any agent.
# Call at the top of each run_*.sh (after weekend guard, before stamp dedup).
#
# Usage: bash vm/health_check.sh AGENT_NAME LOG_FILE STAMP_FILE
#
# Sends an alert to you@example.com if:
#   - the previous run's log contains Python errors / tracebacks
#   - the stamp file shows the agent hasn't completed in 4+ calendar days

AGENT="${1:?agent name required}"
LOG_FILE="${2:-}"
STAMP_FILE="${3:-}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

ALERT=""

# --- 1. Check stamp freshness (4-day threshold covers Mon←Fri gap) ----------
if [ -n "$STAMP_FILE" ] && [ -f "$STAMP_FILE" ]; then
    LAST=$(cat "$STAMP_FILE")
    LAST_TS=$(date -d "$LAST" +%s 2>/dev/null || echo 0)
    NOW_TS=$(date +%s)
    DAYS_AGO=$(( (NOW_TS - LAST_TS) / 86400 ))
    if [ "$DAYS_AGO" -gt 4 ]; then
        ALERT="${ALERT}⚠ MISSED RUNS: $AGENT last completed $DAYS_AGO days ago (${LAST}).\n"
    fi
fi

# --- 2. Scan previous run's log for errors ----------------------------------
if [ -n "$LOG_FILE" ] && [ -s "$LOG_FILE" ]; then
    # Extract lines from the last "start" marker to end of file
    LAST_RUN=$(awk '/run_'"$AGENT"'\.sh start/{buf=""} {buf=buf"\n"$0} END{print buf}' "$LOG_FILE" 2>/dev/null \
               || tail -150 "$LOG_FILE")

    ERRORS=$(echo "$LAST_RUN" | grep -iE \
        "^Traceback \(most recent|^[A-Z][a-zA-Z]*Error:|^Exception:|authentication failed|SMTP.*fail|imap.*error|connection refused|No such file|claude auth login|Please log in|token.*expired|unauthorized|403 Forbidden" \
        | tail -8 || true)

    if [ -n "$ERRORS" ]; then
        ALERT="${ALERT}⚠ ERRORS IN LAST RUN:\n${ERRORS}\n\nLast 20 log lines:\n$(tail -20 "$LOG_FILE")\n"
    fi
fi

# --- 3. Send alert if anything found ----------------------------------------
if [ -n "$ALERT" ]; then
    TMPBODY=$(mktemp /tmp/healthcheck_XXXXXX.txt)
    printf "Agent: %s\nTime (UTC): %s\n\n%b" \
        "$AGENT" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ALERT" > "$TMPBODY"

    source venv/bin/activate 2>/dev/null || true
    python smtp_send.py \
        --to "you@example.com" \
        --subject "[AGENT ALERT] $AGENT — issue detected $(date +%Y-%m-%d)" \
        --body-file "$TMPBODY" \
        --send 2>/dev/null \
        && echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] health_check: alert sent for $AGENT" \
        || echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] health_check: WARNING — could not send alert"

    rm -f "$TMPBODY"
else
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] health_check: $AGENT OK"
fi
