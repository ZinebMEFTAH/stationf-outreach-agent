"""France Travail (ex-Pôle emploi) — job source module.

France Travail's official "Offres d'emploi v2" API is the largest French job database,
with heavy alternance/apprentissage volume. It's a clean OAuth2 REST API — robust, legal,
no scraping.

Activation (one-time): register a free app at https://francetravail.io, subscribe it to the
"Offres d'emploi v2" API, and put the credentials in .env:
    FRANCE_TRAVAIL_ID=...
    FRANCE_TRAVAIL_SECRET=...
Until those are set this source is inert (discover() returns [] with a one-line note), so it
never breaks an `--source all` run.

Discovery-only (enrich=False): the real company domain is recovered by company_resolver /
find-contacts. Anonymous offers (blank company) are skipped.

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

NAME = "francetravail"
TOKEN_URL = ("https://entreprise.francetravail.fr/connexion/oauth2/access_token"
             "?realm=%2Fpartenaire")
SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
JOBS_URL = "https://candidat.francetravail.fr/offres/recherche"
SCOPE = "api_offresdemploiv2 o2dsoffre"
PER_PAGE = 20

QUERIES: dict[str, str] = {
    "ai": "machine learning",
    "backend": "backend",
    "data": "data engineer",
}

_token: str | None = None


def _get_token() -> str | None:
    """Fetch (and cache) an OAuth2 client-credentials token. None if creds missing/fail."""
    global _token
    if _token:
        return _token
    cid, secret = config.FRANCE_TRAVAIL_ID, config.FRANCE_TRAVAIL_SECRET
    if not cid or not secret:
        return None
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": cid,
        "client_secret": secret,
        "scope": SCOPE,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": js.DEFAULT_UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            _token = json.load(r).get("access_token")
    except Exception as e:  # noqa: BLE001
        print(f"[francetravail]   token error: {type(e).__name__} (check FRANCE_TRAVAIL_ID/SECRET)")
        _token = None
    return _token


def _search(token: str, query: str, start: int, count: int = PER_PAGE) -> dict | None:
    params = urllib.parse.urlencode({"motsCles": query, "range": f"{start}-{start + count - 1}"})
    req = urllib.request.Request(f"{SEARCH_URL}?{params}", headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": js.DEFAULT_UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            if r.status == 204:  # no results
                return {"resultats": []}
            return json.load(r)
    except urllib.error.HTTPError as e:
        print(f"[francetravail]   API HTTP {e.code} for '{query}'")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"[francetravail]   API error: {type(e).__name__}: {e}")
        return None


def _job_url(offer: dict) -> str:
    url = (offer.get("origineOffre") or {}).get("urlOrigine")
    return url or f"{JOBS_URL}/detail/{offer.get('id', '')}"


def discover(page=None, max_pages: int | None = None) -> list[js.JobListing]:
    """Query the France Travail jobs API for AI/Backend/Data roles. `page` unused (pure HTTP).
    Inert (returns []) when credentials are not configured."""
    token = _get_token()
    if not token:
        print("[francetravail]   skipped — set FRANCE_TRAVAIL_ID / FRANCE_TRAVAIL_SECRET to enable")
        return []

    pages_per_query = max_pages if max_pages is not None else 2
    listings: list[js.JobListing] = []
    seen: set[str] = set()

    for category, query in QUERIES.items():
        for n in range(pages_per_query):
            data = _search(token, query, n * PER_PAGE)
            if data is None:
                break
            results = data.get("resultats") or []
            if not results:
                break
            added = 0
            for o in results:
                oid = o.get("id")
                title = (o.get("intitule") or "").strip()
                company = ((o.get("entreprise") or {}).get("nom") or "").strip()
                if not oid or not title or len(company) < 2:
                    continue  # skip anonymous offers with no company to contact
                cat = js.matches_target_role(title)
                if not cat or oid in seen:
                    continue
                seen.add(oid)
                listings.append(js.JobListing(
                    company=company,
                    role=title,
                    job_url=_job_url(o),
                    category=cat,
                    source=NAME,
                ))
                added += 1
            if added:
                print(f"[francetravail]   query='{query}' page {n + 1}: +{added} match(es)")
            if len(results) < PER_PAGE:
                break
    return listings


def resolve_company_site(page, listing: js.JobListing) -> str | None:
    """France Travail exposes no company website. Discovery-only (enrich=False), so this
    is never called — kept to satisfy the source interface."""
    return None
