#!/usr/bin/env python3
"""Send what last night's Claude run prepared. PURE PYTHON — spends no Claude quota.

This is the daytime half of the night-prep / day-send split (see outbox.py for why). It runs from
cron at the sending times, reads today's outbox, and hands each due message to `smtp_send.py`
unchanged. It makes no decisions about content and never calls an LLM, so Zineb's Claude allowance
is untouched while she works.

WHAT IT DOES NOT DO — deliberately:
  • It does not re-check whether a message is a good idea. `smtp_send.py` owns that, and still runs
    every gate AT SEND TIME: daily cap, duplicate guard, bounce blocklist, verification, the
    mailbox-evidence gate, and email_lint. Those fire hours after drafting, which is exactly what
    makes queueing safe — a lead that bounced, replied or got blocklisted overnight is refused now.
  • It NEVER retries. `smtp_send` exits 0 with "⚠ DELIVERED but …" on stderr when the mail went out
    and only the bookkeeping failed; treating that as a failure would double-send. Exit 0 is sent,
    full stop.
  • It does not stop on a refusal. A refused cold email is expected (an address goes bad, the cap
    is reached); the rest of the day's queue must still go out, so each item is independent.

    python dispatch.py              # dry run — show what would go now
    python dispatch.py --send
    python dispatch.py --send --all # ignore send_after (catch-up after an outage)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import subprocess
import sys
from pathlib import Path

import outbox

_ROOT = Path(__file__).parent


def _send_one(item: dict, really: bool) -> tuple[bool, str]:
    """Invoke smtp_send for one queued item. Returns (sent, detail)."""
    body = _ROOT / item["body_file"]
    if not body.exists():
        return False, f"draft file missing: {item['body_file']}"

    cmd = [sys.executable, str(_ROOT / "smtp_send.py"),
           "--to", item["to"], "--subject", item["subject"],
           "--body-file", str(body), "--kind", item["kind"]]
    for flag, key in (("--company", "company"), ("--role", "role"), ("--strategy", "strategy")):
        if item.get(key):
            cmd += [flag, str(item[key])]
    if item.get("attach"):
        cmd += ["--attach", str(item["attach"])]
    if really:
        cmd.append("--send")

    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_ROOT))
    tail = (r.stderr or r.stdout or "").strip().splitlines()
    detail = tail[-1][:300] if tail else ""
    # Exit 0 == delivered, even with a "DELIVERED but bookkeeping failed" warning on stderr.
    # Anything else is a refusal by one of the five gates or a transport failure.
    return r.returncode == 0, detail


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Send tonight's prepared outbox (no Claude)")
    ap.add_argument("--send", action="store_true", help="actually transmit (default: dry run)")
    ap.add_argument("--all", action="store_true",
                    help="ignore send_after — catch up everything still queued today")
    ap.add_argument("--day", help="YYYY-MM-DD (default: today)")
    a = ap.parse_args(argv)

    # Yesterday's unsent drafts are never carried forward: they open on a posting that is another
    # day older, against a cap that already reset.
    expired = outbox.expire_stale()
    if expired:
        print(f"[dispatch] expired {expired} unsent draft(s) from previous days")

    items = (outbox.load(a.day) if a.all else outbox.due(day=a.day))
    if a.all:
        items = [i for i in items if i.get("status") == "queued"]
    if not items:
        s = outbox.summary(a.day)
        print(f"[dispatch] nothing due — {s['queued']} still queued, "
              f"{s['sent']} sent, {s['refused']} refused today")
        return 0

    sent = refused = 0
    for it in items:
        label = f"{it['id']} {it['kind']:<9} {(it['company'] or '?')[:26]}"
        if not a.send:
            print(f"[dry-run] would send  {label}  → {it['to']}")
            continue
        ok, detail = _send_one(it, really=True)
        if ok:
            outbox.mark(it["id"], "sent", day=a.day)
            sent += 1
            print(f"✓ sent    {label}")
            if detail.startswith("⚠"):
                # Delivered; only the bookkeeping failed. Recorded, never retried.
                print(f"          ↳ {detail}")
        else:
            outbox.mark(it["id"], "refused", note=detail, day=a.day)
            refused += 1
            print(f"✗ refused {label}\n          ↳ {detail}")

    if a.send:
        s = outbox.summary(a.day)
        print(f"[dispatch] {sent} sent, {refused} refused this pass; "
              f"{s['queued']} still queued for later today")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
