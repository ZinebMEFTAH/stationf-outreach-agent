"""Adzuna — job source module. The channel that reaches employers nothing else here can.

Adzuna aggregates French postings from employer career sites and boards alike, and on
2026-09-16 it turned out to be the ONLY way this repo can see several of Zineb's CFA school
partners. Those employers do not syndicate to France Travail (11 of her 14 partners return zero
there) and their own careers sites are unreadable: BNP Paribas answers 403, Société Générale is
Taleo, Capgemini SuccessFactors, AXA and Expleo iCIMS — all JavaScript-rendered, and the digest is
pure Python on a cron by design. Measured through Adzuna the same day:

    BNP Paribas 125 alternance postings · EDF 71 · Capgemini 39 · Thales 39
    CGI 17 · Société Générale 12 · Expleo 6

and, for the digest as a whole, 12 API calls returned 342 distinct postings, 228 matching her
target roles at junior level, 73 of them in Île-de-France — against the 2 alternances the entire
56-employer company_boards channel could see that morning.

Activation (one-time): register free at https://developer.adzuna.com/signup — note it REQUIRES an
organisation name and website, which for an individual is simply her own name and portfolio — then
put the credentials in .env:
    ADZUNA_APP_ID=...
    ADZUNA_APP_KEY=...
Until those are set this source is inert (discover() returns [] with a one-line note), so it never
breaks an `--source all` run. THE KEY MUST BE ON THE VM: the Mac is dev-only and its .env reaches
nothing — the same lesson as the 2026-09 Hunter outage.

Quota: the free "Trial Access" plan is rate-limited, so the query plan is deliberately SMALL —
QUERIES x PAGES is a dozen calls, run once a night, not a crawl. Widening it is not free.

Discovery-only (enrich=False): the real company domain is recovered later by company_resolver /
find-contacts. Anonymous rows are skipped — Adzuna publishes literal placeholder employers like
"Stage" where the source board named none, and a row with no company is nobody to write to.

Conforms to the source interface used by scraper.py:
    NAME, JOBS_URL, discover(page, max_pages), resolve_company_site(page, listing)
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import config
import jobsource as js
import source_lab as _sl
import labonnealternance

NAME = "adzuna"
API = "https://api.adzuna.com/v1/api/jobs/fr/search"
JOBS_URL = "https://www.adzuna.fr/"
PER_PAGE = 50          # the API's maximum
PAGES = 2              # 100 rows per query; past that the results stop being relevant
_PAUSE = 0.25          # be a polite client on a free plan

# Contract-first queries: the digest's binding scarcity is ALTERNANCE, not postings in general
# (239 reachable offers on 2026-09-15, only 16 of them alternance). Each query names the contract
# AND a domain, because "alternance" alone returns every trade in France.
QUERIES: dict[str, str] = {
    "data": "alternance data",
    "backend": "alternance développeur",
    "ai": "alternance intelligence artificielle",
}
EXTRA_QUERIES: dict[str, str] = {
    "ai": "alternance machine learning",
    "backend": "alternance informatique",
    "data": "apprenti développeur",
    # Widened 2026-09-19, but DELIBERATELY LESS than the French boards: Adzuna's free tier is
    # rate-limited and this file's note above stands — widening it is not free. These six are the
    # terms that produced genuinely new employers when measured on HelloWork.
    "ai2": "alternance IA",
    "data2": "alternance data scientist",
    "data3": "alternance data engineer",
    "mlops": "alternance devops",
    "backend2": "alternance python",
    "appr": "apprenti data",
}

# Adzuna names a placeholder employer when the originating board did not publish one. These are
# not companies and must never become outreach rows.
_PLACEHOLDER_COMPANY = re.compile(
    r"^(stage|alternance|apprentissage|confidentiel|entreprise|recruteur|company|n/?a)$", re.I)

# SCHOOLS POSTING ON BEHALF OF A PARTNER COMPANY — a pattern tracker.is_training_body cannot see,
# because the giveaway is in the DESCRIPTION and the company name looks like an employer.
# Measured 2026-09-16, the first full day Adzuna was live: THREE of the digest's top five were
# these — "INTED GROUP" (an alternance "proposée par une entreprise partenaire du Groupe IEG"),
# "Livecampus" ("recrute pour l'une de ses entreprises partenaires"), "Cybersup" ("dans le cadre
# de son programme Mastère IA Data … pour l'un de ses partenaires"). None is a school by name.
#
# They are unusable for Zineb specifically: applying means enrolling in THAT school's programme,
# and she is already enrolled at Université Paris Cité. So the posting is not an opening she can
# take, whatever its title says — the same class of waste as a CFA recruiting students directly.
#
# Adzuna returns the description, so the signal is free. Anchored on the INTERMEDIARY phrasing
# ("for one of its partner companies", "as part of its Mastère programme"), not on the bare word
# "partenaire" — a real employer saying "notre partenaire industriel" must not be caught.
# `['\u2019]?` throughout: French job boards mix the straight apostrophe with the typographic one
# (U+2019), and a pattern written with only one silently matches half the corpus.
_AP = r"['\u2019]?"
_SCHOOL_INTERMEDIARY = re.compile(
    rf"pour (?:l{_AP}\s?un|l{_AP}\s?une|un|une) de (?:ses|nos) (?:entreprises? )?partenaires?"
    rf"|(?:entreprises?|soci[ée]t[ée]s?) partenaires? d[eu]"
    rf"|dans le cadre d{_AP}\s?un partenariat avec (?:une|l{_AP})"
    rf"|dans le cadre de (?:son|notre) programme"
    rf"|(?:notre|nos) (?:[ée]coles?|centres? de formation|campus)"
    # "Un partenaire de l'école OpenClassrooms recherche un Data Scientist" — the school is named,
    # so "notre école" never fires. Two such rows sat at ★82 in the aggregate on 2026-09-18, and
    # both were originally posted in MARCH 2025: these listings get recycled, so a stale job keeps
    # arriving with a fresh index date. Matched on the PHRASE, never on the school's name —
    # OpenClassrooms also hires engineers for itself, and blocking the name would lose those.
    rf"|partenaires? de l{_AP}\s?[ée]cole"
    rf"|l{_AP}\s?[ée]cole [A-ZÉÈ][\w-]+ (?:recherche|recrute)"
    # The verb + possessive is NOT enough on its own: "nous recherchons pour notre équipe data un
    # alternant" is ordinary employer phrasing, and an earlier version of this line refused it —
    # 87 postings skipped in one run against 23 for the correct rule. So "partenaire" or "client"
    # must actually appear, within the same sentence and close by.
    rf"|(?:recrut|recherch|cherch)\w* pour (?:l{_AP}\s?un|l{_AP}\s?une|son|notre|ses|nos)"
    rf"[^.]{{0,40}}?(?:partenaires?|clients?)\b"
    # "Nous recherchons pour notre entreprise partenaire …" — REDSUP posted exactly this twice on
    # 2026-09-18 and walked through, because the first version of this pattern only knew
    # "recrutons". Same sentence, different verb. Also covers the ESN phrasing, where the
    # advertised company is the agency and the real employer is never named.
    rf"|notre client (?:recherche|recrute|souhaite)"
    rf"|pour (?:le compte de |)(?:l{_AP}\s?un|l{_AP}\s?une) de (?:ses|nos) clients?",
    re.I)


def looks_like_school_intermediary(text: str) -> bool:
    """True when the poster is a school placing a student with a partner firm, not the employer."""
    return bool(_SCHOOL_INTERMEDIARY.search(text or ""))


def _query_plan() -> list[tuple[str, str]]:
    # SELF-TUNING ORDER (step 5): same pairs, sent best-first by what previous runs
    # MEASURED on this board. source_lab.plan never drops a query and never invents one;
    # an unmeasured or unreadable cache is a no-op, so this can only ever reorder.
    return _sl.plan("adzuna", list(QUERIES.items())
                    + list(EXTRA_QUERIES.items()))


def _search(what: str, page: int = 1) -> dict | None:
    if not (config.ADZUNA_APP_ID and config.ADZUNA_APP_KEY):
        return None
    params = urllib.parse.urlencode({
        "app_id": config.ADZUNA_APP_ID, "app_key": config.ADZUNA_APP_KEY,
        "results_per_page": PER_PAGE, "what": what,
        # `content-type` keeps the payload JSON; `sort_by=date` puts the freshest first, which
        # matters because a stale posting is scored down hard by the digest anyway.
        "sort_by": "date",
    })
    req = urllib.request.Request(f"{API}/{page}?{params}",
                                 headers={"Accept": "application/json", "User-Agent": js.DEFAULT_UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # 429 is the free plan's rate limit, not a bug — say so plainly rather than looking broken.
        note = " (rate limit — free plan)" if e.code == 429 else ""
        print(f"[adzuna]   API HTTP {e.code}{note} for '{what}'")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"[adzuna]   API error: {type(e).__name__}: {e}")
        return None


def _location(o: dict) -> str:
    loc = o.get("location") or {}
    name = (loc.get("display_name") or "").strip()
    # `area` is coarse-to-fine ["France", "Ile-de-France", "Paris", ...]. display_name is usually
    # the two finest parts ("9ème Arrondissement, Paris"), which can omit the region the digest's
    # Île-de-France gate looks for — so append it when it is not already in the string.
    area = [str(a).strip() for a in (loc.get("area") or []) if str(a).strip()]
    region = next((a for a in area if "ile-de-france" in a.lower().replace("î", "i")), "")
    if region and region.lower() not in name.lower():
        name = f"{name}, {region}" if name else region
    return name


def _meta(o: dict) -> dict:
    """What Adzuna publishes per offer. It has no alternance flag, so the TITLE is the evidence.

    `contract_type` is permanent/contract and `contract_time` full_time/part_time — neither
    expresses a French alternance, so neither is used for it. Reading them as one would label
    every apprenticeship a permanent role.
    """
    title = (o.get("title") or "")
    alt = bool(re.search(r"\b(alternan\w*|apprenti\w*|contrat\s+pro\w*)\b", title, re.I))
    return {
        "contract": "alternance" if alt else "",
        "posted": (o.get("created") or "")[:10],
        "source_board": "adzuna",
    }


def discover(page=None, max_pages: int | None = None,
             require_company: bool = True) -> list[js.JobListing]:
    """Query Adzuna France for AI/Backend/Data alternance roles. `page` unused (pure HTTP).
    Inert (returns []) when credentials are not configured."""
    if not (config.ADZUNA_APP_ID and config.ADZUNA_APP_KEY):
        print("[adzuna]   skipped — set ADZUNA_APP_ID / ADZUNA_APP_KEY to enable")
        return []

    pages = max_pages if max_pages is not None else PAGES
    listings: list[js.JobListing] = []
    seen: set[str] = set()
    skipped_school = 0

    for category, what in _query_plan():
        added = 0
        for n in range(pages):
            data = _search(what, n + 1)
            if data is None:
                break
            results = data.get("results") or []
            if not results:
                break
            for o in results:
                oid = str(o.get("id") or "")
                title = (o.get("title") or "").strip()
                company = ((o.get("company") or {}).get("display_name") or "").strip()
                if not oid or not title or oid in seen:
                    continue
                if len(company) < 2 or _PLACEHOLDER_COMPANY.match(company):
                    # Outreach must skip these — there is nobody to write to. The digest must NOT:
                    # she opens the link herself, and a real Paris alternance is worth a slot even
                    # when the originating board withheld the employer. Same split as La Bonne
                    # Alternance, and it reuses that module's label so the two read identically
                    # rather than showing Adzuna's raw placeholder ("Stage") as a company name.
                    if require_company:
                        continue
                    company = labonnealternance.ANONYMOUS_EMPLOYER
                cat = js.matches_target_role(title)
                if not cat:
                    continue
                if looks_like_school_intermediary(o.get("description") or ""):
                    skipped_school += 1
                    continue
                seen.add(oid)
                listings.append(js.JobListing(
                    company=company,
                    role=title,
                    job_url=(o.get("redirect_url") or "").strip(),
                    category=cat,
                    source=NAME,
                    location=_location(o) or None,
                    meta=_meta(o),
                ))
                added += 1
            time.sleep(_PAUSE)
        if added:
            print(f"[adzuna]   query='{what}': +{added} match(es)")
    if skipped_school:
        print(f"[adzuna]   {skipped_school} posting(s) skipped — a school recruiting for a "
              f"partner company, not an employer")
    return listings


def resolve_company_site(page=None, listing=None) -> str | None:
    """Adzuna publishes no employer website — company_resolver recovers it later."""
    return None


if __name__ == "__main__":  # pragma: no cover - manual inspection
    for l in discover():
        print(f"{(l.meta or {}).get('contract',''):11} {str(l.location)[:30]:30} "
              f"{l.company[:22]:22} {l.role[:48]}")
