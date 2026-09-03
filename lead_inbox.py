"""Hand-picked lead queue — the way a human adds companies to the pipeline.

WHY THIS EXISTS. `contacts.xlsx` is marked `merge=ours` and the VM runs `git merge -X ours`,
so the VM's copy always wins: rows added on a dev machine are silently discarded on its next
pull. Every automated source writes to the tracker *from the VM*, but there was no path at all
for "Zineb found these twenty companies herself and wants them worked". This is that path — a
small committed queue the VM drains into the tracker on its own runs.

WHAT IT REFUSES TO DO. It never invents an email domain. Slugifying a company name into
`contact@<name>.com` is exactly what produced the August 2026 bounce spike (55 bounces,
`contact@` at domains that resolved but had no such mailbox). An entry is only promoted into
`contacts.xlsx` once a REAL domain has been resolved and its MX records answer. Anything that
cannot be resolved stays queued and is retried on the next run, so nothing is lost and no
guess ever enters the tracker.

The queue is COMMITTED (unlike most of cache/): the whole point is that the VM sees it.
It holds company names and public postal addresses — no personal data.

Usage:
  python lead_inbox.py add "QUANDELA" --location "91120 PALAISEAU" --sector "quantique"
  python lead_inbox.py list
  python lead_inbox.py drain            # dry-run: show what would be added
  python lead_inbox.py drain --apply    # resolve + insert into contacts.xlsx
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

_PATH = Path(__file__).parent / "cache" / "lead_inbox.json"

# Default role text for a speculative pitch. The `[Suggested]` prefix is the schema's marker
# for "we approached them, they never posted" (see CLAUDE.md).
DEFAULT_ROLE = "[Suggested] Alternance IA / Data"
# Give up asking Clearbit after this many drains and hand the company to a human. Without a
# ceiling an unresolvable name is re-queried on every single run, forever.
MAX_ATTEMPTS = 6


def _load() -> list[dict]:
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(rows: list[dict]) -> None:
    _PATH.parent.mkdir(exist_ok=True)
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_PATH)


def _key(company: str) -> str:
    return " ".join(str(company or "").split()).strip().lower()


def add(company: str, *, location: str = "", sector: str = "", domain: str = "",
        role: str = DEFAULT_ROLE, source: str = "manual", note: str = "") -> bool:
    """Queue one company. Returns False if it is already queued."""
    if not str(company or "").strip():
        return False
    rows = _load()
    if any(_key(r.get("company")) == _key(company) for r in rows):
        return False
    rows.append({
        "company": " ".join(company.split()).strip(),
        "location": location, "sector": sector, "domain": domain.strip().lower(),
        "role": role, "source": source, "note": note,
        "status": "queued", "attempts": 0, "queued_on": date.today().isoformat(),
    })
    _save(rows)
    return True


def close(company: str, *, status: str = "Rejected", reason: str = "") -> bool:
    """Queue "this lead is dead" — an outcome Zineb learned OFF-system.

    Interviews happen, calls happen, people say no in person. None of that reaches
    contacts.xlsx, so a lead that is actually over keeps being surfaced as a warm thread to
    re-engage (Haliro sat at the top of the nudge list after the interview had already gone
    badly). She cannot fix the row herself either — `merge=ours` means the VM discards it — so
    the correction has to travel the same queue as a new lead.
    """
    rows = _load()
    rows.append({
        "company": " ".join(str(company).split()).strip(),
        "action": "close", "new_status": status, "reason": reason,
        "status": "queued", "attempts": 0, "queued_on": date.today().isoformat(),
    })
    _save(rows)
    return True


def note(company: str, message: str, *, role: str | None = None,
         status: str | None = None) -> bool:
    """Queue an off-system interaction to be written into the Conversation Log ON the VM.

    Anything that happens outside the pipeline — a call, an interview, an email sent by hand
    from Gmail — is invisible to the tracker, which then mis-times follow-ups and trains the
    learners on an incomplete record. Same queue as `add`/`close`, for the same reason: the
    Mac cannot write contacts.xlsx.
    """
    rows = _load()
    rows.append({
        "company": " ".join(str(company).split()).strip(),
        "action": "note", "message": message, "role": role, "new_status": status,
        "status": "queued", "attempts": 0, "queued_on": date.today().isoformat(),
    })
    _save(rows)
    return True


def _resolve(entry: dict) -> tuple[str | None, str]:
    """(domain, why). A domain is only returned when it is REAL — MX records answer."""
    import email_verify
    candidate = (entry.get("domain") or "").strip().lower()
    why = "supplied with the entry"
    if not candidate:
        try:
            import company_resolver
            candidate = company_resolver.resolve_domain(entry["company"]) or ""
            why = "resolved from the company name"
        except Exception as e:
            return None, f"resolver failed ({type(e).__name__})"
    if not candidate:
        return None, "no domain found — needs a human to supply one"
    try:
        ok = email_verify.check_mx(candidate)[0]
    except Exception:
        ok = False
    if not ok:
        return None, f"{candidate} has no MX records — not a working mail domain"
    return candidate, why


def drain(apply: bool = False, limit: int | None = None) -> dict:
    """Promote queued companies into contacts.xlsx. Dry-run unless ``apply``."""
    import tracker
    df = tracker.load()          # one read for the whole drain, not one per entry
    rows = _load()
    added, skipped, unresolved, existing = [], [], [], []
    touched = False
    closed, noted = [], []
    for entry in rows:
        if entry.get("status") == "added":
            continue
        if entry.get("action") == "note":
            company = entry["company"]
            hits = df["Company"].fillna("").astype(str).str.strip().str.lower() == company.lower()
            if entry.get("role"):
                hits &= df["Role"].fillna("").astype(str).str.strip().str.lower() \
                    == str(entry["role"]).strip().lower()
            if apply and hits.any():
                for i in df.index[hits]:
                    line = f"[{date.today().isoformat()}] Agent: {entry['message']}"
                    cur = str(df.at[i, "Conversation Log"] or "").strip()
                    df.at[i, "Conversation Log"] = f"{cur} \n {line}" if cur else line
                    df.at[i, "Last Interaction Date"] = date.today().isoformat()
                    if entry.get("new_status"):
                        df.at[i, "Status"] = entry["new_status"]
                tracker.save(df)
                entry["status"], touched = "added", True
            noted.append((company, int(hits.sum())))
            continue
        if entry.get("action") == "close":
            company = entry["company"]
            new_status = entry.get("new_status") or "Rejected"
            hits = df["Company"].fillna("").astype(str).str.strip().str.lower() == company.lower()
            if apply and hits.any():
                for i in df.index[hits]:
                    note = (f"[{date.today().isoformat()}] Agent: closed by Zineb — "
                            f"{entry.get('reason') or 'no longer live'}")
                    cur = str(df.at[i, "Conversation Log"] or "").strip()
                    df.at[i, "Conversation Log"] = f"{cur} \n {note}" if cur else note
                    df.at[i, "Status"] = new_status
                tracker.save(df)
                entry["status"], touched = "added", True
            closed.append((company, new_status, int(hits.sum())))
            continue
        if limit is not None and len(added) + len(unresolved) >= limit:
            break
        company, role = entry["company"], entry.get("role") or DEFAULT_ROLE
        if tracker.row_exists(df, company, "", role):
            existing.append(company)
            if apply:
                entry["status"], touched = "added", True
            continue
        domain, why = _resolve(entry)
        if not domain:
            unresolved.append((company, why))
            if apply:
                entry["attempts"] = int(entry.get("attempts", 0)) + 1
                if entry["attempts"] >= MAX_ATTEMPTS:
                    entry["status"] = "needs_human"
                touched = True
            continue
        email = f"contact@{domain}"
        log = (f"[{date.today().isoformat()}] Agent: queued by hand from "
               f"{entry.get('source') or 'manual'}"
               + (f" — {entry['location']}" if entry.get("location") else "")
               + (f" — {entry['sector']}" if entry.get("sector") else "")
               + f"; domain {domain} ({why}), MX verified. Generic inbox — /find-contacts "
                 f"should upgrade it to a named decision-maker before any send.")
        if apply:
            ok = tracker.add_contact(company=company, role=role, contact_email=email,
                                     status="Pending", conversation_log=log)
            entry["status"] = "added" if ok else "queued"
            entry["domain"] = domain
            touched = True
            (added if ok else skipped).append(company)
        else:
            added.append(company)
    if apply and touched:
        _save(rows)
    return {"added": added, "closed": closed, "noted": noted, "already_in_tracker": existing,
            "unresolved": unresolved, "skipped": skipped,
            "still_queued": sum(1 for r in rows if r.get("status") == "queued"),
            "needs_human": [r["company"] for r in rows if r.get("status") == "needs_human"]}


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "list"
    if cmd == "add":
        import argparse
        ap = argparse.ArgumentParser(prog="lead_inbox.py add")
        ap.add_argument("company")
        ap.add_argument("--location", default="")
        ap.add_argument("--sector", default="")
        ap.add_argument("--domain", default="", help="skip resolution if you already know it")
        ap.add_argument("--role", default=DEFAULT_ROLE)
        ap.add_argument("--source", default="manual")
        a = ap.parse_args(argv[2:])
        ok = add(a.company, location=a.location, sector=a.sector, domain=a.domain,
                 role=a.role, source=a.source)
        print(("queued: " if ok else "already queued: ") + a.company)
        return 0
    if cmd == "drain":
        apply = "--apply" in argv
        r = drain(apply=apply)
        for c in r["added"]:
            print(f"  {'ADDED  ' if apply else 'would add'} {c}")
        for c in r["already_in_tracker"]:
            print(f"  already in tracker  {c}")
        for c, n in r.get("noted", []):
            print(f"  {'NOTED  ' if apply else 'would note'} {c:30} ({n} row(s))")
        for c, st, n in r.get("closed", []):
            print(f"  {'CLOSED ' if apply else 'would close'} {c:30} -> {st} ({n} row(s))")
        for c, why in r["unresolved"]:
            print(f"  UNRESOLVED  {c:38} {why}")
        print(f"\n[lead_inbox] {'added' if apply else 'would add'} {len(r['added'])} | "
              f"unresolved {len(r['unresolved'])} | still queued {r['still_queued']}"
              + (f" | needs a human: {', '.join(r['needs_human'])}" if r["needs_human"] else ""))
        if not apply:
            print("[lead_inbox] dry-run — nothing written. Re-run with --apply.")
        return 0
    rows = _load()
    for r in rows:
        print(f"  [{r.get('status','?'):11}] {r['company'][:38]:40} "
              f"{r.get('domain') or '—':26} {r.get('location','')}")
    print(f"{len(rows)} queued lead(s) in {_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
