"""HelloWork — job source module.

HelloWork (hellowork.com) is one of France's largest job boards, with heavy junior /
alternance volume. Unlike WTTJ it is **server-rendered**: the search results are plain
HTML, so discovery is a straightforward read of the rendered DOM via the shared Playwright
page — stable selectors, no API, no Cloudflare fight.

Registered as a discovery-only source (enrich=False): HelloWork hides the employer's real
website (detail pages only link HelloWork-group properties), so inline contact-enrichment
is futile — rows keep the generic fallback email and lean on /find-contacts.

Conforms to the source interface used by scraper.py:
    NAME, JOBS_URL, discover(page, max_pages), resolve_company_site(page, listing)
"""
from __future__ import annotations

import re

import jobsource as js

NAME = "hellowork"
BASE = "https://www.hellowork.com"
SEARCH = f"{BASE}/fr-fr/emploi/recherche.html"
JOBS_URL = SEARCH

# One focused query per target category; titles are re-checked with matches_target_role.
QUERIES: dict[str, str] = {
    "ai": "machine learning engineer",
    "backend": "backend engineer",
    "data": "data engineer",
}

# Extract (href, title, company) from each server-rendered result card. The anchor's
# innerText is reliably "<role>\n<company>"; the aria-label ("… chez <company>, pour …")
# is a fallback for the company when the text layout differs.
_EXTRACT_JS = r"""
() => {
    const out = [];
    const seen = new Set();
    document.querySelectorAll('a[href*="/emplois/"]').forEach(a => {
        const href = a.getAttribute('href') || '';
        if (!/\/emplois\/\d+/.test(href)) return;
        if (seen.has(href)) return;
        const lines = (a.innerText || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean);
        if (!lines.length) return;
        const title = lines[0];
        let company = lines[1] || '';
        if (!company) {
            const m = (a.getAttribute('aria-label') || '').match(/chez\s+(.+?),/i);
            if (m) company = m[1].trim();
        }
        seen.add(href);
        out.push({ href, title, company });
    });
    return out;
}
"""


def _abs(href: str) -> str:
    return href if href.startswith("http") else BASE + href


def _goto(page, url: str, timeout: int = 35000) -> bool:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(1200)
        return True
    except Exception as e:
        print(f"[hellowork]   nav failed: {url} ({type(e).__name__})")
        return False


def discover(page, max_pages: int | None = None) -> list[js.JobListing]:
    """Crawl HelloWork search for AI/Backend/Data roles (France-wide). Best-effort:
    a failed page is logged and skipped, never aborts the run."""
    pages_per_query = max_pages if max_pages is not None else 2
    if page is None:
        return []
    js.accept_cookies(page)

    listings: list[js.JobListing] = []
    seen: set[str] = set()

    for category, query in QUERIES.items():
        for n in range(1, pages_per_query + 1):
            url = f"{SEARCH}?k={query.replace(' ', '%20')}&p={n}"
            if not _goto(page, url):
                continue
            if n == 1:
                js.accept_cookies(page)
            try:
                rows = page.evaluate(_EXTRACT_JS) or []
            except Exception:
                rows = []
            added = 0
            for r in rows:
                href = _abs(r.get("href") or "")
                if href in seen:
                    continue
                title = (r.get("title") or "").strip()
                cat = js.matches_target_role(title)
                if not cat:
                    continue
                company = re.sub(r"\s+", " ", (r.get("company") or "").strip())
                if not company:
                    continue
                seen.add(href)
                listings.append(js.JobListing(
                    company=company,
                    role=title,
                    job_url=href,
                    category=cat,
                    source=NAME,
                ))
                added += 1
            if added:
                print(f"[hellowork]   query='{query}' page {n}: +{added} match(es)")
            if not rows:
                break  # no results rendered → stop paging this query
    return listings


def resolve_company_site(page, listing: js.JobListing) -> str | None:
    """HelloWork hides the employer's real website, so there is nothing reliable to
    resolve. Registered discovery-only (enrich=False), so this is never called in
    practice — kept to satisfy the source interface."""
    return None
