#!/bin/bash
# Sourced by run_*.sh. Runs preflight.py against the freshly-pulled code.
# On failure: send an alert and return 1 so the caller can skip the run —
# better to do nothing than to operate on a broken system.
#
# Usage (after `git pull`, before invoking claude):
#   source "$DIR/vm/preflight_gate.sh"
#   preflight_gate "agent" "logs/agent.log" || exit 1

preflight_gate() {
  local agent="$1"
  local logf="${2:-/dev/null}"
  if python preflight.py --quiet >> "$logf" 2>&1; then
    return 0
  fi
  # Best-effort alert (uses --kind alert: raw, uncounted, not logged)
  python smtp_send.py \
    --to "you@example.com" \
    --kind alert \
    --subject "[PREFLIGHT FAIL] ${agent} $(date +%Y-%m-%d)" \
    --body "preflight.py failed before the ${agent} run — the run was skipped to avoid operating on a broken system. Inspect ${logf} and run 'python preflight.py' locally to see which checks failed." \
    --send >/dev/null 2>&1 || true
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] preflight FAILED — skipping ${agent} run"
  return 1
}
