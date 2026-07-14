"""Remotive — remote/international job source module.

Remotive (remotive.com) lists REMOTE-ONLY tech jobs worldwide via a free public JSON API
(GET /api/remote-jobs) — no key, no browser. This is Zineb's INTERNATIONAL channel: she stays
in France, so a foreign company is only viable as a remote role — which is exactly what Remotive
carries. We keep AI/Backend/Data roles whose required location allows France / Europe / Worldwide
(dropping US-only or region-locked postings she couldn't legally work remotely), and tag each
role as remote+international so /daily-agent asks for the RIGHT contract: an internship / CDI /
full-time role, in English — never "alternance", which needs a French employer + school.

Discovery-only (enrich=False): the company's real domain is recovered later by
company_resolver / /find-contacts. Conforms to the source interface used by scraper.py:
    NAME, JOBS_URL, discover(page, max_pages), resolve_company_site(page, listing)
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import config
import jobsource as js

NAME = "remotive"
BASE = "https://remotive.com"
API = f"{BASE}/api/remote-jobs"
JOBS_URL = f"{BASE}/remote-jobs/software-dev"
PER_QUERY = 40

QUERIES: dict[str, str] = {
    "ai": "machine learning",
    "ai2": "AI engineer",
    "backend": "backend engineer",
    "data": "data engineer",
    "data2": "data scientist",
}

# Drop freelance / staffing-marketplace listings (A.Team, Lemon.io "Independent …") — Zineb
# wants an employment contract, not a gig. Mirrors the Free-Work freelance filter.
_FREELANCE = ("independent", "freelance", "contractor", "contract-to-hire", "gig")

# Tag appended to the role so downstream (ranking + the daily-agent contract/language logic)
# can tell at a glance this is a remote, international lead. Single source of truth in config.
REMOTE_TAG = config.REMOTE_INTL_TAG

# Locations a France-based candidate can legally work remotely from: keep if the required
# location is open (worldwide/anywhere) or explicitly includes Europe/France/EMEA/EU. Drop a
# posting locked to a region she can't work from (USA-only, Brazil, Canada, LATAM-only, …).
_LOCATION_OK = (
    "worldwide", "anywhere", "global", "international",
    "europe", "european", "france", "emea", "united kingdom", " uk", "uk,", "ireland",
)


def is_workable_location(loc: str) -> bool:
    l = (loc or "").strip().lower()
    if not l:
        return True  # unspecified → assume open to anyone
    return any(k in l for k in _LOCATION_OK)


def _search(query: str, limit: int = PER_QUERY) -> list[dict]:
    params = urllib.parse.urlencode({"search": query, "limit": limit})
    req = urllib.request.Request(f"{API}?{params}", headers={
        "User-Agent": js.DEFAULT_UA, "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r).get("jobs", []) or []


def discover(page=None, max_pages: int | None = None) -> list[js.JobListing]:
    """Query Remotive for remote AI/Backend/Data roles workable from France. `page`/`max_pages`
    unused (pure HTTP). Best-effort: a failed query is logged and skipped, never aborts the run."""
    listings: list[js.JobListing] = []
    seen: set[str] = set()

    for category, query in QUERIES.items():
        try:
            results = _search(query)
        except urllib.error.HTTPError as e:
            print(f"[remotive]   API HTTP {e.code} for '{query}'")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"[remotive]   API error for '{query}': {type(e).__name__}: {e}")
            continue

        added = 0
        for j in results:
            title = (j.get("title") or "").strip()
            company = (j.get("company_name") or "").strip()
            key = str(j.get("id") or j.get("url") or "")
            if not title or len(company) < 2 or not key or key in seen:
                continue
            if not is_workable_location(j.get("candidate_required_location", "")):
                continue
            if any(f in title.lower() for f in _FREELANCE):
                continue
            cat = js.matches_target_role(title)
            if not cat:
                continue
            seen.add(key)
            listings.append(js.JobListing(
                company=company,
                role=f"{title} {REMOTE_TAG}",
                job_url=j.get("url"),
                category=cat,
                source=NAME,
            ))
            added += 1
        if added:
            print(f"[remotive]   query='{query}': +{added} match(es)")
    return listings


def resolve_company_site(page, listing: js.JobListing) -> str | None:
    """Remotive exposes no company website in the listing. Discovery-only (enrich=False), so
    this is never called — kept to satisfy the source interface."""
    return None
