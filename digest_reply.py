#!/usr/bin/env python3
"""Turn a reply to the opportunity digest into leads the outreach agent will chase.

Every digest ends with "reply with any you want the outreach agent to chase" — a promise nothing
kept. `imap_fetch.sync()` only matches mail whose sender is an existing lead, so a reply from
Zineb's own address was fetched, matched nothing, and dropped on the floor. She could ask for a
company to be chased and the system would silently ignore her.

This closes that loop. She replies to the digest keeping the lines she wants (quoting is fine —
mail clients quote the whole thing, so only URLs she left in the *unquoted* part count), and each
offer becomes a Pending lead that `/daily-agent` picks up in its next run.

Offer URLs are resolved back to a company and role through `cache/opportunities_seen.json`, which
`opportunities.record_seen()` already writes for everything she has been shown — so the digest's
own history is the index, and there's nothing extra to keep in sync.

Dry-run by default: it prints what it would add and changes nothing until `--apply`.

Usage:
  python digest_reply.py                  # dry-run: show what a reply asked for
  python digest_reply.py --apply          # actually add the leads as Pending
  python digest_reply.py --since-days 5   # look further back in the inbox
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import config

_SEEN_PATH = Path(__file__).parent / "cache" / "opportunities_seen.json"

# Her own addresses — a digest reply comes from her, which is exactly why imap_fetch ignores it.
_HER_ADDRESSES = {
    (config.INTERNAL_ALERT_EMAIL or "").lower().strip(),
    (config.EMAIL_ADDRESS or "").lower().strip(),
} - {""}

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")

# Lines a mail client adds when quoting. Everything from the first of these onward is the digest
# she's replying TO, not a request — otherwise every reply would ask for all 30 offers back.
_QUOTE_MARKERS = (
    re.compile(r"^\s*>", re.M),
    re.compile(r"^\s*On .{0,80}\bwrote:\s*$", re.M | re.I),
    re.compile(r"^\s*Le .{0,80}\ba écrit\s*:\s*$", re.M | re.I),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}\s*$", re.M | re.I),
    re.compile(r"^\s*_{10,}\s*$", re.M),
)


def strip_quoted(body: str) -> str:
    """The part she actually typed — everything before the quoted digest begins."""
    cut = len(body or "")
    for rx in _QUOTE_MARKERS:
        m = rx.search(body or "")
        if m:
            cut = min(cut, m.start())
    return (body or "")[:cut]


def _seen_index() -> dict[str, dict]:
    """URL -> {company, role} for every offer the digest has shown her."""
    try:
        return json.loads(_SEEN_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a missing/corrupt cache just means nothing to match
        return {}


def parse_wanted(body: str) -> list[dict]:
    """Offers she asked for, newest-quoted-first, deduped by URL.

    Only the unquoted part of the reply is read, so forwarding the digest back untouched asks for
    nothing — she has to leave a link in her own text for it to count.
    """
    index = _seen_index()
    out, seen = [], set()
    for url in _URL_RE.findall(strip_quoted(body)):
        url = url.rstrip(".,;)")
        if url in seen:
            continue
        seen.add(url)
        hit = index.get(url)
        if not hit:
            continue  # a link she pasted that the digest never sent — not an offer we know
        out.append({"url": url, "company": hit.get("company", ""), "role": hit.get("role", "")})
    return out


def promote(wanted: list[dict], apply: bool = False) -> tuple[list[dict], list[dict]]:
    """Add each wanted offer as a Pending lead. Returns (added, skipped).

    Every active row must carry an email (preflight enforces it), so a company whose domain can't
    be resolved is reported as skipped rather than written as a broken row.
    """
    import tracker

    added, skipped = [], []
    for w in wanted:
        company, role = w["company"], w["role"]
        if not company or not role:
            skipped.append({**w, "reason": "no company/role in the digest history"})
            continue
        if tracker.is_junk_company(company):
            skipped.append({**w, "reason": "job-board name, not a real employer"})
            continue

        domain = None
        try:
            import company_resolver
            domain = company_resolver.resolve_domain(company)
        except Exception as e:  # noqa: BLE001 — resolution is best-effort, never fatal
            skipped.append({**w, "reason": f"domain lookup failed: {type(e).__name__}"})
            continue
        if not domain:
            skipped.append({**w, "reason": "could not resolve a company domain"})
            continue

        email = f"contact@{domain}"
        if not apply:
            added.append({**w, "email": email, "dry_run": True})
            continue
        if tracker.add_contact(company=company, role=role, contact_email=email, status="Pending"):
            added.append({**w, "email": email})
        else:
            skipped.append({**w, "reason": "already in the tracker"})
    return added, skipped


def from_inbox(since_days: int = 3) -> list[dict]:
    """Offers requested in any digest reply she sent in the last `since_days`."""
    import imap_fetch

    wanted, seen = [], set()
    for r in imap_fetch.fetch_recent_replies(since_days=since_days):
        if (r.sender or "").lower().strip() not in _HER_ADDRESSES:
            continue
        for w in parse_wanted(r.body or ""):
            if w["url"] in seen:
                continue
            seen.add(w["url"])
            wanted.append(w)
    return wanted


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Promote digest replies into Pending leads")
    ap.add_argument("--apply", action="store_true", help="actually add the leads (default: dry-run)")
    ap.add_argument("--since-days", type=int, default=3, dest="since_days")
    args = ap.parse_args(argv)

    wanted = from_inbox(since_days=args.since_days)
    if not wanted:
        print(f"[digest-reply] no digest replies with known offer links in the last "
              f"{args.since_days} day(s).")
        return 0

    added, skipped = promote(wanted, apply=args.apply)
    print(f"[digest-reply] {len(wanted)} offer(s) requested")
    for a in added:
        mark = "would add" if not args.apply else "ADDED"
        print(f"  ✅ {mark}: {a['company']} — {a['role']}  <{a['email']}>")
    for s in skipped:
        print(f"  ⏭  skipped: {s['company'] or s['url']} — {s['reason']}")
    if not args.apply:
        print("\n[digest-reply] dry-run: nothing written. Re-run with --apply to add these.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
