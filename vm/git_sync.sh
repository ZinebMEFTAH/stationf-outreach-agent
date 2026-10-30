#!/bin/bash
# Shared, LOUD git sync for the VM run scripts.
#
# WHY THIS EXISTS: the run scripts used to do `git fetch … 2>/dev/null` and
# `git push … 2>/dev/null || true`, so a failed fetch/push (the #1 recurring
# outage — an expired/revoked GitHub credential) failed SILENTLY: the VM quietly
# stopped pulling new code AND stopped pushing its results, and nobody was told.
#
# Every git operation here is CHECKED. Any failure emails an alert (via smtp_send,
# which uses the Gmail app-password and is independent of git auth, so the alert
# still gets out even when GitHub auth is dead). We also guarantee we operate on
# `main` (never a detached HEAD) and REPORT whether a push actually reached origin
# — the caller gates the healthchecks.io ping on that, so a masked push failure
# trips the external dead-man's switch instead of pinging a false "all good".
#
# Usage (from a run_*.sh, after `cd "$DIR"` and activating the venv):
#   source "$DIR/vm/git_sync.sh"
#   sync_pull || true                     # alerts on failure; run continues on stale code
#   ...do work...
#   sync_push "agent: $(date -u +%F)" contacts.xlsx drafts/ cache/ && PUSH_OK=1 || PUSH_OK=0

_gitsync_alert() {
  # _gitsync_alert "short subject" "body"
  local subj="$1" body="$2"
  python smtp_send.py \
    --to "you@example.com" \
    --kind alert \
    --subject "[VM GIT] ${subj} $(date +%Y-%m-%d)" \
    --body "$(printf '%b' "$body")" \
    --send >/dev/null 2>&1 || true
}

# sync_pull — fetch + merge origin/main, keeping our data files on conflict (-X ours).
# Guarantees a real branch first (recovers a detached HEAD). Returns 0 on success, 1 on
# failure (already alerted). On failure the caller should still run: the work (SMTP sends)
# is independent of git, and the results commit+push on the next healthy run.
#
# WHY THE PRE-MERGE AUTO-COMMIT (the OTHER recurring "not pulling" bug, fixed 2026-07-08):
# `-X ours` / `.gitattributes merge=ours` only resolves conflicts between COMMITTED histories.
# It does NOTHING for *uncommitted* local edits. Tracked data files (contacts.xlsx, cache/*.json)
# are written by BOTH the VM and dev sessions, so whenever the VM had a modified-but-uncommitted
# tracked file that dev also changed upstream, `git merge` aborted with "Your local changes would
# be overwritten by merge" — and STAYED stuck on every later pull until a human reset the tree.
# We now snapshot any stray local changes into a commit FIRST, so the tree is always clean and the
# merge is always history-vs-history, where -X ours is guaranteed to auto-resolve. The pull can no
# longer be blocked by a dirty working tree.
sync_pull() {
  local out
  # Recover from a detached HEAD (the June-2026 silent-failure bug) before anything else.
  if ! git symbolic-ref -q HEAD >/dev/null 2>&1; then
    git checkout main >/dev/null 2>&1 || true
    _gitsync_alert "detached HEAD recovered" \
      "The VM was on a DETACHED HEAD (a known silent-failure mode). Ran 'git checkout main' to recover. Verify the last runs actually pushed."
  fi
  # Snapshot any stray local changes so the working tree is CLEAN before the merge. Without this
  # a single uncommitted tracked-file edit that also changed upstream blocks every future pull.
  if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
    git add -A 2>/dev/null || true
    git commit -q -m "gitsync: autosave VM local state before pull $(date -u +%F)" >/dev/null 2>&1 || true
  fi
  if ! out=$(git fetch origin main 2>&1); then
    _gitsync_alert "fetch FAILED — VM is NOT pulling" \
      "git fetch origin main FAILED on the VM, so it is NOT receiving new code.\n\nMost likely the GitHub credential expired or was revoked (HTTPS token / SSH deploy key). Fix auth on the VM — see OPERATIONS.md 'VM git auth'.\n\ngit output:\n${out}"
    return 1
  fi
  if ! out=$(git merge -q -X ours origin/main 2>&1); then
    git merge --abort >/dev/null 2>&1 || true
    # The tree is clean (autosaved above), so a merge can only fail on a real divergence git can't
    # auto-resolve — should be near-impossible with -X ours. Recover the CODE from origin (source of
    # truth for code) while KEEPING the VM's data files (source of truth for outreach data): take
    # origin's tree, then restore data from the autosave commit made just above and re-commit it.
    local saved; saved=$(git rev-parse HEAD 2>/dev/null)
    if out=$(git reset --hard origin/main 2>&1); then
      # outbox/ too (2026-10-30): its statuses say what was SENT this morning. Losing them would
      # put sent emails back to "queued" for the second pass (the duplicate guard would refuse
      # them, but the day's record would lie).
      git checkout "$saved" -- contacts.xlsx cache/ drafts/ outbox/ 2>/dev/null || true
      git add -A 2>/dev/null || true
      git commit -q -m "gitsync: keep VM data after divergence recovery $(date -u +%F)" >/dev/null 2>&1 || true
      _gitsync_alert "merge diverged — auto-recovered to origin/main (data kept)" \
        "git merge origin/main could not auto-resolve, so the VM took origin/main for CODE and restored its own contacts.xlsx / cache / drafts on top, so it keeps pulling. Verify contacts.xlsx looks right.\n\ngit output:\n${out}"
      return 0
    fi
    _gitsync_alert "merge FAILED — running on STALE code" \
      "git merge origin/main FAILED and recovery to origin/main also FAILED on the VM, so it is running on STALE code.\n\ngit output:\n${out}"
    return 1
  fi
  _deps_sync
  return 0
}

# _outbox_merge_driver — the queue files are JSON lists of emails, and a LINE merge of two
# edited lists is how an approved email disappears (2026-10-30): the VM appends a deferred email
# to tomorrow's file while the Mac appends her new ones to the same file, both at the end, and
# `-X ours` keeps only the VM's lines. `outbox.py merge` merges them email by email instead.
# Registered on every sync because git config is per clone and the VM's was never told.
_outbox_merge_driver() {
  git config merge.outbox.name "union des emails de la file (outbox.py merge)" 2>/dev/null || true
  git config merge.outbox.driver "python outbox.py merge %O %A %B" 2>/dev/null || true
}
_outbox_merge_driver

# _deps_sync — the VM installed requirements.txt ONCE, at setup. A package added later never
# reached it: pypdf was used by the code and missing on the VM, which is half of why preflight
# failed there for a month (2026-10-30). Reinstall when the file changes; never block the run.
_deps_sync() {
  local h stamp="cache/.requirements_installed"
  h=$(cksum requirements.txt 2>/dev/null | cut -d' ' -f1)
  [ -n "$h" ] || return 0
  [ "$(cat "$stamp" 2>/dev/null)" = "$h" ] && return 0
  local out lim=""
  command -v timeout >/dev/null 2>&1 && lim="timeout 900"
  if out=$($lim python -m pip install -q -r requirements.txt 2>&1); then
    mkdir -p cache && echo "$h" > "$stamp"
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] requirements.txt changed — dependencies installed"
  elif [ "$(cat "$stamp.failed" 2>/dev/null)" != "$h" ]; then
    echo "$h" > "$stamp.failed"
    _gitsync_alert "pip install FAILED — new dependencies are missing" \
      "requirements.txt changed and 'pip install -r requirements.txt' failed on the VM, so code that needs the new packages may fail.\n\npip output (tail):\n$(printf '%s' "$out" | tail -15)"
  fi
  return 0
}

# sync_push "commit message" path...  — stage the given paths, commit if anything changed,
# and push. Returns 0 if there was nothing to commit (a legitimately quiet run) OR the push
# reached origin; returns 1 if a commit was made but the push FAILED (already alerted). The
# caller pings the healthcheck ONLY on a 0 return, so a push failure trips the dead-man's switch.
sync_push() {
  local msg="$1"; shift
  git add "$@" 2>/dev/null || true
  if ! git diff --cached --quiet 2>/dev/null; then
    git commit -q -m "$msg" >/dev/null 2>&1 || true
  fi
  # ⚠ PUSH WHENEVER WE ARE AHEAD, not only when THIS run changed something (2026-10-30). The
  #   morning autosaves (replies read by reply_alert, contacts, caches) were committed locally
  #   and only travelled when a run happened to change its own paths — nothing reached GitHub
  #   from 2026-10-19 on, so the Mac and the dashboard read eleven-day-old data.
  if [ "$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)" = "0" ]; then
    return 0   # nothing to deliver — quiet but healthy
  fi
  local out
  # The Mac may have pushed her queue since our pull: merge it (email by email for outbox/,
  # see _outbox_merge_driver) and try once more before calling it a failure.
  if ! out=$(git push origin main 2>&1); then
    if git fetch -q origin main 2>/dev/null && git merge -q -X ours origin/main >/dev/null 2>&1; then
      out=$(git push origin main 2>&1) && return 0
    else
      git merge --abort >/dev/null 2>&1 || true
    fi
    _gitsync_alert "push FAILED — results did NOT reach GitHub" \
      "git push origin main FAILED on the VM. Today's work ('${msg}') is committed LOCALLY but did NOT reach GitHub — so it never reaches you or the dashboard.\n\nMost likely the GitHub credential expired or was revoked. Fix auth on the VM (see OPERATIONS.md 'VM git auth'); the pending commits will push automatically on the next healthy run.\n\ngit output:\n${out}"
    return 1
  fi
  return 0
}
