#!/usr/bin/env python3
"""
Permanent hard-bounce blocklist.

A mailbox that answered "Address not found" once will answer it again. Re-sending
costs nothing to discover and everything to ignore: every hard bounce degrades the
sending domain's reputation for ALL mail, including the leads that would have replied.

The tracker already records bounces (status `Rejected`, log line `Contact: BOUNCED`),
but that is per-ROW. The same address reaches several rows (two roles at one company,
a generic inbox reused across leads), and a re-scrape can create a fresh Pending row
carrying an address that died months ago. This module keys on the ADDRESS itself, so a
bounce anywhere immunises everywhere.

Two layers, deliberately:
  • address — the exact mailbox hard-bounced. Never send again.
  • domain  — a *generic* local (contact@, jobs@) bounced, which says the company does
              not run that role inbox at all. Other generic locals on the same domain
              are then suspect; personal mailboxes there are not, and stay allowed.

Usage:
  import bounce_guard
  bounce_guard.is_blocked("contact@dead.com")     -> (True, "hard-bounced 2026-08-11")
  bounce_guard.record("contact@dead.com")          # called by imap_fetch on each bounce
  python bounce_guard.py seed                      # backfill from contacts.xlsx history
  python bounce_guard.py list
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

_PATH = Path(__file__).parent / "cache" / "bounce_blocklist.json"

# Generic role locals — a bounce on one of these is evidence about the DOMAIN's mail setup,
# not just that mailbox. Kept in sync with smtp_send._GENERIC_LOCALS.
_GENERIC_LOCALS = {
    "contact", "hello", "info", "team", "jobs", "job", "career", "careers", "recrutement",
    "recrute", "recrut", "rh", "hr", "bonjour", "hi", "sales", "press", "contactez", "talent",
    "hiring", "join", "work", "apply",
}


def _norm(email: str) -> str:
    e = (email or "").strip().lower()
    if "<" in e and ">" in e:                      # "Name (Title) <addr>" -> addr
        e = e[e.rfind("<") + 1:e.rfind(">")].strip()
    return e


def load() -> dict:
    try:
        d = json.loads(_PATH.read_text(encoding="utf-8"))
        d.setdefault("addresses", {})
        d.setdefault("generic_domains", {})
        return d
    except Exception:
        return {"addresses": {}, "generic_domains": {}}


def _save(d: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def record(email: str, when: str | None = None, reason: str = "hard bounce") -> bool:
    """Mark an address as permanently undeliverable. Returns True if newly added."""
    e = _norm(email)
    if "@" not in e:
        return False
    d = load()
    new = e not in d["addresses"]
    d["addresses"][e] = {"since": when or date.today().isoformat(), "reason": reason}
    local, _, domain = e.partition("@")
    if local in _GENERIC_LOCALS and domain:
        d["generic_domains"].setdefault(domain, {"since": when or date.today().isoformat(),
                                                 "bounced_local": local})
    _save(d)
    return new


def is_blocked(email: str) -> tuple[bool, str]:
    """(blocked, human-readable reason). Safe to call on any address shape."""
    e = _norm(email)
    if "@" not in e:
        return False, ""
    d = load()
    rec = d["addresses"].get(e)
    if rec:
        return True, f"hard-bounced {rec.get('since','?')} ({rec.get('reason','bounce')})"
    local, _, domain = e.partition("@")
    if local in _GENERIC_LOCALS:
        g = d["generic_domains"].get(domain)
        if g:
            return True, (f"{g.get('bounced_local','a generic inbox')}@{domain} hard-bounced "
                          f"{g.get('since','?')} — this domain does not run role inboxes")
    return False, ""


def seed_from_tracker() -> tuple[int, int]:
    """Backfill the blocklist from every BOUNCED line already in contacts.xlsx."""
    import tracker
    df = tracker.load()
    added = scanned = 0
    pat = re.compile(r"(?:wasn't|was not) delivered to\s+([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
    for _, row in df.iterrows():
        log = str(row.get("Conversation Log") or "")
        if "BOUNCED" not in log:
            continue
        found = set(pat.findall(log))
        if not found:                              # fall back to the row's own address
            found = {_norm(str(row.get("Contact Email") or ""))}
        for addr in found:
            addr = _norm(addr).rstrip(".,;:")
            if "@" not in addr:
                continue
            scanned += 1
            m = re.search(r"\[(\d{4}-\d{2}-\d{2})\] Contact: BOUNCED", log)
            if record(addr, when=m.group(1) if m else None):
                added += 1
    return added, scanned


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "list"
    if cmd == "seed":
        added, scanned = seed_from_tracker()
        print(f"[bounce-guard] seeded: {added} new address(es) from {scanned} bounce record(s)")
        d = load()
        print(f"[bounce-guard] blocklist now: {len(d['addresses'])} addresses, "
              f"{len(d['generic_domains'])} generic-inbox domains")
        return 0
    if cmd == "list":
        d = load()
        print(f"addresses ({len(d['addresses'])}):")
        for a, r in sorted(d["addresses"].items()):
            print(f"  {a:<45} {r.get('since','?')}")
        print(f"\ngeneric-inbox domains ({len(d['generic_domains'])}):")
        for a, r in sorted(d["generic_domains"].items()):
            print(f"  {a:<45} {r.get('since','?')}  ({r.get('bounced_local','?')}@)")
        return 0
    if cmd == "check" and len(argv) > 2:
        blocked, why = is_blocked(argv[2])
        print(f"{'BLOCKED' if blocked else 'allowed'}  {argv[2]}  {why}")
        return 1 if blocked else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
