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

import html as _html
import re
import urllib.parse
import urllib.request

import jobsource as js
import source_lab as _sl

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

# Contract-first queries for the HTTP path. Alternance is the binding scarcity, and the
# generic queries above are contract-agnostic, so the board with the heaviest alternance
# volume in France was being searched as though she wanted any job at all.
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
    # Measured 2026-09-22 (source_lab.tune): best query on this board (50 / 39),
    # ahead of every seed here, and absent from every seed list until now.
    "ai7": "alternance ingenieur IA",
    "ai8": "alternance agents IA",
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
    # DEPTH PAYS, AND COSTS NOTHING WHEN IT DOES NOT (2026-09-19). Two pages per query was
    # leaving matches on the table: measured on HelloWork, "alternance data" returns 45
    # role-matching listings over pages 1-2 and 66 over pages 1-5 (+21), while "alternance IA"
    # exhausts itself at page 2 and returns empty — the loop breaks on an empty page, so a
    # query with nothing left costs one wasted request, not five.
    pages_per_query = max_pages if max_pages is not None else 5
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


# ── HTTP path (no browser) ───────────────────────────────────────────────────
# discover() needs a live Playwright page, which is why opportunities.py — pure Python on a
# cron — excluded HelloWork entirely: one of France's largest boards, already implemented in
# this repo, invisible to the digest. But the search results are genuinely server-rendered,
# so a browser was never required to READ them; it was only required by how this module
# happened to be written. 2026-09-18: a plain GET returns ~624 KB containing 30 result cards.
#
# Every field comes from ONE attribute. Each card's anchor carries
#   aria-label="Voir offre de <TITLE> à <LIEU>, chez <SOCIÉTÉ>, pour un <CONTRAT>, …"
# which is more reliable than scraping the visible text: it is written for screen readers, so
# it stays complete and in a fixed order even when the visual layout changes.
_CARD = re.compile(r'<a\b[^>]*data-cy="offerTitle"[^>]*>', re.I)
_HREF = re.compile(r'href="([^"]+)"')
_ARIA = re.compile(r'aria-label="([^"]+)"')
_ARIA_PARTS = re.compile(
    r"Voir offre de (?P<title>.+?) à (?P<location>.+?), chez (?P<company>.+?), "
    r"pour un (?P<contract>[^,]+)")

_HTTP_HEADERS = {
    "User-Agent": js.DEFAULT_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


def _http_search(query: str, page_no: int = 1, contract: str = "Alternance",
                 region: str = "Ile-de-France") -> list[dict]:
    """One page of HelloWork search results, parsed from the server-rendered HTML."""
    params = {"k": query, "p": page_no}
    if contract:
        params["c"] = contract
    if region:
        params["l"] = region
    url = f"{SEARCH}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers=_HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=25) as r:
            page = r.read(2_000_000).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print(f"[hellowork]   http error for '{query}': {type(e).__name__}: {e}")
        return []

    out = []
    for tag in _CARD.findall(page):
        href = _HREF.search(tag)
        aria = _ARIA.search(tag)
        if not (href and aria):
            continue
        m = _ARIA_PARTS.search(_html.unescape(aria.group(1)))
        if not m:
            continue
        out.append({
            "url": _abs(href.group(1)),
            "role": _html.unescape(m.group("title")).strip(),
            "company": _html.unescape(m.group("company")).strip(),
            "location": _html.unescape(m.group("location")).strip(),
            "contract": _html.unescape(m.group("contract")).strip(),
        })
    return out


def discover_http(max_pages: int | None = None) -> list[js.JobListing]:
    """Browser-free discovery, for the pure-Python digest. Alternance-first."""
    pages = max_pages if max_pages is not None else 2
    listings: list[js.JobListing] = []
    seen: set[str] = set()
    # SELF-TUNING ORDER (step 5). Same queries, sent best-first according to what previous
    # runs actually measured on THIS board — the yields differ wildly between boards, and
    # pages_per_query bounds every run, so the order decides what the budget buys.
    # source_lab.plan never drops a query and never invents one; an empty cache is a no-op.
    for category, query in _sl.plan(NAME, list(ALTERNANCE_QUERIES.items())
                                   + list(QUERIES.items())):
        added = 0
        for n in range(1, pages + 1):
            rows = _http_search(query, n)
            if not rows:
                break
            for r in rows:
                if r["url"] in seen or len(r["company"]) < 2:
                    continue
                cat = js.matches_target_role(r["role"])
                if not cat:
                    continue
                seen.add(r["url"])
                listings.append(js.JobListing(
                    company=r["company"], role=r["role"], job_url=r["url"],
                    category=cat, source=NAME, location=r["location"] or None,
                    meta={"contract": ("alternance"
                                       if re.search(r"alternan|apprenti", r["contract"], re.I)
                                       else "")}))
                added += 1
        if added:
            print(f"[hellowork]   query='{query}': +{added} match(es)")
    return listings
