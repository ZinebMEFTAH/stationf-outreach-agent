"""Ask EVERY employer's own recruitment system what it has open, and keep only what is live.

WHY THIS EXISTS. Three application packs in one session were built for postings that no longer
existed — Groupe SII, Docaret, Hermès — because the aggregators that carry the alternance volume
(HelloWork, Adzuna, LinkedIn) keep rendering closed jobs. HelloWork is 7-for-7 dead on the rows
checked so far. The employer's own board is the only system that knows, because that is where a
recruiter closes the requisition.

Her instruction, 2026-09-21: *"verify all boards of all companies international and national all
that have companies in Paris and ile de france and close to it. forget no one. try to use ur
credits only in final decisions, otherwise other staff if it is possible to do it with code do
it."* So this is deliberately a PROGRAM, not a reading exercise: it gathers the employer
universe, fingerprints each careers site, queries whatever ATS answers, and filters to roles she
could actually take. Claude only judges the shortlist it prints.

THE EMPLOYER UNIVERSE, unioned from everything the repo already knows:
    company_boards.BOARDS   employers with a token VERIFIED against the live API
    school_partners         the CFA numiA partners — the channel that reaches her best
    global_brands           international employers that hire juniors IN France
    cache/queue.json        every employer any source surfaced today
Roughly 400 names, which is why it is parallel, cached and resumable.

⚠ 'unreadable' IS NOT 'nothing open'. Most large French employers run Taleo, SuccessFactors,
iCIMS or Avature, and those are precisely the ones carrying the alternance market. An employer
this cannot read is reported as UNREADABLE and stays a candidate for the CFA route; it is never
counted as empty.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ats
import jobsource as js

OUT = Path(__file__).parent / "cache" / "board_sweep.json"
_WORKERS = 8
_lock = threading.Lock()

# Contract-first: alternance is the scarcity. Each is asked separately because an ATS search is
# a keyword match, not a semantic one, and "alternance" alone misses "apprenti".
_QUERIES = ("alternance", "apprenti", "data", "machine learning", "développeur")

_ALT = re.compile(r"\balternan[ct]e?s?\b|\bapprenti(?:e|s|es)?\b|\bapprenticeship\b|"
                  r"\bwork[- ]study\b", re.I)


# ⚠ THE REPO'S OWN LISTS ARE NOT THE MARKET. Checking 90 well-known Île-de-France employers
# against the unioned universe found 47 ABSENT — Airbus, Sanofi, L'Oréal, RATP, La Poste, Atos,
# Accenture, Worldline, Engie, LVMH and more. They were missing because nothing had surfaced
# them: company_boards holds employers whose token someone verified, global_brands the scale-ups,
# and the queue only whoever advertised this week. Her instruction was "forget no one", so the
# large IDF employers are named here explicitly rather than waiting for a board to mention them.
# Being listed costs one fingerprint; being absent costs an entire employer, permanently.
_MAJOR_IDF = (
    # banque / assurance
    "Crédit Agricole", "Natixis", "Allianz", "CNP Assurances", "La Banque Postale",
    # défense / aéronautique / industrie
    "Airbus", "Naval Group", "MBDA", "ArianeGroup", "Michelin", "Valeo", "Alstom",
    # télécoms
    "SFR", "Free", "Bouygues Telecom",
    # ESN / conseil
    "Atos", "Accenture", "Devoteam", "Onepoint", "Wavestone", "Sia Partners", "Inetum",
    "Econocom", "Akkodis", "Alten", "Talan",
    # énergie / environnement / BTP
    "TotalEnergies", "Engie", "Suez", "Vinci", "Eiffage",
    # retail / luxe / santé
    "Leroy Merlin", "Decathlon", "LVMH", "Kering", "L'Oréal", "Danone", "Sanofi", "Servier",
    "Carrefour",
    # transport / public
    "RATP", "Groupe ADP", "La Poste", "SNCF",
    # tech / paiement
    "Worldline", "Ingenico", "Edenred", "Murex", "Amadeus", "Ubisoft", "Criteo",
)


def employers() -> list[str]:
    """Every employer worth asking, unioned and de-duplicated on the normalised name."""
    import leadset as ls

    names: dict[str, str] = {}

    def add(name: str) -> None:
        n = (name or "").strip()
        if len(n) < 2:
            return
        names.setdefault(ls.norm_company(n), ls.display_company(n))

    try:
        import company_boards as cb
        for b in cb.BOARDS:
            add(b.get("company", ""))
    except Exception:                                              # noqa: BLE001
        pass
    for mod, key in (("school_partners", "company"), ("global_brands", "company")):
        try:
            rows = __import__(mod).load()
            for r in rows:
                add(r.get(key, "") if isinstance(r, dict) else str(r))
        except Exception:                                          # noqa: BLE001
            pass
    for n in _MAJOR_IDF:
        add(n)
    try:
        q = json.loads((Path(__file__).parent / "cache" / "queue.json").read_text(encoding="utf-8"))
        for x in q:
            add((x.get("lead") or {}).get("company", ""))
    except Exception:                                              # noqa: BLE001
        pass
    return sorted(names.values())


def _reachable(location: str) -> bool:
    """Île-de-France, the ~1h ring, remote, or a location the board did not state.

    Unknown counts as reachable on purpose: an ATS row often carries no location at all, and
    dropping those would discard the employer's own listing — the most reliable source there is.
    """
    if not (location or "").strip():
        return True
    try:
        import opportunities as opp
        return opp.is_reachable({"location": location, "mode": "onsite", "role": ""})[0]
    except Exception:                                              # noqa: BLE001
        return True


def _known_board(company: str) -> dict | None:
    """The company_boards entry for this employer, matched on the normalised name."""
    try:
        import company_boards as cb
        import leadset as ls
        key = ls.norm_company(company)
        for b in cb.BOARDS:
            if ls.norm_company(b.get("company", "")) == key:
                return b
    except Exception:                                              # noqa: BLE001
        pass
    return None


def _rows_from_known(board: dict) -> list[dict] | None:
    """Ask the provider this employer is already known to use. None when it fails."""
    try:
        import company_boards as cb
        fn = cb.PROVIDERS.get(board["provider"])
        return fn(board["token"]) if fn else None
    except Exception:                                              # noqa: BLE001
        return None


def _collect(rec: dict, rows: list[dict]) -> None:
    """Filter an employer's own rows down to what she could take, into `rec`."""
    seen: set[str] = set()
    for r in rows:
        role = (r.get("role") or "").strip()
        loc = (r.get("location") or "").strip()
        if not role or role.lower() in seen:
            continue
        seen.add(role.lower())
        if not _reachable(loc):
            continue
        cat = js.matches_target_role(role)
        if not cat or js.excluded_role(role):
            continue
        row = {"role": role, "location": loc, "url": r.get("url") or ""}
        rec["role_fit"].append(row)
        contract = str((r.get("meta") or {}).get("contract") or "")
        if _ALT.search(role) or contract == "alternance":
            rec["alternances"].append(row)


def sweep_one(company: str) -> dict:
    """Fingerprint one employer and ask its board. Never raises."""
    rec = {"company": company, "platform": None, "readable": False,
           "alternances": [], "role_fit": [], "error": ""}
    try:
        # ⚠ A VERIFIED TOKEN BEATS A FRESH FINGERPRINT. 57 employers in company_boards carry a
        # token that was checked against the live API, and fingerprinting can disagree with it:
        # AXA fingerprints as iCIMS (its old host still answers) while its REAL board is the
        # keyless Eightfold at careers.axa.com that this repo already reads. Asking the
        # fingerprint first would report a CFA partner as unreadable when it is not.
        known = _known_board(company)
        if known:
            rec["platform"] = known["provider"]
            rows = _rows_from_known(known)
            if rows is not None:
                rec["readable"] = True
                _collect(rec, rows)
                return rec
        fp = ats.fingerprint(company)
        rec["platform"] = fp.get("platform")
        if not fp.get("platform"):
            rec["error"] = "no ATS signature (custom or JS-only careers site)"
            return rec
        if fp["platform"] in ats.UNREADABLE:
            rec["error"] = f"{fp['platform']} cannot be read without a browser session"
            return rec
        seen: set[str] = set()
        for q in _QUERIES:
            try:
                got = ats.postings(company, query=q)
            except Exception as e:                                 # noqa: BLE001
                rec["error"] = f"{type(e).__name__}"
                break
            if not got.get("readable"):
                rec["error"] = got.get("note") or "unreadable"
                break
            rec["readable"] = True
            for r in got.get("rows") or []:
                role = (r.get("role") or "").strip()
                loc = (r.get("location") or "").strip()
                if not role or role.lower() in seen:
                    continue
                seen.add(role.lower())
                if not _reachable(loc):
                    continue
                cat = js.matches_target_role(role)
                if not cat or js.excluded_role(role):
                    continue
                row = {"role": role, "location": loc, "url": r.get("url") or ""}
                rec["role_fit"].append(row)
                if _ALT.search(role):
                    rec["alternances"].append(row)
    except Exception as e:                                         # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {str(e)[:60]}"
    return rec


def sweep(names: list[str] | None = None, workers: int = _WORKERS,
          on_result=None) -> list[dict]:
    """Sweep every employer in parallel, writing results as they land so it is resumable."""
    names = names or employers()
    done: dict[str, dict] = {}
    if OUT.exists():
        try:
            done = {r["company"]: r for r in json.loads(OUT.read_text(encoding="utf-8"))}
        except Exception:                                          # noqa: BLE001
            done = {}
    todo = [n for n in names if n not in done]
    print(f"[sweep] {len(names)} employers, {len(done)} already done, {len(todo)} to check",
          file=sys.stderr)

    def work(name: str) -> dict:
        rec = sweep_one(name)
        with _lock:
            done[name] = rec
            try:
                OUT.parent.mkdir(parents=True, exist_ok=True)
                OUT.write_text(json.dumps(list(done.values()), ensure_ascii=False, indent=1),
                               encoding="utf-8")
            except Exception:                                      # noqa: BLE001
                pass
        if on_result:
            on_result(rec)
        return rec

    if todo:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for _ in pool.map(work, todo):
                pass
    return list(done.values())


def report(rows: list[dict] | None = None) -> None:
    rows = rows or (json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else [])
    readable = [r for r in rows if r["readable"]]
    withalt = [r for r in rows if r["alternances"]]
    print(f"\n{len(rows)} employers swept · {len(readable)} readable · "
          f"{len(withalt)} with a role-fit ALTERNANCE open now\n")
    for r in sorted(withalt, key=lambda x: -len(x["alternances"])):
        print(f"── {r['company']}  [{r['platform']}]")
        for a in r["alternances"][:6]:
            print(f"     {a['role'][:64]:64} {a['location'][:22]}")
            if a["url"]:
                print(f"       {a['url'][:100]}")
    blocked = [r for r in rows if not r["readable"] and r.get("platform")]
    if blocked:
        import collections
        print(f"\n{len(blocked)} employers run an ATS this cannot read "
              f"(CFA route, not 'nothing open'): "
              f"{dict(collections.Counter(r['platform'] for r in blocked))}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        report()
    else:
        names = employers()
        limit = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
        t0 = time.time()
        rows = sweep(names[:limit] if limit else names)
        print(f"[sweep] finished in {time.time() - t0:.0f}s", file=sys.stderr)
        report(rows)
