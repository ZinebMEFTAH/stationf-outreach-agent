"""Measure what each source is actually worth, and learn which queries work on it.

WHY. The repo had ten boards and no way to answer two obvious questions: *is this source still
working?* and *which queries should I send it?* Both were answered by hand, once, and then never
revisited — which is how HelloWork ran for months on three queries while a wider set returned 38%
more (measured 2026-09-19), and how a dead API would have gone unnoticed until a human spotted
thin results.

TWO THINGS, deliberately in one module because the second needs the first:

    health(source)                 — does it answer at all, and with what?
    measure(source, query)         — what does THIS query yield on THIS source?
    rank(source, candidates)       — try several, sorted by yield
    best(source)                   — what previous runs learned, from cache
    tune(source, candidates)       — measure new queries, keep what beats the incumbents

WHAT "GOOD" MEANS HERE. Not raw row count — a query returning 100 rows of "Employé de rayon" is
worth less than one returning 8 alternance data roles in Île-de-France. `measure()` scores what
Zineb can actually apply to: role-matching (jobsource), alternance, Île-de-France, and NOT a
school. Raw volume is reported but never scored, because optimising for it is how a source ends
up flooding the digest with noise.

THE POINT OF THE CACHE. Query yield changes as the market moves — "alternance IA" was worth
little in July and a lot in September. Measurements land in cache/query_yield.json with a date,
so `best()` reflects what works NOW rather than what someone typed once. That file is committed:
it is knowledge, not state.
"""
from __future__ import annotations

import importlib
import json
import re
import statistics
import time
from datetime import date
from pathlib import Path

import jobsource as js

_CACHE = Path(__file__).parent / "cache" / "query_yield.json"
# After this many days a measurement stops being treated as evidence. The market
# moves seasonally (July vs September on the same query), so ~2 months is the
# horizon over which a number still describes today's board.
_STALE_DAYS = 60

_ALT = re.compile(r"\b(alternan[ct]e?|apprenti\w*|apprentice\w*|work[- ]study)\b", re.I)
_IDF = re.compile(
    r"\b(paris|ile[- ]de[- ]france|île[- ]de[- ]france|hauts[- ]de[- ]seine|seine[- ]saint[- ]denis|"
    r"val[- ]de[- ]marne|yvelines|essonne|val[- ]d.oise|seine[- ]et[- ]marne|"
    r"\b7[5]\d{3}|\b9[12348]\d{3}|\b7[78]\d{3}|\b95\d{3})\b", re.I)


# --------------------------------------------------------------------------- adapters
# Each adapter turns ONE query + ONE page into a flat list of (role, company, location).
# They exist because every board returns a different shape, and the lab must not care.

def _a_hellowork(m, q, p):
    return [(r.get("role", ""), r.get("company", ""), r.get("location", ""))
            for r in m._http_search(q, p + 1)]


def _a_linkedin(m, q, p):
    return [(r.get("role", ""), r.get("company", ""), r.get("location", ""))
            for r in m._search(q, p * 10)]


def _a_free_work(m, q, p):
    out = []
    for o in m._search(q, p + 1) or []:
        c = o.get("company") or {}
        out.append((o.get("title", ""), (c.get("name") if isinstance(c, dict) else c) or "",
                    str(o.get("location") or "")))
    return out


def _a_apec(m, q, p):
    d = m._search(q, p * getattr(m, "PER_PAGE", 20)) or {}
    out = []
    for o in d.get("resultats") or []:
        # APEC CODES the contract (597137 alternance / 597139 contrat pro) and publishes no
        # legend. Reading the title instead reported ZERO alternances for the query
        # "alternance data" — see the 2026-09-20 health run.
        code = str(o.get("typeContrat") or o.get("idTypeContrat") or "")
        meta = {"contract": "alternance"} if code in ("597137", "597139") else {}
        out.append((o.get("intitule", ""), (o.get("nomCommercial") or o.get("nomClient") or ""),
                    o.get("lieuTexte") or "", meta))
    return out


def _a_france_travail(m, q, p):
    tok = m._get_token()
    d = m._search(tok, q, p * getattr(m, "PER_PAGE", 20)) or {}
    out = []
    for o in d.get("resultats") or []:
        ent = o.get("entreprise") or {}
        lieu = o.get("lieuTravail") or {}
        # France Travail ships an `alternance` BOOLEAN on every offer — true on many whose titles
        # never use the word.
        meta = {"contract": "alternance"} if o.get("alternance") else {}
        out.append((o.get("intitule", ""), ent.get("nom") or "", lieu.get("libelle") or "", meta))
    return out


def _a_adzuna(m, q, p):
    d = m._search(q, p + 1) or {}
    out = []
    for o in d.get("results") or []:
        c = o.get("company") or {}
        loc = o.get("location") or {}
        out.append((o.get("title", ""), c.get("display_name", ""), loc.get("display_name", "")))
    return out


def _a_wttj(m, q, p):
    d = m._algolia_query(q, p) or {}
    out = []
    for h in d.get("hits") or []:
        org = h.get("organization") or {}
        names = " ".join(h.get("contract_type_names") or []) or (h.get("contract_type") or "")
        meta = {"contract": "alternance"} if _ALT.search(names) else {}
        out.append((h.get("name", ""), org.get("name", ""), m._location_of(h), meta))
    return out


def _a_remotive(m, q, p):
    if p:                      # remotive has no pagination; one page only
        return []
    return [(o.get("title", ""), o.get("company_name", ""),
             o.get("candidate_required_location", "")) for o in m._search(q) or []]


def _a_indeed(m, q, p):
    # Browser-backed, so it is SLOW compared with the API sources — one chromium launch per
    # call. It belongs here anyway: without an adapter, `plan()` was a permanent no-op for
    # Indeed, asking for a tuned order that nothing could ever measure.
    return [(r.get("role", ""), r.get("company", ""), r.get("location", ""))
            for r in m.search(q, p)]


ADAPTERS = {
    "hellowork": _a_hellowork,
    "indeed": _a_indeed,
    "linkedin": _a_linkedin,
    "free_work": _a_free_work,
    "apec": _a_apec,
    "france_travail": _a_france_travail,
    "adzuna": _a_adzuna,
    "wttj": _a_wttj,
    "remotive": _a_remotive,
}

# Sources with no free-text query: they are driven by ROME codes or a fixed catalogue, so a
# query-tuning lab has nothing to tune. Listed explicitly so "missing" never looks like "broken".
NO_QUERY = {"labonnealternance", "stationf", "company_boards"}


# --------------------------------------------------------------------------- measuring

# A source's NAME is usually its module name, but not always — "indeed" lives in
# browser_boards because the browser machinery is shared. Anything resolving a source to a
# module must go through here, or it fails with ModuleNotFoundError on exactly the sources
# that are not named after their file.
MODULE_OF = {"indeed": "browser_boards"}


def module_for(source: str):
    """The module implementing a source. The one place that mapping lives."""
    return importlib.import_module(MODULE_OF.get(source, source))


def search(source: str, query: str, page: int = 0) -> list[tuple[str, str, str]]:
    """(role, company, location) for one query+page on one source. Raises on a broken source."""
    if source not in ADAPTERS:
        raise KeyError(f"{source}: no adapter (query-less source?)")
    rows = ADAPTERS[source](module_for(source), query, page)
    # Adapters may return 3- or 4-tuples; the 4th is the board's OWN contract signal.
    return [r if len(r) == 4 else (r[0], r[1], r[2], {}) for r in rows]


def measure(source: str, query: str, pages: int = 2) -> dict:
    """What this query is WORTH on this source — not how many rows it returns.

    `score` counts only what she could act on: role-matching, alternance, Île-de-France, not a
    school. Raw volume is reported for context and deliberately not scored.
    """
    import tracker
    raw = fit = alt = idf = school = 0
    t0 = time.time()
    err = ""
    for p in range(pages):
        try:
            rows = search(source, query, p)
        except Exception as e:                       # noqa: BLE001
            err = f"{type(e).__name__}: {str(e)[:60]}"
            break
        if not rows:
            break
        for role, company, loc, meta in rows:
            raw += 1
            if tracker.is_training_body(company):
                school += 1
                continue
            if not js.matches_target_role(role) or js.excluded_role(role):
                continue
            fit += 1
            # PREFER THE BOARD'S OWN FIELD to anything inferred from a title — the same rule
            # fit_score follows. Reading titles alone scored APEC at zero alternances for the
            # query "alternance data", which is plainly wrong: APEC codes the contract instead.
            is_alt = meta.get("contract") == "alternance" or bool(_ALT.search(role))
            in_idf = bool(_IDF.search(loc or ""))
            alt += is_alt
            idf += in_idf
            if is_alt and in_idf:
                pass
        time.sleep(0.3)
    # A role-fit alternance in Île-de-France is the thing she can apply to; weight accordingly.
    score = fit + 2 * alt + idf
    return {"source": source, "query": query, "pages": pages, "raw": raw, "role_fit": fit,
            "alternance": alt, "idf": idf, "schools": school, "score": score,
            "secs": round(time.time() - t0, 1), "error": err, "measured": date.today().isoformat()}


def health(source: str) -> dict:
    """Is this source answering at all? A neutral probe, so a zero means the source, not the query."""
    probe = "alternance data" if source not in ("remotive", "wttj") else "data engineer"
    m = measure(source, probe, pages=1)
    m["ok"] = bool(m["raw"]) and not m["error"]
    return m


# --------------------------------------------------------------------------- learning

def _load() -> dict:
    try:
        return json.loads(_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d: dict) -> None:
    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True),
                          encoding="utf-8")
    except Exception:
        pass                                   # a cache write must never break a run


def rank(source: str, queries: list[str], pages: int = 2, remember: bool = True) -> list[dict]:
    """Measure each query on this source, best first, and remember what was learned."""
    got = [measure(source, q, pages) for q in queries]
    got.sort(key=lambda m: -m["score"])
    if remember:
        d = _load()
        d.setdefault(source, {})
        for m in got:
            # `pages` MUST be stored: score scales with volume, so the same query measured over
            # 2 pages outscores itself measured over 1 (67 vs 74 on WTTJ). Without it a later
            # partial sweep at a different depth would silently rank the DEEPER measurement above
            # the better query.
            d[source][m["query"]] = {k: m[k] for k in
                                     ("score", "raw", "role_fit", "alternance", "idf", "pages",
                                      "measured")}
        _save(d)
    return got


def best(source: str, top: int = 10, min_score: int = 1) -> list[str]:
    """The queries previous runs found most productive here. Empty when nothing is known yet."""
    known = _load().get(source, {})
    ranked = sorted(known.items(), key=lambda kv: -kv[1].get("score", 0))
    return [q for q, v in ranked[:top] if v.get("score", 0) >= min_score]


def plan(source: str, seeds: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The query list a source should send today: ITS OWN seeds, reordered by measurement.

    `seeds` is a list of the source's own (label, query) PAIRS — exactly what its loop already
    iterates, `list(ALTERNANCE_QUERIES.items()) + list(QUERIES.items())`. It must be pairs and
    not a dict: those two dicts SHARE LABELS ("ai", "backend", "data"), so merging them into one
    dict silently drops the alternance query for each collision, which is why the modules
    concatenate the item lists in the first place. Labels come back untouched — LinkedIn derives
    a listing's category from one (`category.rstrip("0123456789")`), so they are load-bearing.

    THIS IS WHAT MAKES SEARCHING IMPROVE INSTEAD OF REPEAT. The hand-written lists were typed
    once and never revisited, and measurement showed THE BEST QUERY IS DIFFERENT ON EVERY BOARD:
    "apprenti data" scores 46 on HelloWork and 0 on WTTJ; "alternance MLOps" 34 on LinkedIn and 0
    on HelloWork; "alternance IA" 67 on WTTJ against 29 on HelloWork. One list copied across
    boards is therefore wrong nearly everywhere, and for a source on a call budget (Adzuna is
    ~12 calls a night, and pages_per_query bounds the others) the ORDER decides what that budget
    actually buys.

    ⚠ IT REORDERS; IT NEVER DROPS, AND IT NEVER INVENTS. Query yield moves with the market —
    "alternance IA" was worth little in July and a lot in September — so a measured zero today is
    not a zero next month, and dropping would silently shrink coverage, which is the opposite of
    what she asked for ("never tell me there is nothing more"). A zero simply sinks to the
    bottom. Learned queries that are NOT already seeds are deliberately not injected either:
    they would arrive with no category label, and `tune()` already reports them for a human to
    add with one.

    ⚠ UNKNOWN RANKS ABOVE KNOWN-BAD. A seed nobody has measured yet sits after the queries proven
    to work and before the ones proven not to, because "not yet measured" is not evidence of
    failure. Absence is not a negative, here as everywhere else in this repo.

    A missing, empty or unreadable cache returns the seeds untouched, so a source can never be
    left with fewer queries than its author gave it.
    """
    known = _load().get(source, {})
    if not known:
        return list(seeds)
    UNMEASURED = 0.5                      # above a measured zero, below anything that worked
    today = date.today()

    def worth(query: str) -> float:
        e = known.get(query)
        if not e:
            return UNMEASURED
        # ⚠ A STALE NUMBER IS CLOSER TO UNKNOWN THAN TO FACT. This module's own premise is that
        # yield moves with the market — "alternance IA" was worth little in July and a lot in
        # September — so trusting a measurement forever contradicts the reason it is measured at
        # all. Past _STALE_DAYS it reverts to unmeasured, which also creates the pressure to
        # re-measure instead of silently ranking on last season's market.
        try:
            age = (today - date.fromisoformat(e.get("measured", ""))).days
        except Exception:
            age = 0                       # undated: treat as current rather than discard it
        if age > _STALE_DAYS:
            return UNMEASURED
        # ⚠ PER PAGE, not per measurement: score scales with volume, so comparing a 2-page
        # measurement against a 1-page one rewards the deeper sweep, not the better query.
        return float(e.get("score", 0)) / max(1, int(e.get("pages", 1) or 1))

    return sorted(seeds, key=lambda pair: (-worth(pair[1]), pair[0], pair[1]))


def tune(source: str, candidates: list[str], pages: int = 2) -> dict:
    """Try NEW queries against what is already known, and report which ones earn a place.

    This is the part that makes the search improve rather than repeat: a candidate is kept only
    if it beats the weakest incumbent, so the query set drifts toward what the market rewards
    instead of what someone typed in September.
    """
    known = _load().get(source, {})
    floor = min((v.get("score", 0) for v in known.values()), default=0)
    fresh = [q for q in candidates if q not in known]
    got = rank(source, fresh, pages) if fresh else []
    keep = [m for m in got if m["score"] > floor]
    return {"source": source, "tried": len(fresh), "kept": [m["query"] for m in keep],
            "floor": floor, "detail": got}


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "health"
    if cmd == "health":
        print(f"{'source':16} {'ok':>3} {'raw':>5} {'fit':>4} {'alt':>4} {'idf':>4} {'secs':>5}  error")
        for name in ADAPTERS:
            h = health(name)
            print(f"{name:16} {'yes' if h['ok'] else 'NO':>3} {h['raw']:5} {h['role_fit']:4} "
                  f"{h['alternance']:4} {h['idf']:4} {h['secs']:5}  {h['error']}")
    elif cmd == "rank":
        src = sys.argv[2]
        for m in rank(src, sys.argv[3:]):
            print(f"  score={m['score']:4} raw={m['raw']:4} fit={m['role_fit']:3} "
                  f"alt={m['alternance']:3} idf={m['idf']:3}  {m['query']}")
    elif cmd == "best":
        print(best(sys.argv[2]))
    elif cmd == "tune":
        print(json.dumps(tune(sys.argv[2], sys.argv[3:]), ensure_ascii=False, indent=1))
