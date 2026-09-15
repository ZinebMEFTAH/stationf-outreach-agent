#!/usr/bin/env python3
"""New-reply alerter — pure Python, so a reply still reaches Zineb on a day with no Claude run.

Replies used to be surfaced by /followup-check (04:00) and /daily-agent (09:00), both Claude runs
inside the working day. Moving all Claude work to the 22:00-03:00 window (see outbox.py) would have
left a gap: a reply landing at 10:00 would sit unseen until that night. A human reply is the scarcest
thing this system produces — roughly one per fifty delivered cold emails — and the 2026-09 audit
found them going quiet for weeks, so losing same-day visibility was not an acceptable trade.

This syncs the inbox and pushes anything genuinely new, using the SAME predicates the rest of the
system uses so the three can never disagree about what counts as a reply:

  has_genuine_human_reply  a real person wrote back — not a bounce, not an autoresponder, not a
                           canned "here are our job offers" (looks_like_template_reply)
  looks_like_meeting_invite  someone proposed a time. Haliro's CEO did exactly that and the thread
                           went silent for 44 business days.
  redirect_address         "write to recruitment@…" — the highest-yield reply there is, and one
                           sat unused for 27 days
  looks_like_rejection     an explicit no: reported, but flagged as closed so it reads differently

Claude still drafts the SUGGESTED REPLY, on the next night run. This alert exists so Zineb knows
within the hour, not so the agent answers — replies have always been draft-and-approve, and she
sends them herself.

Dedup on (company, last-interaction-date) in cache/reply_alerts.json: re-running every few hours
must not re-send the same reply, but a NEW message on the same thread must alert again.

  python reply_alert.py                 # dry run — sync + show what would be sent
  python reply_alert.py --send
  python reply_alert.py --send --no-sync   # alert on what is already in the tracker
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import tracker

_STATE = Path(__file__).parent / "cache" / "reply_alerts.json"
LOOKBACK_DAYS = 3        # a reply older than this was already reported by an earlier pass


def _load() -> dict:
    try:
        return json.loads(_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d: dict) -> None:
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    _STATE.write_text(json.dumps(d, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _last_contact_text(log: str) -> str:
    """The most recent 'Contact:' block in the conversation log."""
    parts = [p.strip() for p in str(log or "").split("[") if "Contact:" in p]
    return ("[" + parts[-1]).strip() if parts else ""


def new_replies(lookback_days: int = LOOKBACK_DAYS) -> list[dict]:
    """Rows with a genuine human reply seen in the last `lookback_days`, not yet alerted."""
    import pandas as pd
    df = tracker.load()
    if df.empty:
        return []
    state = _load()
    cutoff = date.today() - timedelta(days=lookback_days)
    out = []
    for _, r in df.iterrows():
        log = str(r.get("Conversation Log") or "")
        if not tracker.has_genuine_human_reply(log, r.get("Status", "")):
            continue
        when = pd.to_datetime(r.get("Last Interaction Date"), errors="coerce")
        if pd.isna(when) or when.date() < cutoff:
            continue
        company = str(r.get("Company") or "").strip()
        # Keyed on company + the date of the reply, so a NEW message on the same thread alerts
        # again while a re-run within the day does not.
        key = f"{company.lower()}|{when.date().isoformat()}"
        if key in state:
            continue
        text = _last_contact_text(log)
        out.append({
            "key": key,
            "company": company,
            "role": str(r.get("Role") or ""),
            "email": str(r.get("Contact Email") or ""),
            "when": when.date().isoformat(),
            "text": text,
            "rejection": tracker.looks_like_rejection(text),
            "meeting": tracker.looks_like_meeting_invite(text),
            "redirect": tracker.redirect_address(text),
        })
    # Most urgent first: a proposed meeting, then a redirect, then everything else.
    out.sort(key=lambda x: (not x["meeting"], not x["redirect"], x["rejection"]))
    return out


def compose(replies: list[dict]) -> tuple[str, str]:
    live = [r for r in replies if not r["rejection"]]
    subject = (f"💬 {len(live)} nouvelle(s) réponse(s)" if live
               else f"{len(replies)} réponse(s) — sans suite")
    if any(r["meeting"] for r in replies):
        subject = "📅 ENTRETIEN PROPOSÉ — " + subject
    lines = [
        "Réponses arrivées depuis le dernier passage.",
        "La suggestion de réponse rédigée par l'agent arrivera cette nuit ; "
        "c'est toi qui envoies, comme d'habitude.",
        "",
    ]
    for r in replies:
        tag = ("📅 ENTRETIEN PROPOSÉ" if r["meeting"] else
               "➜ REDIRECTION" if r["redirect"] else
               "✗ refus (thread clos)" if r["rejection"] else "💬 réponse")
        lines += [f"{tag} — {r['company']}" + (f" · {r['role']}" if r['role'] else ""),
                  f"   {r['when']} · {r['email']}"]
        if r["redirect"]:
            lines.append(f"   ➜ ils demandent d'écrire à : {r['redirect']}")
        body = " ".join(r["text"].split())
        if body:
            lines.append(f"   « {body[:400]}{'…' if len(body) > 400 else ''} »")
        lines.append("")
    return subject, "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Alert Zineb about new human replies (no Claude)")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--no-sync", action="store_true", help="skip the IMAP fetch")
    ap.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
    a = ap.parse_args(argv)

    if not a.no_sync:
        try:
            import imap_fetch
            imap_fetch.sync(since_days=max(2, a.lookback_days))
        except Exception as e:  # noqa: BLE001 — a mail outage must not hide replies already stored
            print(f"[reply-alert] inbox sync failed ({type(e).__name__}: {e}); "
                  "continuing on what is already recorded", file=sys.stderr)

    replies = new_replies(a.lookback_days)
    if not replies:
        print("[reply-alert] no new replies")
        return 0

    subject, body = compose(replies)
    print(f"SUBJECT: {subject}\n\n{body}")
    if not a.send:
        print("[reply-alert] dry-run — nothing sent. Re-run with --send.")
        return 0

    import config
    from smtp_send import send_and_log
    to = getattr(config, "INTERNAL_ALERT_EMAIL", None) or getattr(config, "EMAIL_ADDRESS", None)
    if not to:
        print("[reply-alert] no INTERNAL_ALERT_EMAIL configured", file=sys.stderr)
        return 1
    res = send_and_log(to_address=to, subject=subject, body=body, attachment_path=None,
                       new_status=None, kind="alert", dry_run=False)
    if not res.ok:
        print(f"[reply-alert] send FAILED: {res.error}", file=sys.stderr)
        return 1

    state = _load()
    for r in replies:
        state[r["key"]] = {"alerted": datetime.now().isoformat(timespec="seconds")}
    _save(state)
    print(f"[reply-alert] alerted {len(replies)} reply(ies) to {to}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
