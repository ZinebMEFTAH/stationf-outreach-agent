#!/usr/bin/env python3
"""
Stalled warm-lead alerter.

A human reply is the scarcest thing this system produces — roughly one in fifty delivered
cold emails earns one. The 2026-09 audit found the pipeline losing them at exactly that
point: `tracker.stalled_conversations()` correctly flagged a founder-level reply that had
been idle for weeks, /status and /followup-check both displayed it, and nothing ever
pushed it to Zineb. A flag nobody reads is not a safety net.

This turns the flag into a push: one email listing every warm thread going cold, with what
the person actually said, so the next action is obvious. Reply-drafting stays manual and
human — this only makes sure the thread is never silently forgotten.

Dedup: a lead is alerted at most once every ALERT_COOLDOWN_DAYS, so a thread that stays
idle nags weekly rather than daily. State lives in cache/stalled_alerts.json.

Usage:
  python stalled_alert.py                 # dry-run: print what would be sent
  python stalled_alert.py --send          # send if there is anything new to report
  python stalled_alert.py --send --min-days 3
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

_STATE = Path(__file__).parent / "cache" / "stalled_alerts.json"
ALERT_COOLDOWN_DAYS = 7
ESCALATE_AFTER_DAYS = 15          # idle longer than this is flagged as urgent in the subject


def _load() -> dict:
    try:
        return json.loads(_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d: dict) -> None:
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    _STATE.write_text(json.dumps(d, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _key(rec: dict) -> str:
    return f"{str(rec.get('Company','')).strip().lower()}|{str(rec.get('Role','')).strip().lower()}"


def due(min_days: int = 5) -> list[dict]:
    """Stalled leads not alerted within the cooldown window, most-stale first."""
    import tracker
    state = _load()
    today = date.today()
    out = []
    for rec in tracker.stalled_conversations(days=min_days):
        last = state.get(_key(rec), {}).get("last_alert")
        if last:
            try:
                if (today - datetime.fromisoformat(last).date()).days < ALERT_COOLDOWN_DAYS:
                    continue
            except Exception:
                pass
        out.append(rec)
    return out


def _dedupe_by_company(leads: list[dict]) -> list[dict]:
    """One entry per company, keeping the stalest.

    A company reached on two addresses (the fintech: the CTO's mailbox and the generic inbox) produced
    two identical entries carrying the same next action. The nudge list only works if it is short
    enough to act on in one sitting.
    """
    best: dict[str, dict] = {}
    for r in leads:
        co = str(r.get("Company", "")).strip().lower()
        cur = best.get(co)
        if cur is None or int(r.get("biz_days_idle") or 0) > int(cur.get("biz_days_idle") or 0):
            best[co] = dict(r, _dupes=int((cur or {}).get("_dupes", 0)) + (1 if cur else 0))
        else:
            cur["_dupes"] = int(cur.get("_dupes", 0)) + 1
        # carry a redirect found on ANY row for the company — it applies company-wide
        if r.get("redirect_to") and not best[co].get("redirect_to"):
            best[co]["redirect_to"] = r["redirect_to"]
        if r.get("meeting_invite"):
            best[co]["meeting_invite"] = True
    return sorted(best.values(), key=lambda r: int(r.get("biz_days_idle") or 0), reverse=True)


def compose(leads: list[dict]) -> tuple[str, str]:
    leads = _dedupe_by_company(leads)
    worst = max((int(r.get("biz_days_idle") or 0) for r in leads), default=0)
    urgent = worst >= ESCALATE_AFTER_DAYS
    subject = (f"{'[URGENT] ' if urgent else ''}{len(leads)} warm lead"
               f"{'s' if len(leads) != 1 else ''} going cold — {worst} business days idle")

    lines = [
        "These people REPLIED to you. Nothing has moved since.",
        "",
        "A human reply is the rarest thing the agent produces — roughly 1 per 50 delivered",
        "cold emails. Each one below is an opportunity still open, not a lost cause.",
        "",
    ]
    for i, r in enumerate(leads, 1):
        idle = r.get("biz_days_idle", "?")
        extra = int(r.get("_dupes") or 0)
        lines += [
            f"{i}. {r.get('Company','?')}  —  idle {idle} business days"
            + (f"  (+{extra} more row{'s' if extra > 1 else ''} for this company)" if extra else ""),
            f"   Role:    {str(r.get('Role','') or '')[:90]}",
            f"   Contact: {r.get('Contact Email','')}",
            f"   Status:  {r.get('Status','')}",
        ]
        # The most actionable thing a reply can contain: the company naming a better address.
        # One arrived on 2026-08-06 ("adressez votre candidature à recruitment@acme.io") and it
        # went unused for 27 days, because the nudge trimmed the text before the address appeared.
        # An interview was actually scheduled and the thread still went quiet. That is not an
        # ordinary stall — it is the closest this system has ever come to an offer.
        if r.get("meeting_invite"):
            lines += [
                "   ➜ AN INTERVIEW WAS SCHEDULED ON THIS THREAD, and it has gone silent.",
                "     Whether it happened or not, this is the most valuable thread you have.",
                "     Re-open it today — one short message, no explanation needed.",
            ]
        if r.get("redirect_to"):
            lines += [
                f"   ➜ THEY ASKED YOU TO WRITE TO: {r['redirect_to']}",
                "     That is an invitation from the company. Send it yourself — highest-yield",
                "     action on this list, and the agent will not do it for you (replies are manual).",
            ]
        lines += [
            f"   They said: {str(r.get('last_reply','') or '')[:260]}",
            "",
        ]
    lines += [
        "-" * 62,
        "Next step is yours — the agent drafts replies but never sends them.",
        "  /interview-prep COMPANY   prep sheet if this is converting",
        "  /cv-builder COMPANY       role-adapted CV to attach",
        "",
        f"Generated by stalled_alert.py on {date.today().isoformat()}.",
    ]
    return subject, "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--send", action="store_true", help="actually send (default: dry-run)")
    ap.add_argument("--min-days", type=int, default=5,
                    help="business days of silence before a lead counts as stalled")
    args = ap.parse_args(argv[1:])

    leads = due(args.min_days)
    if not leads:
        print("[stalled] nothing due — no warm lead is going cold (or all were alerted recently)")
        return 0

    subject, body = compose(leads)
    print(f"[stalled] {len(leads)} row(s) due, {len(_dedupe_by_company(leads))} compan(y/ies)\n")
    print(f"SUBJECT: {subject}\n")
    print(body)

    if not args.send:
        print("\n[stalled] dry-run — nothing sent. Re-run with --send.")
        return 0

    import config
    from smtp_send import send_and_log
    to = getattr(config, "INTERNAL_ALERT_EMAIL", None) or getattr(config, "EMAIL_ADDRESS", None)
    if not to:
        print("[stalled] no INTERNAL_ALERT_EMAIL configured — cannot send", file=sys.stderr)
        return 1

    res = send_and_log(to_address=to, subject=subject, body=body, attachment_path=None,
                       new_status=None, kind="alert", dry_run=False)
    if not res.ok:
        print(f"[stalled] send FAILED: {res.error}", file=sys.stderr)
        return 1

    state = _load()
    today = date.today().isoformat()
    for r in leads:
        state[_key(r)] = {"last_alert": today, "idle_at_alert": r.get("biz_days_idle")}
    _save(state)
    print(f"\n[stalled] alert sent to {to}; {len(leads)} lead(s) marked (cooldown "
          f"{ALERT_COOLDOWN_DAYS}d)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
