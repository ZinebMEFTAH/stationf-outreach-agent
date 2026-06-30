"""La Bonne Alternance — job source module (the hidden market).

La Bonne Alternance (labonnealternance.apprentissage.beta.gouv.fr) is run by the French state.
A predictive algorithm flags companies with strong hiring potential that have NOT published any
offer, and exposes them for spontaneous applications. Its public API
(api.apprentissage.beta.gouv.fr, operation `jobSearch`) returns two arrays for a set of ROME
codes around a geo point:
    - `jobs`       — real alternance postings (carry a title + contract)
    - `recruiters` — the hidden-market companies (no posting; just workplace + apply info)
The `recruiters` are the unique value: pre-qualified targets nobody else is emailing. We query
the software/data ROME codes around Paris with an Île-de-France radius (métier + IDF filter).

The API does NOT expose a contact email (GDPR) — only `apply.url`, `apply.phone` and, most
usefully, `workplace.website`. We carry that website on the listing so persist() derives a real
`contact@<domain>` and /find-contacts later upgrades it to a named decision-maker. Hence this is
a discovery-only source (enrich=False) — but, unlike the other API sources, it ships the real
company domain for free, so its rows are well-targeted from the start. Recruiter rows have no
posting, so they are added as speculative `[Suggested]` pitches (like /speculative produces).

Activation (one-time): create a free account at https://api.apprentissage.beta.gouv.fr, copy the
API key from the profile page, and put it in .env:
    LBA_API_KEY=...
Until it's set this source is inert (discover() returns [] with a one-line note), so it never
breaks an `--source all` run.

Conforms to the source interface used by scraper.py:
    NAME, JOBS_URL, discover(page, max_pages), resolve_company_site(page, listing)
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import config
import jobsource as js

NAME = "labonnealternance"
SEARCH_URL = "https://api.apprentissage.beta.gouv.fr/api/job/v1/search"
JOBS_URL = "https://labonnealternance.apprentissage.beta.gouv.fr/recherche"

# Métier filter: ROME codes covering software / backend / data / AI roles. The algorithm
# flags recruiters for these métiers; job offers are additionally re-checked by title.
#   M1805 — Études et développement informatique (dev, software eng, ML/AI, data science)
#   M1810 — Production et exploitation de systèmes d'information (devops / SRE / ops)
#   M1802 — Expertise et support en systèmes d'information
ROMES = ["M1805", "M1810", "M1802"]

# IDF filter: search around Paris with a radius that covers Île-de-France (the API has no
# region filter, only a geo radius — max 200 km).
PARIS_LAT, PARIS_LON = 48.8566, 2.3522
RADIUS_KM = 50

# The algorithm flags ~300k companies/month nationally; cap how many hidden-market recruiters
# we ingest per run so contacts.xlsx isn't flooded with speculative rows (results come back
# sorted by distance, so the cap keeps the closest ones). Daily send caps gate the rest.
RECRUITER_LIMIT = 12

_SUGGESTED_ROLE = "[Suggested] Alternance Tech & Data"


def _search() -> dict | None:
    """One authenticated GET against the LBA jobSearch API. None on any failure."""
    key = config.LBA_API_KEY
    params = urllib.parse.urlencode({
        "romes": ",".join(ROMES),
        "latitude": PARIS_LAT,
        "longitude": PARIS_LON,
        "radius": RADIUS_KM,
    })
    req = urllib.request.Request(f"{SEARCH_URL}?{params}", headers={
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": js.DEFAULT_UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        hint = " (check LBA_API_KEY)" if e.code in (401, 403) else ""
        print(f"[labonnealternance]   API HTTP {e.code}{hint}")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"[labonnealternance]   API error: {type(e).__name__}: {e}")
        return None


def _company_name(workplace: dict) -> str:
    """Best display name for a workplace: commercial brand → name → legal name."""
    for k in ("brand", "name", "legal_name"):
        v = (workplace.get(k) or "").strip()
        if v:
            return v
    return ""


def _website(workplace: dict) -> str | None:
    site = (workplace.get("website") or "").strip()
    return site or None


def discover(page=None, max_pages: int | None = None) -> list[js.JobListing]:
    """Query La Bonne Alternance for software/data alternance around Île-de-France. `page` and
    `max_pages` are unused (one pure HTTP call, no pagination). Inert (returns []) when
    LBA_API_KEY is not configured. Best-effort: a failed call is logged, never aborts the run."""
    if not config.LBA_API_KEY:
        print("[labonnealternance]   skipped — set LBA_API_KEY to enable "
              "(free key at api.apprentissage.beta.gouv.fr)")
        return []

    data = _search()
    if not data:
        return []

    listings: list[js.JobListing] = []
    seen: set[str] = set()

    # 1) Real alternance postings — keep only AI/Backend/Data titles (re-checked, like the
    #    other boards). These carry a concrete role.
    jobs_added = 0
    for o in data.get("jobs") or []:
        workplace = o.get("workplace") or {}
        company = _company_name(workplace)
        title = ((o.get("offer") or {}).get("title") or "").strip()
        oid = str((o.get("identifier") or {}).get("id") or "")
        if not company or len(company) < 2 or not title:
            continue
        cat = js.matches_target_role(title)
        if not cat:
            continue
        key = oid or f"{company.lower()}|{title.lower()}"
        if key in seen:
            continue
        seen.add(key)
        listings.append(js.JobListing(
            company=company,
            role=title,
            company_url=_website(workplace),
            job_url=(o.get("apply") or {}).get("url") or JOBS_URL,
            category=cat,
            source=NAME,
        ))
        jobs_added += 1

    # 2) Hidden-market recruiters — companies flagged as hiring that posted nothing. No title,
    #    so they become speculative [Suggested] pitches. Capped per run; dedup by company.
    rec_added = 0
    for r in data.get("recruiters") or []:
        if rec_added >= RECRUITER_LIMIT:
            break
        workplace = r.get("workplace") or {}
        company = _company_name(workplace)
        if not company or len(company) < 2:
            continue
        key = company.lower()
        if key in seen:
            continue
        seen.add(key)
        listings.append(js.JobListing(
            company=company,
            role=_SUGGESTED_ROLE,
            company_url=_website(workplace),
            job_url=(r.get("apply") or {}).get("url") or JOBS_URL,
            category="backend",   # not persisted; used only for in-run labelling
            source=NAME,
        ))
        rec_added += 1

    if jobs_added or rec_added:
        print(f"[labonnealternance]   +{jobs_added} posting(s), "
              f"+{rec_added} hidden-market recruiter(s)")
    return listings


def resolve_company_site(page, listing: js.JobListing) -> str | None:
    """The website already comes back on the listing (workplace.website). Discovery-only
    (enrich=False), so this is never called — kept to satisfy the source interface."""
    return listing.company_url
