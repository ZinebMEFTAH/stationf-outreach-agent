"""Welcome to the Jungle — job source module.

WTTJ's web UI is a stubborn SPA (results never render from a plain URL), but its job
search is powered by a PUBLIC Algolia index that the site itself queries. We hit that
index directly over plain HTTP — no browser, no Cloudflare, no fragile DOM scraping for
discovery. Per-company website lookup (for email derivation) still uses the shared
Playwright page, since the API only exposes the company's WTTJ slug, not its real domain.

The Algolia app id + search key are PUBLIC client credentials embedded in WTTJ's site.
They can rotate; override via env (WTTJ_ALGOLIA_APP / WTTJ_ALGOLIA_KEY) if discovery
starts returning 403. The key is referer-restricted, so we send the WTTJ Origin/Referer.

Conforms to the source interface used by scraper.py:
    NAME, JOBS_URL, discover(page, max_pages), resolve_company_site(page, listing)
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import jobsource as js

NAME = "wttj"
BASE = "https://www.welcometothejungle.com"
LANG = "en"
JOBS_URL = f"{BASE}/{LANG}/jobs"

ALGOLIA_APP = os.getenv("WTTJ_ALGOLIA_APP", "CSEKHVMS53")
ALGOLIA_KEY = os.getenv("WTTJ_ALGOLIA_KEY", "4bd8f6215d0cc52b26430765769e65a0")  # public client key
ALGOLIA_INDEX = os.getenv("WTTJ_ALGOLIA_INDEX", "wk_cms_jobs_production")
HITS_PER_PAGE = 20

# One focused query per target category keeps the result set relevant; titles are still
# re-checked with jobsource.matches_target_role so only real AI/Backend/Data roles pass.
QUERIES: dict[str, str] = {
    "ai": "machine learning engineer",
    "backend": "backend engineer",
    "data": "data engineer",
}


def _algolia_query(query: str, page: int, hits_per_page: int = HITS_PER_PAGE) -> dict:
    """POST one search to the public WTTJ Algolia jobs index. Algolia pages are 0-indexed."""
    url = f"https://{ALGOLIA_APP.lower()}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"
    body = json.dumps({"query": query, "page": page, "hitsPerPage": hits_per_page}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "X-Algolia-Application-Id": ALGOLIA_APP,
        "X-Algolia-API-Key": ALGOLIA_KEY,
        "Content-Type": "application/json",
        "User-Agent": js.DEFAULT_UA,
        "Origin": BASE,
        "Referer": BASE + "/",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _job_url(org_slug: str, job_slug: str) -> str:
    return f"{BASE}/{LANG}/companies/{org_slug}/jobs/{job_slug}"


def _is_france(hit: dict) -> bool:
    """Keep France-based roles (her search is France/Paris). Unknown location → keep."""
    offices = hit.get("offices") or []
    if not offices:
        return True
    return any((o.get("country_code") == "FR") for o in offices)


def _meta(hit: dict) -> dict:
    """Structured fields the Algolia hit carries and the scraper was discarding.

    `experience_level_minimum` is the valuable one: it is a number of YEARS, and on a sample of 20
    hits seven asked for 3-5 of them. A title-based seniority filter cannot see that — none of
    those titles said "senior" — so roles she has no chance at were scoring like open ones. Mapped
    onto the same D/S/E alphabet France Travail uses so fit_score needs no special case.
    """
    yrs = hit.get("experience_level_minimum")
    exp = ""
    if isinstance(yrs, int):
        exp = "D" if yrs <= 0 else ("S" if yrs <= 2 else "E")
    ct = (hit.get("contract_type") or "").upper()
    return {
        "contract": ("alternance" if ct in ("APPRENTICESHIP", "APPRENTICESHIP_CONTRACT",
                                            "PROFESSIONAL_TRAINING_CONTRACT")
                     else "internship" if ct == "INTERNSHIP" else ""),
        "posted": (hit.get("published_at") or "")[:10],
        "experience": exp,
    }


def _location_of(hit: dict) -> str:
    """Human-readable location from the Algolia `offices` array.

    The hit carries a full address — {"city": "Paris", "state": "Ile-de-France", ...} — and it was
    being discarded, so every WTTJ listing reached the pipeline with location=None. That is fine
    for outreach (it emails the company, not the city) but it makes the offer digest unable to tell
    a Paris role from a Bordeaux one, which is the difference between a job she can take and one
    she cannot. Remote-only postings often carry no office at all; those stay empty and are
    classified by the caller.
    """
    offices = hit.get("offices") or []
    if not offices:
        return ""
    o = offices[0]
    parts = [str(o.get(k) or "").strip() for k in ("city", "state", "country")]
    return " - ".join(p for p in parts if p)


def discover(page=None, max_pages: int | None = None) -> list[js.JobListing]:
    """Query the WTTJ Algolia jobs index for AI/Backend/Data roles in France.
    `page` (a Playwright page) is unused here — discovery is pure HTTP. Best-effort:
    a failed query is logged and skipped, never aborts the run."""
    pages_per_query = max_pages if max_pages is not None else 2
    listings: list[js.JobListing] = []
    seen: set[str] = set()

    for category, query in QUERIES.items():
        for n in range(pages_per_query):
            try:
                data = _algolia_query(query, n)
            except urllib.error.HTTPError as e:
                print(f"[wttj]   algolia HTTP {e.code} "
                      f"(public key may have rotated — set WTTJ_ALGOLIA_KEY)")
                break
            except Exception as e:  # noqa: BLE001
                print(f"[wttj]   algolia error: {type(e).__name__}: {e}")
                break

            hits = data.get("hits") or []
            if not hits:
                break
            added = 0
            for h in hits:
                org = h.get("organization") or {}
                org_slug = org.get("slug")
                job_slug = h.get("slug")
                name = (h.get("name") or "").strip()
                if not org_slug or not job_slug or not name:
                    continue
                # Precision: only keep titles that actually match a target role
                # (same gate as the Station F scraper), and France-based roles.
                cat = js.matches_target_role(name)
                if not cat or not _is_france(h):
                    continue
                ju = _job_url(org_slug, job_slug)
                if ju in seen:
                    continue
                seen.add(ju)
                listings.append(js.JobListing(
                    company=(org.get("name") or org_slug).strip(),
                    role=name,
                    company_slug=org_slug,
                    job_url=ju,
                    category=cat,
                    source=NAME,
                    location=_location_of(h),
                    meta=_meta(h),
                ))
                added += 1
            if added:
                print(f"[wttj]   query='{query}' page {n + 1}: +{added} match(es)")
            if n + 1 >= int(data.get("nbPages") or 1):
                break
    return listings


def resolve_company_site(page, listing: js.JobListing) -> str | None:
    """No reliable source for a WTTJ company's external website: the public API exposes
    only the WTTJ slug, and the company pages are client-rendered SPAs. WTTJ is registered
    as a discovery-only source (enrich=False), so this is never called in practice — kept
    to satisfy the source interface and return None rather than waste a futile page load."""
    return None
