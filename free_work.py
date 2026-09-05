"""Free-Work — job source module.

Free-Work (free-work.com) is a large French IT/tech board. It's freelance-heavy, but also
carries permanent (CDI), alternance and internship tech roles. Its Angular UI is backed by a
PUBLIC JSON API (`GET /api/job_postings`) over plain HTTP — no browser, no key.

We filter OUT freelance/contractor-only offers (not relevant to a work-contract search) and
keep permanent / apprenticeship / fixed-term / internship. Discovery-only (enrich=False):
the real domain is recovered by company_resolver / find-contacts.

Conforms to the source interface used by scraper.py:
    NAME, JOBS_URL, discover(page, max_pages), resolve_company_site(page, listing)
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import jobsource as js

NAME = "freework"
BASE = "https://www.free-work.com"
API = f"{BASE}/api/job_postings"
JOBS_URL = f"{BASE}/fr/tech-it/jobs"
PER_PAGE = 20

QUERIES: dict[str, str] = {
    "ai": "machine learning",
    "backend": "backend",
    "data": "data engineer",
}

# Keep only work-contract roles; drop freelance/contractor-only (Free-Work's main stock).
_RELEVANT_CONTRACTS = {"permanent", "apprenticeship", "fixed-term", "internship"}


def _search(query: str, page: int) -> list[dict]:
    params = urllib.parse.urlencode({
        "page": page, "itemsPerPage": PER_PAGE,
        "searchKeywords": query, "locationKeys": "fr~~~",
    })
    req = urllib.request.Request(f"{API}?{params}", headers={
        "User-Agent": js.DEFAULT_UA, "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    return data if isinstance(data, list) else (data.get("hydra:member") or [])


def _job_url(offer: dict) -> str:
    job = offer.get("job") or {}
    cat = job.get("nameForContributionSlug") if isinstance(job, dict) else None
    slug = offer.get("slug") or ""
    if cat and slug:
        return f"{BASE}/fr/tech-it/job-mission/{cat}/{slug}"
    return JOBS_URL


_FW_EXPERIENCE = {"junior": "D", "entry": "D", "beginner": "D",
                  "intermediate": "S", "senior": "E", "expert": "E"}


def _meta(offer: dict) -> dict:
    """Structured fields the Free-Work API carries and the scraper was discarding.

    `experienceLevel` matters most: it was "senior" on 11 of 20 sampled offers, none of whose
    titles said so, and the title-based seniority filter therefore let all eleven through. Mapped
    onto France Travail's D/S/E alphabet so fit_score treats every board the same way.
    `expiredAt` is better than any link check — the board states outright when the posting dies.
    """
    contracts = {str(c).lower() for c in (offer.get("contracts") or [])}
    return {
        "contract": ("alternance" if contracts & {"apprenticeship", "alternance", "apprentissage"}
                     else "internship" if "internship" in contracts else ""),
        "posted": (offer.get("publishedAt") or "")[:10],
        "expires": (offer.get("expiredAt") or "")[:10],
        "experience": _FW_EXPERIENCE.get(str(offer.get("experienceLevel") or "").lower(), ""),
    }


def _location_of(offer: dict) -> str:
    """City + region from the offer's `location` object, falling back to the company's.

    The API returns a full address ({"shortLabel": "Paris", "adminLevel1": "Île-de-France"}) and it
    was being dropped, so every Free-Work listing arrived with location=None. Outreach does not
    care — it emails the company — but the offer digest cannot tell a Paris role from a Marseille
    one without it, and that is the difference between a job she can take and one she cannot.
    """
    for src in (offer.get("location"), (offer.get("company") or {}).get("location")):
        if isinstance(src, dict):
            city = (src.get("shortLabel") or src.get("adminLevel2") or src.get("locality") or "").strip()
            region = (src.get("adminLevel1") or "").strip()
            parts = [p for p in (city, region) if p]
            if parts:
                return " - ".join(dict.fromkeys(parts))
    return ""


def discover(page=None, max_pages: int | None = None) -> list[js.JobListing]:
    """Query the Free-Work jobs API for AI/Backend/Data CDI/alternance roles. `page` unused
    (pure HTTP). Best-effort: a failed query is logged and skipped, never aborts the run."""
    pages_per_query = max_pages if max_pages is not None else 2
    listings: list[js.JobListing] = []
    seen: set[str] = set()

    for category, query in QUERIES.items():
        for n in range(1, pages_per_query + 1):
            try:
                results = _search(query, n)
            except urllib.error.HTTPError as e:
                print(f"[freework]   API HTTP {e.code} for '{query}'")
                break
            except Exception as e:  # noqa: BLE001
                print(f"[freework]   API error: {type(e).__name__}: {e}")
                break

            if not results:
                break
            added = 0
            for o in results:
                title = (o.get("title") or "").strip()
                comp = o.get("company") or {}
                company = (comp.get("name") if isinstance(comp, dict) else comp) or ""
                company = company.strip()
                key = str(o.get("id") or o.get("slug") or "")
                if not title or len(company) < 2 or not key or key in seen:
                    continue
                contracts = set(o.get("contracts") or [])
                # Drop freelance/contractor-only offers (keep if relevant type or unknown).
                if contracts and not (contracts & _RELEVANT_CONTRACTS):
                    continue
                cat = js.matches_target_role(title)
                if not cat:
                    continue
                seen.add(key)
                listings.append(js.JobListing(
                    company=company,
                    role=title,
                    job_url=_job_url(o),
                    category=cat,
                    source=NAME,
                    location=_location_of(o),
                    meta=_meta(o),
                ))
                added += 1
            if added:
                print(f"[freework]   query='{query}' page {n}: +{added} match(es)")
            if len(results) < PER_PAGE:
                break
    return listings


def resolve_company_site(page, listing: js.JobListing) -> str | None:
    """Free-Work exposes no company website. Discovery-only (enrich=False), so this is
    never called — kept to satisfy the source interface."""
    return None
