"""school_partners.py — the school / CFA alternance channel.

French alternance is heavily school-mediated: companies that partner with Zineb's admitted M1
programs (and their CFA) actively recruit apprentices FROM those programs. That makes a company
reachable for alternance even when a cold email would die in its ATS — the legitimate angle is
"je rejoins le M1 <programme> à <école>, dont <Company> est partenaire (via le CFA numiA)".

Key nuance: most school/CFA partners are large employers (SNCF, BNP Paribas, Société Générale, …),
which the cold-email ranking DOWN-ranks (an unreachable cold inbox). The school tie doesn't make
them cold-emailable — it makes them applyable through the school/CFA + a school-aware application.
So /daily-agent routes a big-corp school-partner to the APPLICATION path (portal + /cover-letter with
the school angle), and only small school-partner startups get a warm cold email with the school opener.

Data lives in a sidecar (cache/school_partners.json) — PUBLIC info (company + program), not personal,
so it's committed (unlike warm_contacts.json). Seeded from research; grow it as Zineb learns more.

Each entry: {"company", "program" (which of her M1s), "source" (CFA / school), "note"}.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "cache" / "school_partners.json"


def _norm(s: str) -> str:
    # Only strip legal suffixes/punctuation — NOT geographic words like "France"/"Paris" (stripping
    # them turned "Air France" into "air", which then substring-matched "Trustpair"/"Vestiaire").
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


def add(company: str, program: str = "", source: str = "", note: str = "") -> bool:
    if not company:
        return False
    rows = [r for r in load() if _norm(r.get("company", "")) != _norm(company)]
    rows.append({"company": company.strip(), "program": program.strip(),
                 "source": source.strip(), "note": note.strip()})
    _save(rows)
    return True


def match(company: str) -> list[dict]:
    """School/CFA partner entries for a company. Token-set match (one name's tokens a subset of the
    other's) — so "BNP Paribas" matches "BNP PARIBAS" but "Air France" never matches "Trustpair"."""
    ct = _tokens(company)
    if not ct:
        return []
    out = []
    for r in load():
        rt = _tokens(r.get("company", ""))
        if rt and (rt <= ct or ct <= rt):
            out.append(r)
    return out


def is_partner(company: str) -> bool:
    return bool(match(company))


def summary(company: str) -> str:
    """One-line summary of the school/CFA tie for a company (for openers / cover letters / reasons)."""
    ms = match(company)
    if not ms:
        return ""
    m = ms[0]
    via = m.get("source") or m.get("program") or "mon programme"
    prog = f" ({m['program']})" if m.get("program") else ""
    return f"partenaire alternance via {via}{prog}"


def companies() -> set[str]:
    return {r.get("company", "").strip() for r in load() if r.get("company")}


# ── Seed: partners of Zineb's ACCEPTED program — Paris Cité MLSD (researched July 2026) ───────────
# Zineb is accepted to the M1 "Machine Learning pour la Science des Données" (MLSD) at Université
# Paris Cité, run in apprenticeship through CFA Afia (now numiA). CFA Afia/numiA was founded and is
# managed by the companies below (they host its apprentices), so they are MLSD's real host partners —
# reachable for alternance THROUGH the school/CFA even when a cold email would die in their ATS. The
# "partenaire via le CFA" opener is truthful only for these; do not add companies without a real tie.
_SEED = [
    # source, program, companies
    ("CFA Afia / numiA", "Paris Cité MLSD", [
        "Air France", "AXA", "BNP Paribas", "Capgemini", "CGI", "Ekino", "Expleo",
        "Informatique CDC", "Société Générale", "Sopra Steria", "Viveris",
        "EDF", "Orange", "Thales"]),
]


def seed(force: bool = False) -> int:
    """Populate cache/school_partners.json from the researched seed. No-op if already populated
    unless force=True. Returns the number of entries written."""
    if load() and not force:
        return 0
    rows = []
    for source, program, cos in _SEED:
        for c in cos:
            rows.append({"company": c, "program": program, "source": source,
                         "note": "alternance via l'école/CFA (recrute des alternants du programme)"})
    _save(rows)
    return len(rows)


if __name__ == "__main__":
    import sys
    a = sys.argv[1:]
    if a and a[0] == "seed":
        print("seeded", seed(force="--force" in a), "entries")
    elif a and a[0] == "list":
        for r in load():
            print(f"  · {r['company']:20} — {r.get('source','')} ({r.get('program','')})")
    elif a and a[0] == "match" and len(a) > 1:
        print(json.dumps(match(a[1]), ensure_ascii=False, indent=2))
    else:
        print(__doc__)
