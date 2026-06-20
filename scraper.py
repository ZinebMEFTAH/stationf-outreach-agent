"""Multi-source job-board scraper / orchestrator.

Owns the browser, runs one or more job sources (Station F, Welcome to the Jungle, …),
enriches each company with a named contact (contact_finder), deduplicates against
contacts.xlsx, and inserts new rows as 'Pending'.

Source-neutral building blocks live in `jobsource.py`; each board has its own discovery
module (Station F discovery is in this file; Welcome to the Jungle is in `wttj.py`).
Add a new board by writing a module with discover()/resolve_company_site() and
registering it in SOURCES below.
"""
from __future__ import annotations

import argparse
import re
import sys
from typing import Iterable

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

import apec
import company_resolver
import contact_finder as cf
import hellowork
import jobsource as js
import tracker
import wttj

# Shared, source-neutral pieces (kept under their historical names so the rest of this
# module — and external importers/tests — keep working unchanged).
from jobsource import JobListing, KNOWN_BAD_EMAIL_DOMAINS, deduce_email  # noqa: F401

ROLE_KEYWORDS = js.ROLE_KEYWORDS
_matches_target_role = js.matches_target_role
_slugify_company = js.slugify_company
_domain_from_url = js.domain_from_url
_accept_cookies = js.accept_cookies

STATION_F_BASE = "https://jobs.stationf.co"
STATION_F_JOBS_URL = f"{STATION_F_BASE}/search"


def _extract_listings_from_page(page: Page) -> list[JobListing]:
    raw = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('a.jobs-item-link')).map(a => ({
            href: a.href || '',
            text: (a.innerText || '').trim(),
        }))
        """
    )
    out: list[JobListing] = []
    for item in raw:
        href = item.get("href") or ""
        text = item.get("text") or ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        role = lines[0]
        company = lines[1]
        category = _matches_target_role(role)
        if not category:
            continue
        slug = _extract_slug(href)
        out.append(
            JobListing(
                company=company,
                role=role,
                company_slug=slug,
                job_url=href,
                category=category,
            )
        )
    return out


def _extract_slug(job_url: str) -> str | None:
    m = re.search(r"/companies/([^/]+)/jobs/", job_url or "")
    return m.group(1) if m else None


def _get_total_pages(page: Page) -> int:
    """Compute total pages.

    The visible pagination only shows a few numbered links plus '»', so we
    prefer the 'N jobs found' counter and divide by per-page count.
    Falls back to the highest visible numbered link if the counter is missing.
    """
    try:
        info = page.evaluate(
            """
            () => {
              const body = document.body ? document.body.innerText : '';
              const m = body.match(/(\\d[\\d\\s.,]*)\\s+jobs?\\s+found/i);
              const total = m ? parseInt(m[1].replace(/[\\s.,]/g,''), 10) : null;
              const perPage = document.querySelectorAll('a.jobs-item-link').length || 20;
              const linkNums = Array.from(document.querySelectorAll('a.ais-Pagination-link'))
                .map(a => parseInt((a.innerText||'').trim(), 10))
                .filter(n => !isNaN(n));
              const maxNumbered = linkNums.length ? Math.max(...linkNums) : 1;
              return { total, perPage, maxNumbered };
            }
            """
        )
        total = info.get("total")
        per_page = max(int(info.get("perPage") or 20), 1)
        if total:
            return max((int(total) + per_page - 1) // per_page, 1)
        return max(int(info.get("maxNumbered") or 1), 1)
    except Exception:
        return 1


def _enrich_company_website(page: Page, slug: str, company_name: str = "") -> str | None:
    """Open /companies/<slug> and try to extract the *official* company website URL.

    Heuristic: among external anchors that aren't on the platform blocklist
    (social, station f, welcome-to-the-jungle, big consumer platforms), prefer
    the one whose hostname best matches the company slug or whose link text
    mentions the company name. Falls back to the first plausible external link.
    """
    if not slug:
        return None
    url = f"{STATION_F_BASE}/companies/{slug}"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(800)
    except Exception:
        return None
    try:
        anchors = page.evaluate(
            """
            () => {
              const blocked = [
                'stationf.co','linkedin.com','twitter.com','x.com','facebook.com',
                'instagram.com','welcometothejungle','welcomekit','youtube.com','youtu.be',
                'canva.com','vimeo.com','tiktok.com','medium.com','notion.so','notion.site',
                'github.com','calendly.com','goo.gl','google.com','docs.google.com',
                'apps.apple.com','play.google.com','airtable.com','typeform.com',
              ];
              const blockedTexts = [
                'back to main website','open our page on welcome to the jungle',
                'see our website','our page on',
              ];
              return Array.from(document.querySelectorAll('a[href^="http"]'))
                .map(a => ({
                  href: a.href,
                  text: ((a.innerText||'').trim()).toLowerCase(),
                  inFooter: !!a.closest('footer, .footer, .main-footer, .site-footer'),
                  cls: (a.className||'').toString().toLowerCase(),
                }))
                .filter(a => {
                  if (a.inFooter) return false;
                  if (a.cls.includes('footer')) return false;
                  if (blockedTexts.some(t => a.text.includes(t))) return false;
                  let host = '';
                  try { host = new URL(a.href).hostname.toLowerCase(); } catch(e) { return false; }
                  if (!host || host.indexOf('.') < 0) return false;
                  if (blocked.some(b => host.includes(b))) return false;
                  a._host = host;
                  return true;
                })
                .map(a => ({href: a.href, text: a.text, host: a._host}));
            }
            """
        )
    except Exception:
        return None

    if not anchors:
        return None

    slug_norm = re.sub(r"[^a-z0-9]", "", (slug or "").lower())
    name_norm = re.sub(r"[^a-z0-9]", "", (company_name or "").lower())

    def host_root(host: str) -> str:
        parts = host.split(".")
        return parts[-2] if len(parts) >= 2 else host

    def score(a: dict) -> int:
        host = a["host"]
        root = host_root(host)
        s = 0
        if name_norm and name_norm in root:
            s += 100
        if slug_norm and slug_norm in root:
            s += 80
        if name_norm and root in name_norm:
            s += 60
        if name_norm and name_norm in a["text"]:
            s += 20
        if a["text"]:
            s += 1
        return s

    best = max(anchors, key=score)
    if score(best) <= 1:
        return None
    return best["href"]


def _stationf_discover(page: Page, max_pages: int | None = None) -> list[JobListing]:
    """Station F discovery: paginate the search board and extract matching listings."""
    page.goto(STATION_F_JOBS_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    _accept_cookies(page)
    try:
        page.wait_for_load_state("networkidle", timeout=45000)
    except PWTimeout:
        pass

    total = _get_total_pages(page)
    if max_pages is not None:
        total = min(total, max_pages)
    print(f"[scraper] paginating across {total} page(s)")

    listings: list[JobListing] = []
    for n in range(1, total + 1):
        if n > 1:
            page.goto(f"{STATION_F_JOBS_URL}?page={n}", wait_until="domcontentloaded")
            page.wait_for_timeout(700)
            try:
                page.wait_for_load_state("networkidle", timeout=45000)
            except PWTimeout:
                pass
        got = _extract_listings_from_page(page)
        if got:
            print(f"[scraper]   page {n}: +{len(got)} match(es)")
        listings.extend(got)
    return listings


def _stationf_resolve_site(page: Page, listing: JobListing) -> str | None:
    return _enrich_company_website(page, listing.company_slug, company_name=listing.company)


# Registry of pluggable job sources. Add a board by writing a module (or functions)
# with discover(page, max_pages) + resolve_company_site(page, listing) and listing it here.
#   enrich=True  → resolve the company website + find a named contact inline.
#   enrich=False → discovery only; rows keep the generic fallback email and are
#                  enriched later by /find-contacts. (WTTJ hides company domains in its
#                  public API and serves SPA company pages, so inline enrichment is futile.)
SOURCES: dict[str, dict] = {
    "stationf": {"discover": _stationf_discover, "resolve": _stationf_resolve_site, "enrich": True},
    "wttj": {"discover": wttj.discover, "resolve": wttj.resolve_company_site, "enrich": False},
    "hellowork": {"discover": hellowork.discover, "resolve": hellowork.resolve_company_site, "enrich": False},
    "apec": {"discover": apec.discover, "resolve": apec.resolve_company_site, "enrich": False},
}


def _enrich_all(page: Page, listings: list[JobListing]) -> None:
    """Resolve each company's website and find a named contact. Dedups per company
    within a source; a single failure is logged, never aborts the run. Sources flagged
    enrich=False are skipped (discovery-only)."""
    cache: dict[tuple, tuple] = {}
    for job in listings:
        if not SOURCES.get(job.source, {}).get("enrich", True):
            continue
        key = (job.source, (job.company_slug or job.company or "").strip().lower())
        if key in cache:
            job.company_url, job.found_contact = cache[key]
            continue
        resolve = SOURCES[job.source]["resolve"]
        try:
            site = resolve(page, job)
        except Exception as e:  # noqa: BLE001
            print(f"[scraper]   site lookup failed for {job.company}: {type(e).__name__}")
            site = None
        job.company_url = site
        domain = _domain_from_url(site)
        print(f"[scraper] finding contact for {job.company} [{job.source}]"
              + (f" ({domain})" if domain else " (no domain — profile-only)"))
        # Only Station F slugs resolve on the Station F profile; for other boards
        # rely on the company website + job page instead.
        sf_slug = job.company_slug if job.source == "stationf" else None
        try:
            found = cf.find_contact_with_page(
                page, company_name=job.company, domain=domain,
                slug=sf_slug, job_url=job.job_url, website_url=site,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[scraper]   contact lookup failed for {job.company}: {type(e).__name__}")
            found = None
        job.found_contact = found
        cache[key] = (job.company_url, job.found_contact)


def scrape_sources(
    source_names: Iterable[str],
    headless: bool = True,
    timeout_ms: int = 45000,
    max_pages: int | None = None,
    enrich: bool = True,
) -> list[JobListing]:
    """Run the given sources in one shared browser session, then enrich + return all
    matched listings. Unknown source names are skipped with a warning."""
    listings: list[JobListing] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(user_agent=js.DEFAULT_UA, locale="en-US")
        page = ctx.new_page()
        page.set_default_timeout(timeout_ms)
        try:
            for name in source_names:
                src = SOURCES.get(name)
                if not src:
                    print(f"[scraper] unknown source '{name}' — skipping")
                    continue
                print(f"[scraper] ===== source: {name} =====")
                try:
                    got = src["discover"](page, max_pages)
                except Exception as e:  # noqa: BLE001 — one source must not sink the run
                    print(f"[scraper] source '{name}' failed: {type(e).__name__}: {e}")
                    got = []
                print(f"[scraper] {name}: {len(got)} match(es)")
                listings.extend(got)

            if enrich and listings:
                _enrich_all(page, listings)
        finally:
            ctx.close()
            browser.close()
    return listings


def scrape(
    url: str = STATION_F_JOBS_URL,
    headless: bool = True,
    timeout_ms: int = 45000,
    max_pages: int | None = None,
    enrich: bool = True,
) -> list[JobListing]:
    """Backward-compatible Station-F-only entry point (kept for existing callers)."""
    return scrape_sources(["stationf"], headless=headless, timeout_ms=timeout_ms,
                          max_pages=max_pages, enrich=enrich)


def _email_domain(email: str) -> str:
    return email.split("@", 1)[1].lower() if "@" in (email or "") else ""


def _email_for(job: JobListing) -> tuple[str, bool]:
    """The address a listing should carry, and whether it's a *real* (non-guessed) one.

    Preference: a named contact found during enrichment → the real company domain (from a
    discovered website, else resolved from the company name) → a slug-based guess. Used by
    both persist() and the CLI preview so they never disagree.
    """
    if job.found_contact and job.found_contact.tracker_format:
        return job.found_contact.tracker_format, True
    # Resolve a real domain so discovery-only rows (WTTJ / HelloWork) carry a CORRECT
    # generic address (contact@trustpair.fr) instead of a wrong slug guess.
    domain = _domain_from_url(job.company_url) or company_resolver.resolve_domain(job.company)
    if domain:
        return f"contact@{domain}", True
    return deduce_email(job.company, job.company_url, job.company_slug), False


def _is_named_contact(email: str) -> bool:
    """True if email already has a display name (RFC 5322 with angle brackets)."""
    return "<" in (email or "") and ">" in (email or "")


def persist(listings: Iterable[JobListing], update_existing_emails: bool = True) -> tuple[int, int, int]:
    """Insert new (company, role) rows; optionally upgrade or repair emails.

    Returns (added, skipped, updated). A row's email is updated when:
      - update_existing_emails is True, AND
      - the newly-deduced email differs from the stored email, AND
      - either the new email comes from a real website (preferred), OR
        the stored email is on a known-bad domain (revert to slug fallback).
    """
    added = 0
    skipped = 0
    updated = 0

    df = tracker.load()
    df_dirty = False

    for job in listings:
        new_email, new_email_is_real = _email_for(job)

        if update_existing_emails and not df.empty:
            companies = df["Company"].fillna("").astype(str).str.strip().str.lower()
            roles = df["Role"].fillna("").astype(str).str.strip().str.lower()
            mask = (companies == job.company.strip().lower()) & (roles == job.role.strip().lower())
            if mask.any():
                idxs = df.index[mask].tolist()
                for idx in idxs:
                    current = str(df.at[idx, "Contact Email"] or "").strip()
                    current_domain = _email_domain(current.lower())
                    is_bad = current_domain in KNOWN_BAD_EMAIL_DOMAINS
                    if current.lower() == new_email.lower():
                        continue
                    # Don't overwrite an already-named contact with a new one
                    # (manual enrichment or a previous find-contacts pass may be higher quality)
                    if _is_named_contact(current) and not is_bad:
                        continue
                    if new_email_is_real or is_bad:
                        df.at[idx, "Contact Email"] = new_email
                        updated += 1
                        df_dirty = True
                skipped += 1
                continue

        ok = tracker.add_contact(
            company=job.company,
            role=job.role,
            contact_email=new_email,
            status="Pending",
        )
        if ok:
            added += 1
            df = tracker.load()
        else:
            skipped += 1

    if df_dirty:
        tracker.save(df)

    return added, skipped, updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Multi-source job scraper (Station F, WTTJ, …)")
    parser.add_argument("--source", default="all",
                        help="comma-separated sources to run, or 'all' "
                             f"(available: {', '.join(SOURCES)})")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="cap pages per source (Station F: total; WTTJ: per query)")
    parser.add_argument("--no-enrich", action="store_true", help="Skip per-company website lookup")
    parser.add_argument("--no-update-existing", action="store_true",
                        help="Do not patch emails on existing rows when a real domain is discovered")
    args = parser.parse_args(argv)

    if args.source.strip().lower() == "all":
        sources = list(SOURCES)
    else:
        sources = [s.strip().lower() for s in args.source.split(",") if s.strip()]

    print(f"[scraper] sources: {', '.join(sources)}")
    listings = scrape_sources(
        sources,
        headless=not args.headed,
        max_pages=args.max_pages,
        enrich=not args.no_enrich,
    )
    print(f"[scraper] matched (AI/Backend/Data): {len(listings)}")

    for j in listings[:25]:
        email_display, _ = _email_for(j)
        print(f"  - [{j.source}/{j.category}] {j.role} @ {j.company} :: {email_display}")

    if args.dry_run:
        print("[scraper] dry-run: nothing written")
        return 0

    added, skipped, updated = persist(listings, update_existing_emails=not args.no_update_existing)
    print(f"[scraper] inserted={added} skipped_duplicates={skipped} email_updates={updated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
