"""Station F job board scraper.

Filters listings for AI / Backend / Data roles, deduces a contact email per
company, deduplicates against contacts.xlsx, and inserts new rows as 'Pending'.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

import contact_finder as cf
import tracker

STATION_F_BASE = "https://jobs.stationf.co"
STATION_F_JOBS_URL = f"{STATION_F_BASE}/search"

ROLE_KEYWORDS = {
    "ai": [
        "ai engineer", "a.i.", "artificial intelligence", "machine learning", " ml ",
        "mlops", "ml ops", "deep learning", " nlp", "llm", "genai", "gen ai",
        "computer vision", "research scientist", "ia ", "intelligence artificielle",
        "ingénieur ia", "ingenieur ia",
    ],
    "backend": [
        "backend", "back-end", "back end", "software engineer", "software developer",
        "platform engineer", "devops", "site reliability", "sre ", "api engineer",
        "développeur backend", "developpeur backend", "ingénieur logiciel",
        "fullstack", "full-stack", "full stack",
    ],
    "data": [
        "data engineer", "data analyst", "data scientist", "analytics engineer",
        "data platform", "data ops", "dataops", "ingénieur data", "ingenieur data",
        "data architect",
    ],
}


@dataclass
class JobListing:
    company: str
    role: str
    company_slug: str | None = None
    company_url: str | None = None
    job_url: str | None = None
    category: str | None = None
    found_contact: "cf.FoundContact | None" = None


def _matches_target_role(title: str) -> str | None:
    t = f" {(title or '').lower()} "
    for category, kws in ROLE_KEYWORDS.items():
        for kw in kws:
            if kw in t:
                return category
    return None


def _slugify_company(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return None
    host = host.lower().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return None
    if any(b in host for b in ("stationf.co", "linkedin.com", "welcometothejungle", "welcomekit.")):
        return None
    return host


def deduce_email(company: str, company_url: str | None, company_slug: str | None = None) -> str:
    domain = _domain_from_url(company_url)
    if domain:
        return f"contact@{domain}"
    # Prefer the human-readable company name for the fallback slug; URL slugs
    # can be legacy aliases (e.g. Ekie -> avostart-1) that don't match reality.
    slug = _slugify_company(company) or (company_slug or "unknown").replace("_", "").replace("-", "")
    return f"contact@{slug}.com"


def _accept_cookies(page: Page) -> None:
    candidates = [
        "button:has-text('Accept all')",
        "button:has-text('Accept')",
        "button:has-text('Accepter')",
        "button:has-text('Tout accepter')",
        "#axeptio_btn_acceptAll",
        "[data-testid='cookie-accept']",
    ]
    for sel in candidates:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=400):
                btn.click(timeout=1000)
                page.wait_for_timeout(250)
                return
        except Exception:
            continue


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


def scrape(
    url: str = STATION_F_JOBS_URL,
    headless: bool = True,
    timeout_ms: int = 45000,
    max_pages: int | None = None,
    enrich: bool = True,
) -> list[JobListing]:
    listings: list[JobListing] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = ctx.new_page()
        page.set_default_timeout(timeout_ms)
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            _accept_cookies(page)
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except PWTimeout:
                pass

            total = _get_total_pages(page)
            if max_pages is not None:
                total = min(total, max_pages)
            print(f"[scraper] paginating across {total} page(s)")

            for n in range(1, total + 1):
                if n > 1:
                    page.goto(f"{url}?page={n}", wait_until="domcontentloaded")
                    page.wait_for_timeout(700)
                    try:
                        page.wait_for_load_state("networkidle", timeout=timeout_ms)
                    except PWTimeout:
                        pass
                got = _extract_listings_from_page(page)
                if got:
                    print(f"[scraper]   page {n}: +{len(got)} match(es)")
                listings.extend(got)

            if enrich and listings:
                slugs_done: dict[str, str | None] = {}
                contacts_done: dict[str, "cf.FoundContact | None"] = {}
                for job in listings:
                    if not job.company_slug:
                        continue
                    if job.company_slug in slugs_done:
                        job.company_url = slugs_done[job.company_slug]
                        job.found_contact = contacts_done.get(job.company_slug)
                        continue
                    site = _enrich_company_website(page, job.company_slug, company_name=job.company)
                    slugs_done[job.company_slug] = site
                    job.company_url = site

                    domain = _domain_from_url(site)
                    # Try contact discovery even when no website was found —
                    # the Station F profile or job page may still yield a name.
                    print(f"[scraper] finding contact for {job.company}"
                          + (f" ({domain})" if domain else " (no domain — profile-only)"))
                    found = cf.find_contact_with_page(
                        page,
                        company_name=job.company,
                        domain=domain,  # may be None — contact_finder handles it
                        slug=job.company_slug,
                        job_url=job.job_url,
                        website_url=site,
                    )
                    contacts_done[job.company_slug] = found
                    job.found_contact = found
        finally:
            ctx.close()
            browser.close()
    return listings


KNOWN_BAD_EMAIL_DOMAINS = {
    "canva.com", "youtu.be", "youtube.com", "voodoo.io", "welcomekit.co",
    "welcometothejungle.com", "businessdigital.fr",
}


def _email_domain(email: str) -> str:
    return email.split("@", 1)[1].lower() if "@" in (email or "") else ""


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
        # Prefer a named contact found during enrichment over a generic contact@ fallback
        if job.found_contact and job.found_contact.tracker_format:
            new_email = job.found_contact.tracker_format
            new_email_is_real = True
        else:
            new_email = deduce_email(job.company, job.company_url, job.company_slug)
            new_email_is_real = _domain_from_url(job.company_url) is not None

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
    parser = argparse.ArgumentParser(description="Station F scraper")
    parser.add_argument("--url", default=STATION_F_JOBS_URL)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--no-enrich", action="store_true", help="Skip per-company website lookup")
    parser.add_argument("--no-update-existing", action="store_true",
                        help="Do not patch emails on existing rows when a real domain is discovered")
    args = parser.parse_args(argv)

    print(f"[scraper] target: {args.url}")
    listings = scrape(
        args.url,
        headless=not args.headed,
        max_pages=args.max_pages,
        enrich=not args.no_enrich,
    )
    print(f"[scraper] matched (AI/Backend/Data): {len(listings)}")

    for j in listings[:25]:
        if j.found_contact and j.found_contact.tracker_format:
            email_display = j.found_contact.tracker_format
        else:
            email_display = deduce_email(j.company, j.company_url, j.company_slug)
        print(f"  - [{j.category}] {j.role} @ {j.company} :: {email_display}")

    if args.dry_run:
        print("[scraper] dry-run: nothing written")
        return 0

    added, skipped, updated = persist(listings, update_existing_emails=not args.no_update_existing)
    print(f"[scraper] inserted={added} skipped_duplicates={skipped} email_updates={updated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
