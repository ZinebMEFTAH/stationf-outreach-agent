"""Job boards that only render in a real browser — the interactive channel.

WHY THIS EXISTS NOW AND NOT BEFORE. Every "cannot be read" verdict in this repo was reached under
one constraint: *the digest is pure Python on a cron, on a 1GB e2-micro, BY DESIGN*. Zineb removed
the cron on 2026-09-20 — *"i do not want it to be scheduled, i want it to do it with u here each
time i can"* — so `/apply` runs on her Mac with Playwright available, and the boards written off
for needing a browser came back into scope.

⚠ OPTIONAL BY CONSTRUCTION. `available()` is checked before anything, and every entry point
returns [] when Playwright or chromium is missing. The VM's 08:00 job still runs pure Python and
must never break because a browser is absent — so this module may only ever ADD listings.

⚠ PACE IT. Indeed serves 48 cards to a first visit and nothing to three rapid ones; that is
automation detection, not a selector bug (the page still titles itself "plus de 100 emplois").
The same lesson LinkedIn taught when it returned 45 throttles in one queue build. `_GAP` seconds
between navigations, and a single browser context reused across queries so the session looks like
one person rather than N.

⚠ INDEED IS SCHOOL-INFESTED on alternance queries — the first eight cards of a "alternance data"
search were ISCOD and Galileo Global Education. That is fine and expected: `brief.py` demotes
schools to the bottom of the queue rather than dropping them, precisely because the name test
cannot tell a course-seller from an edtech employer.
"""
from __future__ import annotations

import re
import time

import jobsource as js

NAME = "indeed"
_GAP = 2.5                       # seconds between navigations — see the pacing note above
_TIMEOUT = 45000
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")

# Contract-first, for the same reason every other French source here is: alternance is the
# scarcity, and a contract-agnostic query spends the budget on jobs she cannot take.
QUERIES: dict[str, str] = {
    "ai": "alternance intelligence artificielle",
    "ai2": "alternance IA",
    "data": "alternance data",
    "data2": "alternance data engineer",
    "backend": "alternance développeur",
    "mlops": "alternance devops",
    "appr": "apprenti data",
}

_CONSENT = ("#onetrust-accept-btn-handler", "button:has-text('Tout accepter')",
            "#didomi-notice-agree-button", "button:has-text('Accepter')")


def available() -> bool:
    """Is a browser usable here at all? Never raises — absence is a normal state."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:                                          # noqa: BLE001
        return False
    try:
        with sync_playwright() as p:
            return bool(p.chromium.executable_path)
    except Exception:                                          # noqa: BLE001
        return False


def _consent(pg) -> None:
    for sel in _CONSENT:
        try:
            el = pg.query_selector(sel)
            if el and el.is_visible():
                el.click()
                return
        except Exception:                                      # noqa: BLE001
            continue


def _cards(pg) -> list[dict]:
    """One Indeed results page -> rows. `job_seen_beacon` is the CARD; the nested
    `resultContent`/`cardOutline` match the same job again, so selecting all three triples
    every row."""
    out = []
    for c in pg.query_selector_all("div.job_seen_beacon"):
        def txt(sel: str) -> str:
            el = c.query_selector(sel)
            return (el.inner_text() or "").strip().replace("\n", " ") if el else ""
        # ⚠ THE TITLE LIVES IN h3.jobTitle a — NOT h2. Guessing h2 cost a run that reported
        # "0 role-matching listings" while the page itself was titled "plus de 100 emplois" and
        # 16 cards were sitting in the DOM: a wrong selector reads exactly like a dead source.
        a = c.query_selector("h3.jobTitle a") or c.query_selector("h2 a") or c.query_selector("a")
        title = (a.inner_text() or "").strip().replace("\n", " ") if a else ""
        if not title and a:
            # Fallback: the aria-label, which wraps it as « tous les détails sur "TITLE" ».
            lab = a.get_attribute("aria-label") or ""
            m = re.search(r"[«\"]\s*(.+?)\s*[»\"]", lab)
            title = (m.group(1) if m else lab).strip()
        title = re.sub(r"^(?:complet\s*:\s*|nouveau\s*)", "", title, flags=re.I).strip()
        href = (a.get_attribute("href") or "") if a else ""
        if not title or not href:
            continue
        out.append({"role": title,
                    "company": txt("[data-testid='company-name']"),
                    "location": txt("[data-testid='text-location']"),
                    "url": href if href.startswith("http") else f"https://fr.indeed.com{href}"})
    return out


def discover(page=None, max_pages: int | None = None) -> list[js.JobListing]:
    """Indeed via a real browser. Returns [] — never raises — when no browser is available."""
    if not available():
        print("[indeed]   no browser available — skipped (this source is optional)")
        return []
    from playwright.sync_api import sync_playwright

    pages = max_pages if max_pages is not None else 1
    import source_lab as _sl
    plan = _sl.plan(NAME, list(QUERIES.items()))

    listings: list[js.JobListing] = []
    seen: set[str] = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for category, query in plan:
                # ⚠ A FRESH CONTEXT PER QUERY. Indeed throttles a REUSED session hard: the first
                # query returns 16 cards and every query after it returns ZERO, even with a 6s
                # gap — which reads exactly like "this board has nothing", the most expensive
                # kind of silent failure. A new context (new cookies, new fingerprint) restores
                # it: measured 16 / 16 / 16 across three queries instead of 16 / 0 / 0.
                ctx = browser.new_context(locale="fr-FR", user_agent=_UA,
                                          viewport={"width": 1400, "height": 1000})
                pg = ctx.new_page()
                first = True
                for n in range(pages):
                    url = ("https://fr.indeed.com/jobs?q=" + query.replace(" ", "+")
                           + "&l=Paris&start=" + str(n * 10))
                    try:
                        pg.goto(url, wait_until="domcontentloaded", timeout=_TIMEOUT)
                    except Exception as e:                     # noqa: BLE001
                        print(f"[indeed]   '{query}': {type(e).__name__} — skipped")
                        break
                    pg.wait_for_timeout(2500)
                    if first:
                        _consent(pg)
                        pg.wait_for_timeout(2000)
                        first = False
                    rows = _cards(pg)
                    added = 0
                    for r in rows:
                        key = f"{r['company'].lower()}|{r['role'].lower()}"
                        if key in seen or len(r["company"]) < 2:
                            continue
                        cat = js.matches_target_role(r["role"])
                        if not cat or js.excluded_role(r["role"]):
                            continue
                        seen.add(key)
                        listings.append(js.JobListing(
                            company=r["company"], role=r["role"], job_url=r["url"],
                            location=r["location"], category=cat, source=NAME,
                            meta={"contract": "alternance"
                                  if re.search(r"alternan|apprenti", r["role"], re.I) else ""}))
                        added += 1
                    if added:
                        print(f"[indeed]   query='{query}' p{n + 1}: +{added} match(es)")
                    if not rows:
                        break
                ctx.close()
                time.sleep(_GAP)
        finally:
            browser.close()
    return listings


if __name__ == "__main__":
    print(f"browser available: {available()}")
    got = discover(max_pages=1)
    print(f"\n{len(got)} role-matching listing(s)")
    for g in got[:15]:
        print(f"   {g.role[:52]:52} | {g.company[:24]:24} | {g.location[:20]}")
