"""Merge the same job arriving from several sources into ONE lead.

WHY. Ten boards mirror each other, so one posting reaches the pipeline several times wearing
different punctuation: "Data Analyste Import F/H - Alternance" from LinkedIn and "Data Analyste
Import - Alternance H/F" from HelloWork are the same job at Carrefour. Nothing deduplicated them,
so every later step paid for the duplicate — and during the 2026-09-19/20 screening a human read
the same posting more than once without noticing.

Deduping first also makes everything downstream cheaper, which matters because the expensive
steps (fetching a description, verifying against the employer's ATS) are per-lead.

WHAT IT MERGES ON, and why it is conservative. The key is (normalised company, normalised role)
compared for EQUALITY. Fuzzy matching was rejected deliberately: Safran runs "Alternance Data
Analyst Supply Chain MRO" and "Alternance Essai Hydromécanique Data Analyst" at the same time,
and a similarity threshold loose enough to merge the Carrefour pair is loose enough to merge those
two — which would silently delete a real opportunity. A missed merge costs one duplicate read; a
wrong merge costs an application. Near-misses are REPORTED, never merged, so the threshold can be
revisited with evidence instead of guesswork.

WHICH URL SURVIVES. The one closest to the employer. An ATS link can be verified and applied to;
an aggregator link may outlive the posting (five of five HelloWork rows checked on 2026-09-19 were
already dead). Order: employer ATS > official board (France Travail, APEC, LBA) > aggregator.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

# Gender and inclusive-writing markers. French postings append these in a dozen shapes and they
# carry no information about the job, so they must not make two copies look like two jobs.
# ⚠ THE THREE-PART FORM MUST COME FIRST. With the two-part alternative first, "H/F/X" matched
# "H/F" and left a stray "x" in the key — which is why the Chanel posting failed to merge across
# three sources (measured 2026-09-20).
_GENDER = re.compile(
    r"\(?\b[hfmx]\s*[/·.\-]\s*[hfmx]\s*[/·.\-]\s*[hfmnx]\b\)?"   # H/F/N, H/F/X, F/H/X
    r"|\(?\b[hf]\s*[/·.\-]\s*[fh]\b\)?"                            # H/F, F/H, H-F, H.F
    r"|\(?\b[mf]\s*[/·.\-]\s*[fm]\b\)?"                            # M/F, F/M
    r"|\(\s*[hfmnx]\s*\)"                                            # (H), (F), (X)
    # INCLUSIVE WRITING, every spelling treated identically. "Chargé·e" and "Chargé.e" are the
    # same word; handling them differently left an orphan "e" and blocked the MGEN merge.
    r"|[·.\-]\s*e\b|\(\s*e\s*\)|\(\s*se\s*\)|[·.]\s*se\b"
    r"|\bh\.?f\.?\b",
    re.I)

# Duration parentheticals carry no identity: BPCE's "Alternance (1 an) - Data analyste" and
# "Alternance - Data Analyste" are one job.
_DURATION = re.compile(r"\(\s*\d+\s*(?:à|a|-)?\s*\d*\s*(?:an|ans|mois|month|year)s?\s*\)", re.I)

# Some boards glue the DESCRIPTION onto the title — HelloWork served
# "Alternance - Data Analyst Objectif du Poste Missions Principales Compétences Requises…".
# Cut at the first section word a description starts with, then cap the length.
_DESC_GLUE = re.compile(
    r"\b(objectif|objectifs|missions?\s+principales|comp[ée]tences?\s+requises|profil\s+recherch|"
    r"description\s+du\s+poste|vos\s+missions|le\s+poste|qui\s+sommes)\b.*$", re.I)
_MAX_TITLE_WORDS = 14

# Legal forms and corporate noise: "Hermes Sellier SAS" and "Hermès Sellier" are one employer.
_LEGAL = re.compile(
    r"\b(s\.?a\.?s\.?u?|s\.?a\.?r\.?l|s\.?a\b|sasu|eurl|snc|gie|plc|ltd|llc|inc|gmbh|"
    r"groupe|group|holding|corporate|technologies|technology|services|solutions|"
    r"consulting|conseil|international)\b", re.I)

# Aggregator noise glued onto a company name by the board itself ("Groupe SII, super recruteur").
_BOARD_NOISE = re.compile(r",?\s*(super recruteur|recrute|recrutement|jobs?|careers?)\s*$", re.I)

# How close a URL is to the employer. Lower is better.
_URL_RANK = [
    (re.compile(r"(smartrecruiters|myworkdayjobs|workday|greenhouse|lever\.co|ashbyhq|"
                r"oraclecloud|icims|taleo|successfactors|avature|teamtailor|radancy)", re.I), 0),
    (re.compile(r"(candidat\.francetravail\.fr|apec\.fr|labonnealternance|apprentissage\.beta\.gouv)",
                re.I), 1),
    (re.compile(r"(linkedin|hellowork|adzuna|welcometothejungle|free-?work|meteojob|directemploi)",
                re.I), 3),
]
_SOURCE_RANK = {"company_boards": 0, "stationf": 1, "francetravail": 1, "france_travail": 1,
                "apec": 1, "labonnealternance": 1, "wttj": 2, "freework": 2, "free_work": 2,
                "linkedin": 3, "hellowork": 3, "adzuna": 3}


def _fold(s: str) -> str:
    """Lowercase, accent-free, punctuation collapsed to single spaces."""
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


# A TRAILING country/region token is decoration, not identity: "Chanel Fr" and "Chanel" are one
# employer, and that single missing abbreviation left the Chanel posting split 3+1 across boards
# even though all four titles produced an identical role key (found 2026-09-20 by a straggler
# test, not by the earlier precision tests).
# ⚠ Trailing ONLY, and never the whole name: a company may legitimately be called "US Robotics",
# and "fr" as a first token would be a real word in another language.
# ⚠ ABBREVIATIONS ONLY. Stripping the WORD "france" destroys brands where the country IS the
# name: "Air France" collapsed to "air" and "France Travail" to "travail" (caught 2026-09-20).
# "Fr", "EMEA", "APAC" are never part of a brand; "France", "Espana", "Italia" can be. The cost
# of the stricter rule is that "PwC France" and "PwC" stay separate — one duplicate read, which
# is the cheap error. A wrong merge deletes an opportunity, which is not.
_COUNTRY_TAIL = re.compile(r"\s+(fr|uk|usa?|eu|emea|apac|benelux|na)$", re.I)


def norm_company(name: str) -> str:
    """A company key that survives spelling, legal form, country tail and board decoration."""
    s = _BOARD_NOISE.sub("", (name or "").strip())
    s = _fold(s)
    s = _LEGAL.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    prev = None
    while prev != s:                      # "Bosch France Services Fr" -> "bosch"
        prev = s
        s = _COUNTRY_TAIL.sub("", s).strip()
    return s


def display_company(name: str) -> str:
    """The employer's name as a human would write it - board decoration removed, case kept.

    norm_company() folds and lowercases because it is a KEY. This is what goes on a CV header
    and into a letter's salutation, so "Groupe SII, super recruteur" must lose HelloWork's badge
    without becoming "groupe sii".
    """
    return _BOARD_NOISE.sub("", (name or "").strip()).strip(" ,-")


def norm_role(title: str) -> str:
    """A role key that survives gender markers, punctuation and word order noise.

    Contract words are KEPT: "Alternance Data Analyst" and "Stage Data Analyst" are different
    jobs at the same employer, and collapsing them would merge an alternance into an internship.
    """
    s = _DESC_GLUE.sub(" ", (title or ""))
    s = _DURATION.sub(" ", s)
    s = _GENDER.sub(" ", s)
    s = _fold(s)
    # A board may prefix its own contract label onto a title that already carries it
    # ("Alternance : Alternance - AI/Data Engineer" from Adzuna). Collapse the repeat.
    s = re.sub(r"\b(alternance|apprenti|apprentissage|stage)\b(\s+\1\b)+", r"\1", s)
    words = s.split()
    return " ".join(words[:_MAX_TITLE_WORDS])


def key(company: str, role: str) -> tuple[str, str]:
    return norm_company(company), norm_role(role)


def _url_rank(url: str, source: str) -> int:
    for rx, rank in _URL_RANK:
        if url and rx.search(url):
            return rank
    return _SOURCE_RANK.get((source or "").lower(), 3)


def _pick_order(lead: dict) -> tuple:
    """Total order over the copies of one job, so the surviving URL never depends on input order.

    `sorted` is stable, so ranking on closeness-to-employer ALONE left ties broken by arrival
    order: shuffling the input changed the surviving URL on 16 of 526 leads (measured 2026-09-20).
    The digest would then show a different link run to run, for no reason.

    After closeness, prefer a URL with no tracking query (Adzuna appends `?utm_medium=…&se=…`),
    then the shorter one, then the string itself — arbitrary but *stable*, which is the property
    that matters.
    """
    url = lead.get("url") or ""
    src = (lead.get("src") or lead.get("source") or "").lower()
    return (_url_rank(url, src), 1 if "?" in url else 0, len(url), url, src)


def merge(leads: list[dict]) -> tuple[list[dict], dict]:
    """Collapse duplicates. -> (merged leads, stats)

    Each merged lead keeps: the best URL, every `source` it appeared in, every URL seen, and the
    union of `meta` (a board that publishes a contract type fills a gap left by one that does not).
    Input dicts are not modified.
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    dropped_no_key = []
    for L in leads:
        c, r = key(L.get("company") or "", L.get("role") or "")
        if not c or not r:
            dropped_no_key.append(L)
            continue
        groups[(c, r)].append(L)

    out = []
    for (c, r), members in groups.items():
        members = sorted(members, key=_pick_order)
        best = dict(members[0])
        meta = {}
        for m in reversed(members):                 # earlier members win on conflict
            meta.update(m.get("meta") or {})
        best["meta"] = meta
        # BACKFILL GAPS FROM THE LOSING COPIES. The winner is chosen for the closeness of its URL,
        # not for how complete it is: a LinkedIn card carries no location and no fit score, so
        # taking its dict wholesale DELETED both from six real leads — Green-Got, CYLAD and
        # Yuri & Neil among them (measured 2026-09-20). Only empty fields are filled, in rank
        # order, so the winner's own values always stand.
        _OWNED = {"url", "src", "source", "meta", "sources", "urls", "dupes", "_key"}
        for m in members[1:]:
            for k, v in m.items():
                if k in _OWNED or v in (None, "", [], {}):
                    continue
                if best.get(k) in (None, "", [], {}):
                    best[k] = v
        best["sources"] = sorted({(m.get("src") or m.get("source") or "?") for m in members})
        best["urls"] = [u for u in dict.fromkeys(m.get("url") for m in members) if u]
        best["dupes"] = len(members)
        best["_key"] = f"{c}|{r}"
        out.append(best)

    stats = {"in": len(leads), "out": len(out) + len(dropped_no_key),
             "merged": len(leads) - len(out) - len(dropped_no_key),
             "no_key": len(dropped_no_key),
             "multi_source": sum(1 for o in out if len(o["sources"]) > 1)}
    return out + dropped_no_key, stats


def near_misses(leads: list[dict], min_overlap: float = 0.8) -> list[tuple[dict, dict, float]]:
    """Pairs at the SAME company whose roles ALMOST match — reported, never merged.

    This is the evidence channel for loosening the key later: if real duplicates keep showing up
    here, the rule can be changed deliberately rather than by intuition.
    """
    by_company: dict[str, list[dict]] = defaultdict(list)
    for L in leads:
        c = norm_company(L.get("company") or "")
        if c:
            by_company[c].append(L)
    pairs = []
    for c, members in by_company.items():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                ta, tb = set(norm_role(a.get("role") or "").split()), set(norm_role(b.get("role") or "").split())
                if not ta or not tb or ta == tb:
                    continue
                ov = len(ta & tb) / min(len(ta), len(tb))
                if ov >= min_overlap:
                    pairs.append((a, b, round(ov, 2)))
    return sorted(pairs, key=lambda p: -p[2])


if __name__ == "__main__":
    import json
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print("usage: python leadset.py <leads.json>")
        raise SystemExit(1)
    leads = json.load(open(path, encoding="utf-8"))
    merged, stats = merge(leads)
    print(f"in={stats['in']}  out={stats['out']}  merged_away={stats['merged']}  "
          f"multi_source={stats['multi_source']}  no_key={stats['no_key']}")
    for L in sorted((m for m in merged if m.get("dupes", 1) > 1),
                    key=lambda m: -m["dupes"])[:15]:
        print(f"  x{L['dupes']} {str(L.get('company'))[:26]:26} | {str(L.get('role'))[:48]:48} "
              f"| {','.join(L['sources'])}")
    nm = near_misses(leads)
    if nm:
        print(f"\nnear-misses (NOT merged) — {len(nm)}:")
        for a, b, ov in nm[:10]:
            print(f"  {ov}  {str(a.get('company'))[:20]:20} | {str(a.get('role'))[:40]:40} "
                  f"|| {str(b.get('role'))[:40]}")
