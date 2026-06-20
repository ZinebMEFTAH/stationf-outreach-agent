"""APEC — job source module.

APEC (apec.fr) is France's reference board for cadre / engineering roles, with strong
AI/Backend/Data and alternance volume. Its Angular UI is backed by a PUBLIC JSON search
API (`POST /cms/webservices/rechercheOffre`) that works over plain HTTP — no browser, no
session. Discovery is a stdlib HTTP client; ~97% of results carry a real company name.

Registered as a discovery-only source (enrich=False): APEC exposes no company website and
its detail pages are SPAs, so the real domain is recovered later by company_resolver /
find-contacts. Confidential/agency offers (blank company) are skipped.

Conforms to the source interface used by scraper.py:
    NAME, JOBS_URL, discover(page, max_pages), resolve_company_site(page, listing)
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import jobsource as js

NAME = "apec"
BASE = "https://www.apec.fr"
API = f"{BASE}/cms/webservices/rechercheOffre"
JOBS_URL = f"{BASE}/candidat/recherche-emploi.html/emploi"
PER_PAGE = 20

# One focused query per target category; titles are re-checked with matches_target_role.
QUERIES: dict[str, str] = {
    "ai": "machine learning",
    "backend": "backend",
    "data": "data engineer",
}

# Default contract-convention codes the APEC UI sends (CDI/CDD/alternance/etc.). Kept as
# captured from the site so the result set matches what a user sees in the browser.
_TYPES_CONVENTION = ["143684", "143685", "143686", "143687", "143706"]


def _search(query: str, start_index: int, count: int = PER_PAGE) -> dict:
    payload = {
        "lieux": [], "fonctions": [], "statutPoste": [], "typesContrat": [],
        "typesConvention": _TYPES_CONVENTION, "niveauxExperience": [],
        "idsEtablissement": [], "secteursActivite": [], "typesTeletravail": [],
        "idNomZonesDeplacement": [], "positionNumbersExcluded": [],
        "typeClient": "CADRE",
        "sorts": [{"type": "SCORE", "direction": "DESCENDING"}],
        "pagination": {"range": count, "startIndex": start_index},
        "activeFiltre": True,
        "pointGeolocDeReference": {"distance": 0},
        "motsCles": query,
    }
    req = urllib.request.Request(API, data=json.dumps(payload).encode(), method="POST",
                                headers={
                                    "Content-Type": "application/json",
                                    "Accept": "application/json",
                                    "User-Agent": js.DEFAULT_UA,
                                    "Origin": BASE,
                                    "Referer": JOBS_URL,
                                })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _job_url(numero: str) -> str:
    return f"{BASE}/candidat/recherche-emploi.html/detail-offre/{numero}"


def discover(page=None, max_pages: int | None = None) -> list[js.JobListing]:
    """Query the APEC jobs API for AI/Backend/Data roles. `page` is unused (pure HTTP).
    Best-effort: a failed query is logged and skipped, never aborts the run."""
    pages_per_query = max_pages if max_pages is not None else 2
    listings: list[js.JobListing] = []
    seen: set[str] = set()

    for category, query in QUERIES.items():
        for n in range(pages_per_query):
            try:
                data = _search(query, n * PER_PAGE)
            except urllib.error.HTTPError as e:
                print(f"[apec]   API HTTP {e.code} for '{query}'")
                break
            except Exception as e:  # noqa: BLE001
                print(f"[apec]   API error: {type(e).__name__}: {e}")
                break

            results = data.get("resultats") or []
            if not results:
                break
            added = 0
            for o in results:
                numero = o.get("numeroOffre")
                title = (o.get("intitule") or "").strip()
                company = (o.get("nomCommercial") or "").strip()
                if not numero or not title or len(company) < 2:
                    continue  # skip confidential / agency offers with no company
                cat = js.matches_target_role(title)
                if not cat:
                    continue
                if numero in seen:
                    continue
                seen.add(numero)
                listings.append(js.JobListing(
                    company=company,
                    role=title,
                    job_url=_job_url(numero),
                    category=cat,
                    source=NAME,
                ))
                added += 1
            if added:
                print(f"[apec]   query='{query}' page {n + 1}: +{added} match(es)")
            total = int(data.get("totalCount") or 0)
            if (n + 1) * PER_PAGE >= total:
                break
    return listings


def resolve_company_site(page, listing: js.JobListing) -> str | None:
    """APEC exposes no company website (and detail pages are SPAs). Discovery-only
    (enrich=False), so this is never called — kept to satisfy the source interface."""
    return None
