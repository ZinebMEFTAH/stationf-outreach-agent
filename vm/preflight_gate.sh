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
  if ! python preflight.py --quiet >> "$logf" 2>&1; then
    # Best-effort alert (uses --kind alert: raw, uncounted, not logged)
    python smtp_send.py \
      --to "you@example.com" \
      --kind alert \
      --subject "[PREFLIGHT FAIL] ${agent} $(date +%Y-%m-%d)" \
      --body "preflight.py failed before the ${agent} run — the run was skipped to avoid operating on a broken system. Inspect ${logf} and run 'python preflight.py' locally to see which checks failed." \
      --send >/dev/null 2>&1 || true
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] preflight FAILED — skipping ${agent} run"
    return 1
  fi

  # Claude usage self-throttle (fail-OPEN): protect the rolling-5h / weekly subscription
  # quota from runaway bunching. CRITICAL: skip ONLY on exit code 3 (a deliberate throttle);
  # any other non-zero is a crash in the check itself → proceed anyway, never block outreach
  # on a bookkeeping bug. Records the run when it allows it.
  local reason rc
  reason=$(python usage_budget.py claude-gate 2>>"$logf"); rc=$?
  if [ "$rc" -eq 3 ]; then
    python smtp_send.py \
      --to "you@example.com" \
      --kind alert \
      --subject "[CLAUDE BUDGET] ${agent} throttled $(date +%Y-%m-%d)" \
      --body "The ${agent} run was skipped to stay under the Claude usage quota — ${reason}. This is the self-throttle protecting the 5h/weekly limit (usually caused by extra manual runs). Normal cron resumes automatically once the window clears." \
      --send >/dev/null 2>&1 || true
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Claude budget throttle — skipping ${agent} run (${reason})"
    return 1
  fi
  return 0
}
