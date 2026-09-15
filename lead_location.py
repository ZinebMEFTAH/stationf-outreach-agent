"""Lead location sidecar — WHERE a posting actually is, so outreach stops chasing unreachable jobs.

Six of the boards publish a location on every offer (`jobsource.JobListing.location`), and the
scraper threw it away at insert: contacts.xlsx has a frozen 6-column schema with nowhere to put it,
so 1,768 Pending leads carry no geography at all. The digest enforces a hard Île-de-France + ~1h
commuter-ring gate before showing Zineb anything; the OUTREACH half enforced nothing, so the two
halves of the same system disagreed about which jobs she can take.

What that costs, concretely: EKTOR is a 30-person ESN in DIJON. The agent cold-emailed it, spent a
Hunter verification (the binding constraint, ~3 sends a day), drafted a LinkedIn note, and earned a
warm reply inviting an application — for an alternance she would have to move to Dijon to take,
while her M1 is at Université Paris Cité. That is the scarcest resource the system has, spent on a
job that cannot be accepted.

`config.classify_location()` already existed but reads the ROLE TITLE, and titles rarely name a
city: 931 of 940 ranked leads classified as "" and nothing used the result for scoring anyway.

Architecture: raw I/O only, same shape as lead_age.py — a committed sidecar keyed company+role,
normalised so a re-scrape that reformats a title does not mint a new entry. Absence is ALWAYS
neutral: a lead with no recorded location is never penalised, because the 1,768 rows already in the
queue have none and there is no way to recover it (unlike lead_age, which git history could
backfill — no commit ever stored a location). Only newly scraped leads carry one.

    python lead_location.py stats
    python lead_location.py check "EKTOR" "Alternant Data & IA Builder (H/F)"
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_PATH = Path(__file__).parent / "cache" / "lead_locations.json"

# Île-de-France: the 8 departments, plus Paris arrondissement codes (75001-75020).
_IDF_DEPTS = {"75", "77", "78", "91", "92", "93", "94", "95"}

# Towns inside the ~1h commuter ring that are NOT in Île-de-France, so a department code alone
# would reject them. Deliberately short and literal — a guessed ring is worse than none.
_RING = {
    "chartres", "creil", "compiegne", "beauvais", "evreux", "vernon", "dreux",
    "rouen", "amiens", "orleans", "sens", "montargis", "soissons", "chateau-thierry",
}

# Major French cities that are unambiguously outside the commuter ring. A bare city name with no
# postal code is common on job boards, and without this "Bordeaux" classified as unknown, i.e.
# neutral. Only cities whose name cannot plausibly refer to an Île-de-France commune are listed —
# a false "far" would bury a workable lead, so the list stays conservative and explicit.
_FAR_CITIES = {
    "lyon", "marseille", "bordeaux", "toulouse", "lille", "nantes", "nice", "strasbourg",
    "rennes", "montpellier", "dijon", "grenoble", "brest", "angers", "clermont-ferrand",
    "le mans", "aix-en-provence", "tours", "limoges", "besancon", "besançon", "metz", "nancy",
    "perpignan", "caen", "avignon", "nimes", "nîmes", "saint-etienne", "saint-étienne",
    "toulon", "chalon-sur-saone", "chalon-sur-saône", "lons-le-saunier", "pau", "bayonne",
}

_REMOTE = re.compile(r"\b(t[ée]l[ée]travail|remote|100\s*%\s*distanciel|full\s*remote|à distance)\b", re.I)


def key(company: str, role: str) -> str:
    """Stable key, identical in spirit to lead_age.key so the two sidecars stay aligned."""
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


def record(company: str, role: str, location: str | None) -> bool:
    """Store the board's own location string. FIRST WRITE WINS, like lead_age.

    Returns True if this was new. An empty location writes nothing: "unknown" and "recorded as
    nothing" must stay distinguishable, or a board that omits the field would look like a verdict.
    """
    loc = (location or "").strip()
    if not loc:
        return False
    d = load()
    k = key(company, role)
    if k in d:
        return False
    d[k] = loc[:120]
    save(d)
    return True


def get(company: str, role: str) -> str | None:
    return load().get(key(company, role))


def classify(location: str | None) -> str:
    """'idf' | 'ring' | 'remote' | 'far' | 'unknown' — how reachable the posting is from home.

    'remote' wins over any place name: a remote role in Lyon is workable from Île-de-France, and
    that is the whole point of checking. 'unknown' is returned for anything unrecognised, and
    callers must treat it as neutral — never as 'far'.
    """
    s = (location or "").strip()
    if not s:
        return "unknown"
    if _REMOTE.search(s):
        return "remote"
    low = s.lower()
    # A French postal/department code anywhere in the string ("75 - PARIS", "Lyon - 69", "69003").
    codes = re.findall(r"\b(\d{2})\d{0,3}\b", s)
    if codes:
        if any(c in _IDF_DEPTS for c in codes):
            return "idf"
        # A code we recognised as a real department, and it is not Île-de-France.
        if any(c.isdigit() and "01" <= c <= "95" for c in codes):
            return "ring" if any(t in low for t in _RING) else "far"
    if "paris" in low or "île-de-france" in low or "ile-de-france" in low:
        return "idf"
    if any(t in low for t in _RING):
        return "ring"
    if any(c in low for c in _FAR_CITIES):
        return "far"
    return "unknown"


def reachability(company: str, role: str) -> str:
    """classify() for a stored lead. 'unknown' when nothing was recorded — always neutral."""
    return classify(get(company, role))


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "check" and len(args) >= 3:
        loc = get(args[1], args[2])
        print(f"{args[1]} · {args[2]}\n  location: {loc or '(not recorded)'}\n  → {classify(loc)}")
    else:
        import collections
        d = load()
        c = collections.Counter(classify(v) for v in d.values())
        print(f"{len(d)} leads with a recorded location")
        for k, n in c.most_common():
            print(f"  {k:8} {n:>5}")
        if not d:
            print("  (empty — locations are recorded from the next /scrape onward;\n"
                  "   existing rows cannot be backfilled, no commit ever stored one)")
