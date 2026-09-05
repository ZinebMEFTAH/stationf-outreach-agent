"""Job openings straight from an employer's OWN careers site.

The aggregators (APEC, France Travail, WTTJ, Free-Work, the remote boards) only ever show what a
company chose to syndicate. A large employer's real opening list lives on its own careers site and
is often nowhere else: GE HealthCare alone had 281 "data" postings behind careers.gehealthcare.com
on 2026-09-05, none of which any board in this repo could see. Those are also the roles worth the
most to her — she has a warm contact at GE HealthCare, and an application through a company's own
site lands in its ATS rather than in an aggregator's forwarding queue.

Almost every employer runs one of a handful of hosted platforms, and each publishes a public,
keyless JSON endpoint. So this is five small readers, not one scraper per company:

    greenhouse       boards-api.greenhouse.io/v1/boards/<token>/jobs
    lever            api.lever.co/v0/postings/<token>?mode=json
    ashby            api.ashbyhq.com/posting-api/job-board/<token>
    smartrecruiters  api.smartrecruiters.com/v1/companies/<token>/postings
    phenom           <career-site>/search-results — jobs embedded as `phApp.ddo` JSON

Every token in BOARDS was verified against the live API, never guessed: a wrong token returns an
empty list rather than an error, so a guessed one would fail silently and forever. Grow the list
with `probe`, which tries a slug against all four JSON providers and reports what answers:

    python company_boards.py probe mistral alan qonto   # which ATS, how many jobs
    python company_boards.py list
    python company_boards.py fetch [--company NAME]

Returns offers in the shape opportunities.py consumes; role/seniority filtering belongs to the
caller. Only France-based and remote roles are returned — a Bengaluru posting is not reachable.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import jobsource as js

NAME = "company-board"
_TIMEOUT = 20
_WORKERS = 8

# Coarse gate only — opportunities.is_reachable() applies the strict Île-de-France rule. This just
# stops a 600-posting global board from being carried across the network for nothing.
_FRANCE = re.compile(r"\b(france|paris|[îi]le[- ]de[- ]france|idf|lyon|toulouse|lille|bordeaux|"
                     r"nantes|marseille|rennes|grenoble|strasbourg|sophia|buc|v[ée]lizy)\b", re.I)
_REMOTE = re.compile(r"\bremote|t[ée]l[ée]travail|anywhere\b", re.I)
# "Remote" is a work-authorisation claim, not a geography: a US company's "US Remote" requires
# living and being employable in the United States, so it is no more available to her than an
# on-site job in Chicago — and Stripe and Datadog alone post hundreds, enough to swamp a 30-line
# digest.
#
# This is an ALLOWLIST, and it has to be. The first version blocked a list of non-EU places, which
# a board can always fall outside of: Ashby sets isRemote on postings whose location reads
# "Palo Alto HQ", "Tel Aviv" or "London", none of which were on the list, so all three sailed
# through as remote-workable. A blocklist of the world's cities cannot be completed. So a remote
# role is kept only when its scope is POSITIVELY open — worldwide / EMEA / Europe / France — or
# when it names no place at all ("Remote", "Flexible / Remote"). Anything that names somewhere
# else is somewhere else.
_REMOTE_EU_OK = re.compile(
    r"\b(worldwide|global|anywhere|emea|europe|european|eu|france|paris|ireland|germany|spain|"
    r"portugal|poland|netherlands|belgium|italy|cet)\b", re.I)
# Words that qualify HOW someone works rather than WHERE — stripped before asking whether a place
# was named, so "Flexible / Remote" reads as "no place named" instead of as a location.
_REMOTE_NEUTRAL = re.compile(
    r"\b(remote|remotely|t[ée]l[ée]travail|flexible|hybrid|hybride|home|wfh|distributed|"
    r"anywhere|any|location|locations|based|optional|friendly|first|onsite|on[- ]site|"
    r"full[- ]?time|part[- ]?time|ok|from|work)\b|[^A-Za-zÀ-ÿ]+", re.I)


def _remote_scope_ok(location: str) -> bool:
    """Is a remote role open to someone working from France?"""
    loc = location or ""
    if _REMOTE_EU_OK.search(loc):
        return True
    return not _REMOTE_NEUTRAL.sub(" ", loc).strip()


def _get(url: str, data: bytes | None = None, timeout: int = _TIMEOUT):
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": js.DEFAULT_UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _keep(location: str) -> tuple[bool, str]:
    """(keep, mode). Remote is kept wherever it is; on-site only if it looks French."""
    if _REMOTE.search(location or ""):
        return (True, "remote") if _remote_scope_ok(location) else (False, "")
    if _FRANCE.search(location or ""):
        return True, "onsite"
    return False, ""


# ── Providers ────────────────────────────────────────────────────────────────

def _greenhouse(token: str) -> list[dict]:
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
    out = []
    for j in data.get("jobs") or []:
        loc = ((j.get("location") or {}).get("name") or "").strip()
        keep, mode = _keep(loc)
        if keep:
            out.append({"role": (j.get("title") or "").strip(),
                        "url": j.get("absolute_url") or "", "location": loc, "mode": mode})
    return out


def _lever(token: str) -> list[dict]:
    data = _get(f"https://api.lever.co/v0/postings/{token}?mode=json")
    out = []
    for j in data if isinstance(data, list) else []:
        cat = j.get("categories") or {}
        loc = (cat.get("location") or "").strip()
        keep, mode = _keep(f"{loc} {cat.get('commitment') or ''} {j.get('workplaceType') or ''}")
        if keep:
            out.append({"role": (j.get("text") or "").strip(),
                        "url": j.get("hostedUrl") or "", "location": loc, "mode": mode})
    return out


def _ashby(token: str) -> list[dict]:
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    out = []
    for j in data.get("jobs") or []:
        loc = (j.get("location") or "").strip()
        keep, mode = _keep(f"{loc} {'remote' if j.get('isRemote') else ''}")
        if keep:
            out.append({"role": (j.get("title") or "").strip(),
                        "url": j.get("jobUrl") or "", "location": loc, "mode": mode})
    return out


def _smartrecruiters(token: str) -> list[dict]:
    data = _get(f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100")
    out = []
    for j in data.get("content") or []:
        loc = j.get("location") or {}
        text = " ".join(str(loc.get(k) or "") for k in ("city", "region", "country"))
        keep, mode = _keep(f"{text} {'remote' if loc.get('remote') else ''}")
        if keep:
            out.append({"role": (j.get("name") or "").strip(),
                        "url": f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}",
                        "location": text.strip(), "mode": mode})
    return out


def _phenom_ddo(url: str) -> dict | None:
    """Pull the `phApp.ddo = {...}` blob a Phenom career site embeds in its search page.

    Phenom hosts the careers site of a large share of the Fortune 500 (GE HealthCare among them)
    and exposes no documented API — its own front end reads this object. Brace-matched rather than
    regex-matched: the JSON contains every job description, so it is far too nested for a pattern.
    """
    req = urllib.request.Request(url, headers={"User-Agent": js.DEFAULT_UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        html = r.read().decode("utf-8", "ignore")
    marker = "phApp.ddo = "
    i = html.find(marker)
    if i < 0:
        return None
    frag = html[i + len(marker):]
    depth = 0
    for n, c in enumerate(frag):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(frag[:n + 1])
                except ValueError:
                    return None
    return None


_PHENOM_QUERIES = ("data", "machine learning", "software engineer")
_PHENOM_PAGE = 10     # Phenom serves ten postings per search page and ignores any size parameter
_PHENOM_PAGES = 10    # …so paginate; 100 per keyword covers GE HealthCare's 281 "data" hits


def _phenom(token: str) -> list[dict]:
    """`token` is the search-results base, e.g. 'careers.gehealthcare.com/global/en'.

    Queried by KEYWORD and paginated with `from=`, then filtered on location here. Both halves of
    that are forced. Phenom's location filter needs a place_id resolved through its own geocoder:
    `country=France` and `location=Paris, France` are both accepted, return HTTP 200, leave the
    result count untouched at 281 and hand back the same worldwide first page — so a naive
    location filter looks like it works and silently shows her jobs in Bengaluru. And the page
    itself is only ten postings deep with no size parameter, so without `from=` a global employer
    is judged on ten arbitrary rows: GE HealthCare's first page held zero French roles out of 281
    matches, which read as "no openings" when it meant "we only looked at the first ten".
    """
    out, seen = [], set()

    def _page(args: tuple[str, int]) -> list[dict]:
        q, frm = args
        url = f"https://{token}/search-results?" + urllib.parse.urlencode(
            {"keywords": q, "from": frm})
        try:
            ddo = _phenom_ddo(url)
        except Exception as e:  # noqa: BLE001
            print(f"[boards]   phenom {token} '{q}' from={frm}: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
            return []
        return ((ddo or {}).get("eagerLoadRefineSearch") or {}).get("data", {}).get("jobs", [])

    work = [(q, n * _PHENOM_PAGE) for q in _PHENOM_QUERIES for n in range(_PHENOM_PAGES)]
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        for jobs in pool.map(_page, work):
            for j in jobs:
                loc = (j.get("cityStateCountry") or j.get("location")
                       or f"{j.get('city') or ''} {j.get('country') or ''}").strip()
                keep, mode = _keep(loc)
                link = j.get("applyUrl") or j.get("jobSeoUrl") or ""
                if keep and link and link not in seen:
                    seen.add(link)
                    out.append({"role": (j.get("title") or "").strip(),
                                "url": link, "location": loc, "mode": mode})
    return out


PROVIDERS = {
    "greenhouse": _greenhouse,
    "lever": _lever,
    "ashby": _ashby,
    "smartrecruiters": _smartrecruiters,
    "phenom": _phenom,
}
_JSON_PROVIDERS = ("greenhouse", "lever", "ashby", "smartrecruiters")   # probe-able by slug


# ── The employer list ────────────────────────────────────────────────────────
# Every entry below answered live when it was added (see `probe`). A company appears once: where
# two platforms both answered, the one with the fuller listing wins.
BOARDS: list[dict] = [
    # Warm/priority — she has a referral here, so its own careers site is the channel that matters.
    {"company": "GE HealthCare", "provider": "phenom", "token": "careers.gehealthcare.com/global/en"},

    # Paris-HQ scale-ups: the employers global_brands.py flags as cold-emailable, read at source.
    {"company": "Alan", "provider": "ashby", "token": "alan"},
    {"company": "Qonto", "provider": "ashby", "token": "qonto"},
    {"company": "Doctolib", "provider": "greenhouse", "token": "doctolib"},
    {"company": "Contentsquare", "provider": "lever", "token": "contentsquare"},
    {"company": "Pennylane", "provider": "ashby", "token": "pennylane"},
    {"company": "Swile", "provider": "lever", "token": "swile"},
    {"company": "Back Market", "provider": "ashby", "token": "backmarket"},
    {"company": "Dataiku", "provider": "greenhouse", "token": "dataiku"},
    {"company": "Ledger", "provider": "ashby", "token": "ledger"},
    {"company": "Sorare", "provider": "ashby", "token": "sorare"},
    {"company": "Aircall", "provider": "lever", "token": "aircall"},
    {"company": "BlaBlaCar", "provider": "lever", "token": "blablacar"},
    {"company": "Algolia", "provider": "greenhouse", "token": "algolia"},
    {"company": "Mirakl", "provider": "greenhouse", "token": "mirakl"},
    {"company": "Younited", "provider": "lever", "token": "younited"},
    {"company": "Shift Technology", "provider": "greenhouse", "token": "shifttechnology"},
    {"company": "Owkin", "provider": "ashby", "token": "owkin"},
    {"company": "Ivalua", "provider": "greenhouse", "token": "ivalua"},
    {"company": "Veepee", "provider": "lever", "token": "veepee"},
    {"company": "Agicap", "provider": "lever", "token": "agicap"},
    {"company": "Malt", "provider": "lever", "token": "malt"},
    {"company": "Voodoo", "provider": "ashby", "token": "voodoo"},
    {"company": "Ankorstore", "provider": "ashby", "token": "ankorstore"},
    {"company": "Sunday", "provider": "ashby", "token": "sunday"},
    {"company": "Scaleway", "provider": "lever", "token": "scaleway"},
    {"company": "Verkor", "provider": "lever", "token": "verkor"},
    {"company": "Poolside", "provider": "ashby", "token": "poolside"},
    {"company": "Photoroom", "provider": "ashby", "token": "photoroom"},
    {"company": "Nabla", "provider": "ashby", "token": "nabla"},

    # More Paris scale-ups, each verified with `probe` before being added.
    {"company": "Pigment", "provider": "lever", "token": "pigment"},
    {"company": "Doctrine", "provider": "lever", "token": "doctrine"},
    {"company": "360Learning", "provider": "lever", "token": "360learning"},
    {"company": "Heetch", "provider": "lever", "token": "heetch"},
    {"company": "Alice & Bob", "provider": "lever", "token": "alice-bob"},
    {"company": "Vestiaire Collective", "provider": "lever", "token": "vestiairecollective"},
    {"company": "Dust", "provider": "ashby", "token": "dust"},
    {"company": "Filigran", "provider": "ashby", "token": "filigran"},
    {"company": "Finary", "provider": "ashby", "token": "finary"},
    {"company": "Gorgias", "provider": "ashby", "token": "gorgias"},
    {"company": "Lifen", "provider": "ashby", "token": "lifen"},
    {"company": "Swan", "provider": "ashby", "token": "swan"},
    {"company": "Trainline", "provider": "ashby", "token": "trainline"},
    {"company": "Believe", "provider": "smartrecruiters", "token": "believe"},
    {"company": "Dailymotion", "provider": "smartrecruiters", "token": "dailymotion"},
    {"company": "EcoVadis", "provider": "smartrecruiters", "token": "ecovadis"},
    {"company": "JobTeaser", "provider": "smartrecruiters", "token": "jobteaser"},
    {"company": "Meilleurtaux", "provider": "smartrecruiters", "token": "meilleurtaux"},
    {"company": "Accor", "provider": "smartrecruiters", "token": "Accor"},

    # Large employers whose careers site is Phenom-hosted — where alternance volume actually is.
    # Thales is also one of her CFA school partners (school_partners.py), so a posting here is
    # reachable through the school as well as through the portal. Roche and Siemens Healthineers
    # sit next to her GE HealthCare profile; both had no French AI/Data opening on 2026-09-05, and
    # are kept because that changes week to week and the reader costs one parallel pass.
    {"company": "Thales", "provider": "phenom", "token": "careers.thalesgroup.com/global/en"},
    {"company": "Roche", "provider": "phenom", "token": "careers.roche.com/global/en"},
    {"company": "Siemens Healthineers", "provider": "phenom",
     "token": "careers.siemens-healthineers.com/global/en"},

    # Global engineering employers with a Paris office — the roles are real, the competition is
    # stiff, and the only way in is their own portal. Worth surfacing, never worth cold-emailing.
    {"company": "Datadog", "provider": "greenhouse", "token": "datadog"},
    {"company": "Stripe", "provider": "greenhouse", "token": "stripe"},
]


def fetch_one(board: dict) -> list[dict]:
    fn = PROVIDERS.get(board.get("provider", ""))
    if not fn:
        return []
    try:
        rows = fn(board["token"])
    except Exception as e:  # noqa: BLE001
        print(f"[boards]   {board['company']} ({board['provider']}): "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        return []
    out = []
    for r in rows:
        if not r.get("role") or not r.get("url"):
            continue
        out.append({"company": board["company"], "role": r["role"], "url": r["url"],
                    "location": r.get("location") or "France", "mode": r.get("mode") or "onsite",
                    "source": board["provider"]})
    if out:
        print(f"[boards]   {board['company']}: +{len(out)} France/remote posting(s)")
    return out


def fetch(company: str | None = None) -> list[dict]:
    """Every France-based or remote posting across the configured employers, fetched in parallel."""
    boards = [b for b in BOARDS
              if not company or company.strip().lower() in b["company"].lower()]
    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        for rows in pool.map(fetch_one, boards):
            out.extend(rows)
    return out


def probe(slug: str) -> list[tuple[str, int]]:
    """Which JSON ATS answers for this slug, and with how many postings. How BOARDS grows.

    Phenom is absent by design: it is keyed on a career-site hostname, not a slug, so there is
    nothing to guess — find the site, confirm it serves `phApp.ddo`, and add it by hand.
    """
    hits = []
    for name in _JSON_PROVIDERS:
        try:
            rows = PROVIDERS[name](slug)
        except Exception:  # noqa: BLE001
            continue
        if rows:
            hits.append((name, len(rows)))
    return hits


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list")
    f = sub.add_parser("fetch")
    f.add_argument("--company")
    p = sub.add_parser("probe")
    p.add_argument("slugs", nargs="+")
    a = ap.parse_args(argv)

    if a.cmd == "probe":
        for slug in a.slugs:
            hits = probe(slug)
            print(f"{slug:<24} {hits if hits else '— no public board found'}")
        return 0
    if a.cmd == "fetch":
        rows = fetch(a.company)
        for r in rows:
            print(f"{r['company']:<18} {r['role'][:52]:<54} {r['location'][:26]:<28} {r['mode']}")
        print(f"\n{len(rows)} France/remote posting(s) across "
              f"{len({r['company'] for r in rows})} employer(s)")
        return 0
    by_provider: dict[str, list[str]] = {}
    for b in BOARDS:
        by_provider.setdefault(b["provider"], []).append(b["company"])
    for prov, names in sorted(by_provider.items()):
        print(f"{prov:<16} {len(names):>2}  {', '.join(sorted(names))}")
    print(f"\n{len(BOARDS)} employer careers sites configured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
