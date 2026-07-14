"""Generic usage-quota ledger — keeps the agent inside every external rate limit.

The system leans on several capped resources:
  • Hunter.io email verification — ~100/month on the free tier
  • the Claude subscription token — a rolling ~5-hour window AND a weekly ceiling
  • (future) any other metered API

This module records timestamped usage events per resource in ``cache/usage_ledger.json``
and answers "may I spend one more?" over rolling windows (5h / day / week) and the calendar
month. Callers check ``allow()`` and **degrade gracefully** instead of slamming into a hard
quota (a failed run or a 429).

Design: pure local I/O, no network. It's a *self-throttle*, deliberately conservative:
- Hunter spend is fail-CLOSED (when in doubt, don't spend — a graceful fallback exists).
- Claude runs are fail-OPEN (a bug here must never silently halt outreach — see claude_run_gate).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

_PATH = Path(__file__).parent / "cache" / "usage_ledger.json"

HOUR = 3600
DAY = 24 * HOUR
WEEK = 7 * DAY

# Prune events older than this so the ledger can't grow unbounded.
_RETAIN = 40 * DAY


def _load() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        _PATH.parent.mkdir(exist_ok=True)
        tmp = _PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_PATH)
    except Exception:
        pass  # ledger is best-effort; never let bookkeeping break a run


def _month_key(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m")


def record(resource: str, ts: float | None = None) -> None:
    """Append one usage event for ``resource`` (now, or an explicit timestamp)."""
    ts = time.time() if ts is None else ts
    data = _load()
    events = [t for t in data.get(resource, []) if ts - t <= _RETAIN]
    events.append(ts)
    data[resource] = events
    _save(data)


def count(resource: str, window_seconds: float | None = None,
          month: bool = False, now: float | None = None) -> int:
    """Count events for ``resource`` within a rolling window, or the current calendar month."""
    now = time.time() if now is None else now
    events = _load().get(resource, [])
    if month:
        mk = _month_key(now)
        return sum(1 for t in events if _month_key(t) == mk)
    if window_seconds is None:
        return len(events)
    return sum(1 for t in events if now - t <= window_seconds)


def allow(resource: str, *, per_5h: int | None = None, per_day: int | None = None,
          per_week: int | None = None, per_month: int | None = None,
          now: float | None = None) -> tuple[bool, str]:
    """Return (ok, reason). A cap of None or <= 0 is treated as *unlimited* (disabled).

    Checks only the caps you pass; returns False + a human reason on the first breach.
    """
    checks = [
        ("5h", per_5h, 5 * HOUR, False),
        ("day", per_day, DAY, False),
        ("week", per_week, WEEK, False),
        ("month", per_month, None, True),
    ]
    for name, cap, window, is_month in checks:
        if not cap or cap <= 0:
            continue  # unlimited / disabled
        used = count(resource, window_seconds=window, month=is_month, now=now)
        if used >= cap:
            return False, f"{resource}: {used}/{cap} used in the last {name} — throttled"
    return True, f"{resource}: within budget"


def snapshot(resource: str) -> dict:
    """Small dict of current usage across windows — for /status and preflight surfacing."""
    return {
        "last_5h": count(resource, 5 * HOUR),
        "last_day": count(resource, DAY),
        "last_week": count(resource, WEEK),
        "this_month": count(resource, month=True),
    }


def claude_run_gate() -> tuple[bool, str]:
    """Fail-OPEN gate for one Claude subscription run; records the run when allowed.

    The 5h-spaced cron already keeps normal ops well under the caps — this only catches a
    runaway (manual-run bunching, a retry loop) before it burns the rolling-window or weekly
    Claude quota. Any internal error → ALLOW: a bookkeeping bug must never halt outreach.
    """
    try:
        import config
        ok, reason = allow(
            "claude_run",
            per_5h=config.CLAUDE_MAX_RUNS_5H,
            per_week=config.CLAUDE_MAX_RUNS_7D,
        )
        if ok:
            record("claude_run")
        return ok, reason
    except Exception as e:  # fail-open
        return True, f"claude budget check errored ({type(e).__name__}: {e}) — allowing"


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "claude-gate":
        ok, reason = claude_run_gate()
        print(reason)
        sys.exit(0 if ok else 3)  # 3 = deliberately throttled (callers skip only on 3)
    # default: print a usage snapshot
    print(json.dumps({r: snapshot(r) for r in ("hunter_verify", "claude_run")}, indent=2))
