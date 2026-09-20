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
import source_lab as _sl

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

# See apec.ALTERNANCE_QUERIES — same reasoning. The queries above are contract-agnostic, so
# alternance postings were largely invisible on the one board with the deepest French coverage.
# WIDENED 2026-09-19. The old three-query set was the binding constraint on volume, not the
# boards: measured on HelloWork, 6 queries -> 92 role-matching listings, 22 queries -> 127
# (+38%), 35 of them invisible before — Veolia "Apprenti Création d'Agents LLM", Société
# Générale "Apprenti Data · IA", Safran "Ingénieur Dev Logiciel en IA", Sanofi, Capgemini,
# Valeo, Shiseido, Younited. A posting says "IA", "MLOps", "apprenti" or "python"; the old
# queries only said "data", "développeur" and "intelligence artificielle".
# matches_target_role still gates every title, so extra terms add matches, never noise.
ALTERNANCE_QUERIES: dict[str, str] = {
    "ai": "alternance intelligence artificielle",
    "backend": "alternance développeur",
    "data": "alternance data",
    "ai2": "alternance IA",
    "ai3": "alternance machine learning",
    "ai4": "alternance deep learning",
    "ai5": "alternance LLM",
    "ai6": "alternance NLP",
    "data2": "alternance data scientist",
    "data3": "alternance data engineer",
    "data4": "alternance data analyst",
    "data5": "alternance big data",
    "backend2": "alternance python",
    "backend3": "alternance software engineer",
    "backend4": "alternance backend",
    "mlops": "alternance MLOps",
    "mlops2": "alternance devops",
    "mlops3": "alternance cloud",
    "appr": "apprenti data",
    "appr2": "apprenti développeur",
    "appr3": "apprenti ingénieur",
}


def _query_plan() -> list[tuple[str, str]]:
    """(category, query) pairs to run — the standard queries, then the alternance ones."""
    # SELF-TUNING ORDER (step 5): same pairs, sent best-first by what previous runs
    # MEASURED on this board. source_lab.plan never drops a query and never invents one;
    # an unmeasured or unreadable cache is a no-op, so this can only ever reorder.
    return _sl.plan("france_travail", list(QUERIES.items())
                    + list(ALTERNANCE_QUERIES.items()))

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


def _meta(o: dict) -> dict:
    """Structured fields France Travail publishes on every offer and the scraper was discarding.

    All three are decisive for someone with no professional experience yet, and all three were
    being guessed from the job title instead:
      alternance            an explicit boolean — true on 105 of 216 sampled offers, many of whose
                            titles never use the word.
      experienceExige       "D" débutant accepté / "S" souhaitée / "E" exigée. A clean 117/99 split
                            on the same sample, so it halves the pool into employers who will look
                            at a beginner and employers who will not.
      offresManqueCandidats the employer is short of applicants (14 of 216). The single most
                            direct evidence there is that an application will actually be read.
    """
    return {
        # THE FULL DESCRIPTION, FREE. It arrives in the SAME response as the listing, and was
        # being dropped — so descriptions.fetch() went back over the network for text this
        # source had already handed over (or, for a host it has no reader for, got nothing).
        # Capped: a few postings paste an entire company handbook.
        "description": str(o.get("description") or "")[:20000],
        "contract": ("alternance" if o.get("alternance")
                     or "apprentissage" in str(o.get("natureContrat") or "").lower()
                     or "professionnalisation" in str(o.get("natureContrat") or "").lower()
                     else ""),
        "posted": (o.get("dateCreation") or "")[:10],
        "few_applicants": bool(o.get("offresManqueCandidats")),
        "experience": str(o.get("experienceExige") or ""),
        # ROME is the STATE occupational taxonomy, published on every offer. M18xx is the
        # informatique family, so it says whether this is an IT job as a matter of official
        # classification rather than of job-title fashion. job_family.classify() reads it.
        "rome": str(o.get("romeCode") or "").strip(),
        "rome_label": str(o.get("romeLibelle") or "").strip(),
    }


def discover(page=None, max_pages: int | None = None) -> list[js.JobListing]:
    """Query the France Travail jobs API for AI/Backend/Data roles. `page` unused (pure HTTP).
    Inert (returns []) when credentials are not configured."""
    token = _get_token()
    if not token:
        print("[francetravail]   skipped — set FRANCE_TRAVAIL_ID / FRANCE_TRAVAIL_SECRET to enable")
        return []

    # DEPTH PAYS, AND COSTS NOTHING WHEN IT DOES NOT (2026-09-19). Two pages per query was
    # leaving matches on the table: measured on HelloWork, "alternance data" returns 45
    # role-matching listings over pages 1-2 and 66 over pages 1-5 (+21), while "alternance IA"
    # exhausts itself at page 2 and returns empty — the loop breaks on an empty page, so a
    # query with nothing left costs one wasted request, not five.
    pages_per_query = max_pages if max_pages is not None else 5
    listings: list[js.JobListing] = []
    seen: set[str] = set()

    for category, query in _query_plan():
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
                    location=((o.get("lieuTravail") or {}).get("libelle") or "").strip() or None,
                    meta=_meta(o),
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
