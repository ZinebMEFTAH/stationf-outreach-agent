"""LinkedIn job search, through the public guest endpoint. No auth, no browser.

WHY THIS EXISTS. It was the single largest one-off gain in the whole source list: added
2026-09-19, it returned 443 raw postings in Île-de-France, 271 role-matching, 72 alternances,
and **56 employers no other source in this repo had ever surfaced** — IBM, Docaposte, Converteo,
Implicity, Generix, Paris La Défense, Numberly, Albioma, the Health Data Hub. Six of the
applications sent that week came from here and from nowhere else.

The endpoint `/jobs-guest/jobs/api/seeMoreJobPostings/search` is what LinkedIn serves to a logged
-out visitor scrolling the results list. It returns bare `<li>` cards, ten per call, paged by a
`start` offset. No key, no cookie, no JavaScript.

THREE TRAPS, ALL PAID FOR ONCE ALREADY — do not rediscover them:

1. THE LOCATION STRING MUST BE ASCII AND MATCH LINKEDIN'S OWN TAXONOMY. "Île-de-France, France"
   is ACCEPTED, returns HTTP 200, and silently answers with a different region entirely — the
   first harvest came back full of jobs in BREST. There is no error and no empty result to warn
   you. `LOCATION` below is the spelling that works.

2. THE CARD CLASS IS `base-search-card__*`, NOT `job-search-card__title`. The obvious guess
   matches nothing, and "nothing" reads exactly like "this query has no results".

3. LINKEDIN'S "POSTED X AGO" IS ITS OWN DISPLAY, NOT THE EMPLOYER'S DATE. A Generix posting
   showed as "7 hours ago, 33 applicants" on LinkedIn while its SmartRecruiters record gave a
   release date six weeks earlier. Never argue urgency from a LinkedIn timestamp; check the
   employer's own board.

RELIABILITY. LinkedIn is an AGGREGATOR: it mirrors postings and keeps showing them after the
employer has closed them, exactly like HelloWork and Adzuna (5 of 5 HelloWork rows checked on
2026-09-19 were already dead). Treat a row from here as a LEAD TO VERIFY, never as a job — see
the verification recipes in CLAUDE.md. Many rows also carry no apply button at all, which means
the posting lives on an ATS: fingerprint the employer's careers page and apply there.
"""
from __future__ import annotations

import html
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import jobsource as js
import source_lab as _sl

NAME = "linkedin"
JOBS_URL = "https://www.linkedin.com/jobs/search"

_API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

# ⚠ ASCII, and LinkedIn's own spelling. See trap 1 in the module docstring.
LOCATION = "Paris, Ile-de-France, France"

_PER_PAGE = 10          # the endpoint's fixed page size; `start` is an offset, not a page number
_PAGES = 6              # 60 rows per query; past that the results stop being relevant
_PAUSE = 0.5            # be a polite guest

# Contract-first, for the same reason as every other French source here: alternance is the
# scarcity, and a contract-agnostic query returns the whole market. The spread of wordings is
# deliberate — a posting says "IA", "MLOps", "apprenti" or "python", and a query set that only
# said "data", "développeur" and "intelligence artificielle" missed a third of the matches.
QUERIES: dict[str, str] = {
    "ai": "alternance intelligence artificielle",
    "ai2": "alternance IA",
    "ai3": "alternance machine learning",
    "ai4": "alternance deep learning",
    "ai5": "alternance LLM",
    "ai6": "alternance NLP",
    "data": "alternance data",
    "data2": "alternance data scientist",
    "data3": "alternance data engineer",
    "data4": "alternance data analyst",
    "data5": "alternance big data",
    "backend": "alternance developpeur backend",
    "backend2": "alternance python",
    "backend3": "alternance software engineer",
    "mlops": "alternance MLOps",
    "mlops2": "alternance devops",
    "mlops3": "alternance cloud",
    "appr": "apprenti data",
    "appr2": "apprenti developpeur",
    # Measured 2026-09-22 (source_lab.tune): these four outscore every seed above on
    # LinkedIn — 58/58/56/54 against a previous best of 36. None was in any seed list.
    "ai7": "alternance IA generative",
    "ai8": "alternance machine learning engineer",
    "ai9": "alternance ingenieur IA",
    "ai10": "alternance agents IA",
    "appr3": "apprenti ingenieur logiciel",
}

_CARD = re.compile(r"<li>(.*?)</li>", re.S)
_TITLE = re.compile(r'base-search-card__title"?[^>]*>\s*([^<]+)')
_COMPANY = re.compile(r'base-search-card__subtitle"?[^>]*>\s*(?:<a[^>]*>)?\s*([^<]+)')
_PLACE = re.compile(r'job-search-card__location"?[^>]*>\s*([^<]+)')
_LINK = re.compile(r'base-card__full-link[^"]*"\s+href="([^"?]+)')
_POSTED = re.compile(r'datetime="([^"]+)"')


def _clean(m) -> str:
    return html.unescape(m.group(1)).strip() if m else ""


def _parse_card(card: str) -> dict | None:
    """One `<li>` -> a plain dict, or None when the card carries no title or link."""
    title, link = _TITLE.search(card), _LINK.search(card)
    if not (title and link):
        return None
    return {
        "role": _clean(title),
        "company": _clean(_COMPANY.search(card)),
        "location": _clean(_PLACE.search(card)),
        "url": html.unescape(link.group(1)),
        "posted": _POSTED.search(card).group(1) if _POSTED.search(card) else "",
    }


def _search(keywords: str, start: int, location: str = LOCATION) -> list[dict]:
    qs = urllib.parse.urlencode({"keywords": keywords, "location": location, "start": start})
    req = urllib.request.Request(
        f"{_API}?{qs}",
        headers={"User-Agent": js.DEFAULT_UA, "Accept": "text/html"},
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        body = r.read().decode("utf-8", "replace")
    return [c for c in (_parse_card(m) for m in _CARD.findall(body)) if c]


def discover(page=None, max_pages: int | None = None) -> list[js.JobListing]:
    """Role-matching alternance postings in Île-de-France. `page` unused (pure HTTP).

    Best-effort per query: a failure is logged and skipped, never aborts the run.
    """
    pages = max_pages if max_pages is not None else _PAGES
    listings: list[js.JobListing] = []
    seen: set[str] = set()

    # SELF-TUNING ORDER (step 5). Same queries, sent best-first according to what previous
    # runs actually measured on THIS board — the yields differ wildly between boards, and
    # pages_per_query bounds every run, so the order decides what the budget buys.
    # source_lab.plan never drops a query and never invents one; an empty cache is a no-op.
    for category, query in _sl.plan(NAME, list(QUERIES.items())):
        added = 0
        for n in range(pages):
            try:
                rows = _search(query, n * _PER_PAGE)
            except urllib.error.HTTPError as e:
                print(f"[linkedin]   HTTP {e.code} for '{query}'")
                break
            except Exception as e:  # noqa: BLE001
                print(f"[linkedin]   error: {type(e).__name__}: {e}")
                break
            if not rows:
                break
            for r in rows:
                url, company, role = r["url"], r["company"], r["role"]
                if url in seen or len(company) < 2 or not role:
                    continue
                seen.add(url)
                cat = js.matches_target_role(role)
                if not cat or js.excluded_role(role):
                    continue
                listings.append(js.JobListing(
                    company=company,
                    role=role,
                    job_url=url,
                    category=cat,
                    source=NAME,
                    location=r["location"] or None,
                    # `posted` is LinkedIn's own display date — see trap 3. Recorded because the
                    # digest's staleness penalty wants A date, but never trusted as the
                    # employer's posting date.
                    meta={k: v for k, v in (("posted", r["posted"]),) if v},
                ))
                added += 1
            time.sleep(_PAUSE)
        base = category.rstrip("0123456789")
        print(f"[linkedin]   query='{query}' ({base}): +{added} match(es)")
    return listings


def resolve_company_site(page, listing: js.JobListing) -> str | None:
    """LinkedIn does not publish the employer's own domain on a guest card."""
    return None


if __name__ == "__main__":
    found = discover()
    print(f"\n[linkedin] {len(found)} role-matching listings")
    for l in found[:30]:
        print(f"  {l.company[:28]:28} | {l.role[:56]:56} | {(l.location or '')[:18]}")
