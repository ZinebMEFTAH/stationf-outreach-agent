"""warm_network.py — the warm / referral path (the highest-yield channel).

Referrals and warm intros convert far better than cold email — Zineb's GE HealthCare interview came
through one ("candidature transmise par Stéphane Bayard"). Yet the agent is otherwise 100% cold.
This module holds Zineb's real-world connections so the agent can:
  1. rank a company where she has a contact FAR above any cold lead, and
  2. write a warm-intro-aware opener ("X m'a suggéré de vous écrire…") instead of a cold one.

There is no LinkedIn API, so Zineb populates this herself (one line per person she knows). It's a
sidecar — `cache/warm_contacts.json` — and NEVER touches the 6-column contacts.xlsx schema.

Each entry:
    {"person": "Full Name", "company": "Company", "relationship": "how she knows them + strength",
     "note": "free text (what they do, whether they'd refer her, etc.)"}
e.g. relationship = "ancienne collègue GE" / "alumni ENSIA" / "rencontré au hackathon Avignon 2025".

CLI:
    python warm_network.py add "Stéphane Bayard" "GE HealthCare" "collègue — m'a déjà référée" "..."
    python warm_network.py list
    python warm_network.py match "GE HealthCare"
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "cache" / "warm_contacts.json"


def _norm(s: str) -> str:
    """Loose company key: lowercase, drop legal suffixes & punctuation, collapse spaces. Does NOT
    strip geographic words (stripping "France" made "Air France" → "air", a false-match hazard)."""
    s = (s or "").lower()
    s = re.sub(r"[’'`.,\-_/&()]", " ", s)
    s = re.sub(r"\b(sas|sarl|sasu|sa|inc|ltd|llc|gmbh|group|groupe)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(s: str) -> set:
    return {t for t in _norm(s).split() if len(t) >= 2}


def load() -> list[dict]:
    if not _PATH.exists():
        return []
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(rows: list[dict]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def add(person: str, company: str, relationship: str = "", note: str = "") -> bool:
    """Add (or update) a known contact at a company. Returns True on write."""
    if not (person and company):
        return False
    rows = load()
    key = (_norm(person), _norm(company))
    rows = [r for r in rows if (_norm(r.get("person", "")), _norm(r.get("company", ""))) != key]
    rows.append({"person": person.strip(), "company": company.strip(),
                 "relationship": relationship.strip(), "note": note.strip()})
    _save(rows)
    return True


def match(company: str) -> list[dict]:
    """Warm contacts at a company. Token-set match (one name's tokens a subset of the other's) so
    "BNP Paribas" matches "BNP PARIBAS" but short tokens can't cause false hits (Air France≠Trustpair)."""
    ct = _tokens(company)
    if not ct:
        return []
    out = []
    for r in load():
        rt = _tokens(r.get("company", ""))
        if rt and (rt <= ct or ct <= rt):
            out.append(r)
    return out


def has_warm_contact(company: str) -> bool:
    return bool(match(company))


def companies() -> set[str]:
    """All companies where Zineb knows someone (for surfacing warm targets not yet in the pipeline)."""
    return {r.get("company", "").strip() for r in load() if r.get("company")}


def summary(company: str) -> str:
    """One-line human summary of the warm contact(s) at a company, for openers / alerts / reasons."""
    ms = match(company)
    if not ms:
        return ""
    return "; ".join(f"{m['person']}" + (f" ({m['relationship']})" if m.get("relationship") else "")
                     for m in ms)


def _main(argv) -> int:
    if not argv:
        print(__doc__); return 0
    cmd = argv[0]
    if cmd == "add" and len(argv) >= 3:
        ok = add(argv[1], argv[2], argv[3] if len(argv) > 3 else "", argv[4] if len(argv) > 4 else "")
        print("added" if ok else "failed"); return 0 if ok else 1
    if cmd == "list":
        rows = load()
        print(f"{len(rows)} warm contact(s):")
        for r in rows:
            print(f"  · {r['person']} @ {r['company']}"
                  + (f" — {r['relationship']}" if r.get("relationship") else ""))
        return 0
    if cmd == "match" and len(argv) >= 2:
        print(json.dumps(match(argv[1]), ensure_ascii=False, indent=2)); return 0
    print(__doc__); return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
