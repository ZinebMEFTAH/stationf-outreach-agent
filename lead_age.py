"""How old is a lead — the signal contacts.xlsx cannot hold.

A `Pending` row records no date. Ranking therefore treated a posting scraped in June exactly
like one scraped this morning, and with ~1,570 Pending rows against ~5 cold sends a day the
queue is roughly ten months deep: without an age, the agent will keep writing "je vous écris
au sujet de votre offre X" about roles that closed months ago. That reads as spam to the
recipient and it is spam in effect — it spends one of the day's scarce slots (and one Hunter
verification) on a job nobody can be hired into.

The 6-column schema is fixed and the VM owns contacts.xlsx (`merge=ours`), so the date lives in
a committed sidecar keyed on company+role. New rows record themselves via tracker.add_contact;
the existing backlog is recovered from git — every scrape commit is a dated snapshot of the
file, so the commit where a key first appears IS its first-seen date.

    python lead_age.py backfill      # one-time, walks the history of contacts.xlsx
    python lead_age.py stats
    python lead_age.py check "Company" "Role"
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

_PATH = Path(__file__).parent / "cache" / "lead_first_seen.json"

# A speculative pitch has no posting to expire, so it is never penalised for age (see age_bucket).
_SPECULATIVE = re.compile(r"^\s*\[suggested\]", re.I)


def key(company: str, role: str) -> str:
    """Stable key for a lead. Case/whitespace/punctuation-insensitive so a re-scrape that
    reformats a title ("Data Analyst (H/F)" → "DATA ANALYST H/F") does not mint a new lead."""
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (s or "").strip().lower()).strip()
    return f"{norm(company)}|{norm(role)}"


def load() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(d: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def record(company: str, role: str, when: str | date | None = None) -> bool:
    """Note when a lead was first seen. FIRST WRITE WINS — a re-scrape of the same posting must
    not reset its age, which is the whole point of the file. Returns True if this was new."""
    d = load()
    k = key(company, role)
    if k in d:
        return False
    if isinstance(when, date):
        when = when.isoformat()
    d[k] = (when or date.today().isoformat())[:10]
    save(d)
    return True


def first_seen(company: str, role: str) -> str | None:
    return load().get(key(company, role))


def age_days(company: str, role: str, _cache: dict | None = None) -> int | None:
    """Days since the lead first appeared, or None if unknown (never guess — an unknown age
    must stay neutral in ranking rather than be treated as old)."""
    d = _cache if _cache is not None else load()
    seen = d.get(key(company, role))
    if not seen:
        return None
    try:
        return (date.today() - datetime.strptime(seen[:10], "%Y-%m-%d").date()).days
    except ValueError:
        return None


# Bucket thresholds. French postings typically stay open 4–8 weeks; past that the odds the role
# is still open fall away, and past ~3 months a cold email about it is simply wrong.
FRESH_DAYS, AGING_DAYS, STALE_DAYS = 21, 45, 90


def age_bucket(company: str, role: str, _cache: dict | None = None) -> tuple[int, str]:
    """(score delta, reason) for rank_pending_leads. (0, "") when age is unknown or irrelevant."""
    if _SPECULATIVE.search(role or ""):
        return 0, ""            # no posting to expire — a hidden-market pitch keeps forever
    age = age_days(company, role, _cache)
    if age is None:
        return 0, ""
    if age <= FRESH_DAYS:
        return 6, f"fresh posting ({age}d)"
    if age <= AGING_DAYS:
        return 0, ""
    if age <= STALE_DAYS:
        return -10, f"posting {age}d old — may be filled"
    return -22, f"posting {age}d old — likely closed"


# ---------------------------------------------------------------------------
# One-time backfill from the history of contacts.xlsx
# ---------------------------------------------------------------------------

def backfill_from_git(verbose: bool = True) -> dict:
    """Walk every commit that touched contacts.xlsx, oldest first, and date each key by the
    commit that introduced it. Existing entries are never overwritten."""
    import io
    import pandas as pd

    out = subprocess.run(
        ["git", "log", "--reverse", "--format=%H %ad", "--date=short", "--", "contacts.xlsx"],
        capture_output=True, text=True, cwd=Path(__file__).parent, check=True).stdout
    commits = [ln.split(None, 1) for ln in out.splitlines() if ln.strip()]
    seen = load()
    added = 0
    for i, (sha, day) in enumerate(commits, 1):
        blob = subprocess.run(["git", "show", f"{sha}:contacts.xlsx"],
                              capture_output=True, cwd=Path(__file__).parent)
        if blob.returncode != 0 or not blob.stdout:
            continue
        try:
            df = pd.read_excel(io.BytesIO(blob.stdout))
        except Exception:
            continue
        for _, r in df.iterrows():
            k = key(str(r.get("Company") or ""), str(r.get("Role") or ""))
            if k != "|" and k not in seen:
                seen[k] = day
                added += 1
        if verbose:
            print(f"  [{i}/{len(commits)}] {day} {sha[:8]} → {added} dated", flush=True)
    save(seen)
    return seen


def _stats() -> None:
    import tracker
    d = load()
    df = tracker.load()
    pend = df[df["Status"].astype(str).str.strip() == "Pending"]
    buckets = {"fresh ≤21d": 0, "22-45d": 0, "46-90d": 0, ">90d": 0, "unknown": 0}
    for _, r in pend.iterrows():
        age = age_days(str(r.get("Company") or ""), str(r.get("Role") or ""), d)
        if age is None:
            buckets["unknown"] += 1
        elif age <= FRESH_DAYS:
            buckets["fresh ≤21d"] += 1
        elif age <= AGING_DAYS:
            buckets["22-45d"] += 1
        elif age <= STALE_DAYS:
            buckets["46-90d"] += 1
        else:
            buckets[">90d"] += 1
    print(f"{len(d)} leads dated · {len(pend)} Pending")
    for k, v in buckets.items():
        print(f"  {k:>10}: {v:5d}")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "stats"
    if cmd == "backfill":
        backfill_from_git()
        _stats()
    elif cmd == "check" and len(argv) >= 3:
        print(first_seen(argv[1], argv[2]), "→", age_bucket(argv[1], argv[2]))
    else:
        _stats()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
