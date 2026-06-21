"""
Find a named decision-maker for a Station F company.

Provides functions that accept an active Playwright Page object (for use
within the scraper's existing browser session) as well as a standalone CLI.

Strategy cascade (each step only runs if the previous found nothing useful):
  1. Station F company profile  — JSON-LD, LinkedIn links, DOM card patterns
  2. Company website team pages — /team, /about, /equipe, /about-us, etc.
  3. Job detail page            — poster / contact name in job text

CLI:
  python contact_finder.py \\
      --company "Craft AI" --domain "craft.ai" \\
      [--slug "craft-ai"] [--job-url "https://..."] [--website "https://craft.ai"]
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright

import company_resolver
from email_verify import build_patterns, verify

STATION_F_BASE = "https://jobs.stationf.co"

# ---------------------------------------------------------------------------
# Title scoring — higher = more relevant for an AI/Backend/Data alternance
# ---------------------------------------------------------------------------

_TITLE_SCORES: list[tuple[int, list[str]]] = [
    (100, ["chief technology officer", "cto", "chief technical officer", "directeur technique", "directeur technologique"]),
    (95, ["head of ai", "vp ai", "director of ai", "head of machine learning", "lead ai", "head ml",
          "responsable ia", "head of data", "vp data", "director of data", "lead data"]),
    (90, ["vp engineering", "head of engineering", "vp of engineering", "director of engineering",
          "head of tech", "vp tech", "chief engineer", "vp product & tech", "head of product & tech"]),
    (85, ["chief product officer", "cpo"]),
    (75, ["engineering manager", "lead engineer", "principal engineer", "staff engineer",
          "lead developer", "tech lead", "lead technique"]),
    (65, ["co-founder", "cofounder", "co-fondateur", "cofondateur", "cofondatrice", "co-fondatrice"]),
    (55, ["chief executive officer", "ceo", "founder", "fondateur", "fondatrice",
          "président", "president", "directeur général", "dg "]),
    (30, ["talent acquisition", "head of people", "head of talent", "recruiting manager",
          "talent manager", "campus manager", "talent lead", "talent partner"]),
    (20, ["recruiter", "recruteur", "recrutement", " rh ", " hr ", "human resources", "ressources humaines"]),
]


def score_title(title: str) -> int:
    t = (title or "").lower()
    for pts, keywords in _TITLE_SCORES:
        if any(kw in t for kw in keywords):
            return pts
    return 0


# ---------------------------------------------------------------------------
# JavaScript — extract people from the current page
# ---------------------------------------------------------------------------

_EXTRACT_PEOPLE_JS = r"""
() => {
    const people = [];
    const seen = new Set();

    function norm(s) { return (s || '').toLowerCase().replace(/\s+/g, ' ').trim(); }

    function addPerson(name, title, li_url, source) {
        name = (name || '').trim();
        const key = norm(name);
        if (!key || key.length < 4 || key.length > 55 || seen.has(key)) return;
        seen.add(key);
        people.push({ name, title: norm(title || ''), linkedin_url: li_url || null, source });
    }

    // ---- 1. JSON-LD schema.org Person / employee / member ------------------
    document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
        try {
            const raw = JSON.parse(s.textContent);
            const items = Array.isArray(raw) ? raw.flat(4) : [raw];
            function checkItem(e) {
                if (!e || !e.name) return;
                const types = [].concat(e['@type'] || []);
                const isPerson = types.some(t => ['Person','Employee','OrganizationMember'].includes(t));
                if (!isPerson) return;
                const sameAs = [].concat(e.sameAs || []);
                const li = sameAs.find(u => String(u).includes('linkedin')) || null;
                addPerson(e.name, e.jobTitle || '', li, 'schema_org');
            }
            items.forEach(item => {
                if (!item) return;
                checkItem(item);
                [].concat(item['@graph'] || [], item.employee || [], item.member || []).forEach(checkItem);
            });
        } catch(e) {}
    });

    // ---- 2. LinkedIn anchor text with a person name -------------------------
    document.querySelectorAll('a[href*="linkedin.com/in/"]').forEach(a => {
        const text = (a.innerText || a.title || a.getAttribute('aria-label') || '').trim();
        const words = text.split(/\s+/);
        if (!text || text.length < 5 || text.length > 55 || words.length < 2 || words.length > 5) return;
        let title = '';
        const card = a.closest('[class*="team"],[class*="member"],[class*="person"],[class*="staff"],[class*="bio"],[class*="people"],article,li');
        if (card) {
            const titleEl = Array.from(card.querySelectorAll('p,span,[class*="title"],[class*="role"],[class*="position"],[class*="job"],[class*="poste"]'))
                .find(el => (el.innerText || '').trim() !== text && (el.innerText || '').trim().length > 1);
            if (titleEl) title = titleEl.innerText.trim().split('\n')[0];
        }
        addPerson(text, title, a.href, 'linkedin_link');
    });

    // ---- 3. DOM card patterns (team-member, member-card, bio, …) -----------
    const cardCSS = [
        '[class*="team-member"]','[class*="team_member"]','[class*="member-card"]',
        '[class*="memberCard"]','[class*="person-card"]','[class*="personCard"]',
        '[class*="staff-member"]','[class*="team-item"]','[class*="bio-card"]',
        '[class*="team-bio"]','[class*="leadership-item"]','[class*="management-item"]',
        '[class*="equipe-membre"]','[class*="membre-equipe"]','[class*="co-founder"]',
    ].join(',');
    document.querySelectorAll(cardCSS).forEach(card => {
        const hEls = Array.from(card.querySelectorAll('h1,h2,h3,h4,h5,strong,b'))
            .map(el => (el.innerText || '').trim().split('\n')[0].trim())
            .filter(t => t.length >= 4 && t.length < 60);
        const pEls = Array.from(card.querySelectorAll('p,span,[class*="title"],[class*="role"],[class*="position"],[class*="poste"]'))
            .map(el => (el.innerText || '').trim().split('\n')[0].trim())
            .filter(t => t.length >= 2 && t.length < 80);
        if (hEls.length > 0) {
            const li = (card.querySelector('a[href*="linkedin.com"]') || {}).href || null;
            addPerson(hEls[0], pEls[0] || '', li, 'card_pattern');
        }
    });

    // ---- 4. Heading immediately followed by a short title line -------------
    document.querySelectorAll('h2,h3,h4').forEach(h => {
        const text = (h.innerText || '').trim().split('\n')[0];
        const words = text.split(/\s+/);
        if (words.length < 2 || words.length > 5 || text.length < 5 || text.length > 55) return;
        // Require at least 2 words starting with a capital or accented capital
        const capRe = /^[A-ZÀÂÄÉÈÊËÎÏÔÙÛÜ]/;
        if (words.filter(w => capRe.test(w)).length < 2) return;
        const next = h.nextElementSibling;
        const title = next ? (next.innerText || '').trim().split('\n')[0] : '';
        // Skip if the next element is too long — it's a company description, not a job title
        if (title.length > 80) return;
        const section = h.closest('section,article,div,li') || document;
        const li = (section.querySelector('a[href*="linkedin.com"]') || {}).href || null;
        addPerson(text, title, li, 'heading_pattern');
    });

    return people;
}
"""


def extract_people_from_page(page: Page) -> list[dict]:
    try:
        return page.evaluate(_EXTRACT_PEOPLE_JS) or []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Page navigation helpers
# ---------------------------------------------------------------------------

def _goto_safe(page: Page, url: str, timeout: int = 10000) -> bool:
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(500)
        return bool(resp and resp.status < 400)
    except Exception:
        return False


def find_people_on_stationf_profile(page: Page, slug: str) -> list[dict]:
    """Visit /companies/{slug} and extract team members."""
    if not _goto_safe(page, f"{STATION_F_BASE}/companies/{slug}", timeout=15000):
        return []
    return extract_people_from_page(page)


def find_people_on_team_pages(page: Page, website_url: str) -> list[dict]:
    """Try common team/about URL paths on the company website."""
    parsed = urlparse(website_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    paths = [
        "/team", "/about", "/equipe", "/about-us", "/notre-equipe",
        "/company", "/who-we-are", "/leadership", "/management",
        "/fr/equipe", "/en/team", "/a-propos",
    ]
    for path in paths:
        if _goto_safe(page, base + path):
            people = extract_people_from_page(page)
            if people:
                return people
    return []


def find_people_on_job_page(page: Page, job_url: str) -> list[dict]:
    """Visit the job detail page looking for a poster/contact."""
    if not _goto_safe(page, job_url):
        return []
    people = extract_people_from_page(page)
    # Also scan body text for "Contact: Firstname Lastname" patterns
    try:
        extra = page.evaluate(r"""
        () => {
            const txt = document.body ? document.body.innerText : '';
            const re = /(?:contact|recruiter|apply to|posted by|responsable)[:\s]+([A-ZÀÂÄÉÈÊËÎÏÔÙÛÜ][a-zàâäéèêëîïôùûü]+(?:\s[A-ZÀÂÄÉÈÊËÎÏÔÙÛÜ][a-zàâäéèêëîïôùûü]+)+)/g;
            const found = [];
            let m;
            while ((m = re.exec(txt)) !== null) {
                found.push({ name: m[1], title: '', linkedin_url: null, source: 'job_page_text' });
            }
            return found;
        }
        """) or []
        people.extend(extra)
    except Exception:
        pass
    return people


# ---------------------------------------------------------------------------
# Contact model + email derivation
# ---------------------------------------------------------------------------

@dataclass
class FoundContact:
    first_name: str
    last_name: str
    title: str
    domain: str
    email: Optional[str] = None
    linkedin_url: Optional[str] = None
    source: str = "unknown"
    email_confidence: str = "guessed"

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def display(self) -> str:
        title_part = f" ({self.title})" if self.title else ""
        return f"{self.full_name}{title_part}"

    @property
    def tracker_format(self) -> str:
        """RFC 5322 format for contacts.xlsx Contact Email column."""
        if not self.email:
            return ""
        title_part = f" ({self.title})" if self.title else ""
        return f'"{self.full_name}{title_part}" <{self.email}>'


_JOB_TITLE_WORDS = {
    "engineer", "ingénieur", "ingenieur", "developer", "développeur", "developpeur",
    "analyst", "analyste", "scientist", "stage", "intern", "internship", "alternance",
    "full-time", "part-time", "freelance", "cdi", "cdd", "senior", "junior", "lead",
    "manager", "director", "business", "sales", "sdr", "bdr", "account", "marketing",
    "designer", "product", "growth", "ops", "operations", "finance", "legal", "hr",
    "recruiter", "talent", "support", "customer", "success",
}

# Articles, pronouns, and company/greeting words that can't be part of a person name
_NOT_A_NAME_WORDS = {
    # French
    "nous", "notre", "votre", "leur", "leurs", "il", "elle", "ils", "elles",
    "ce", "cette", "ces", "le", "la", "les", "un", "une", "des", "du", "de",
    "en", "et", "ou", "avec", "pour", "sur", "par", "dans", "qui", "que",
    "bonjour", "bienvenue", "découvrez", "rejoignez", "contactez",
    # English
    "the", "our", "your", "their", "we", "they", "he", "she", "it",
    "a", "an", "in", "at", "on", "by", "for", "with", "and", "or",
    "hello", "welcome", "discover", "join", "contact",
}


def _parse_name(text: str) -> tuple[str, str] | None:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text or len(text) < 4 or len(text) > 60:
        return None
    # Reject non-ASCII characters that would break SMTP (emoji, etc.)
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        # Strip non-ASCII and retry
        text = text.encode("ascii", errors="ignore").decode("ascii").strip()
        if len(text) < 4:
            return None
    if any(c in text for c in "@#$%&*()[]{}|<>/\\~`^0123456789"):
        return None
    parts = text.split()
    if len(parts) < 2 or len(parts) > 5:
        return None
    # Reject all-caps names — those are job titles/section headers, not people
    if all(w.isupper() for w in parts if len(w) > 2):
        return None
    # Reject if any word is a known job-title keyword (case-insensitive)
    if any(w.lower() in _JOB_TITLE_WORDS for w in parts):
        return None
    # Reject if any word is an article, pronoun, or non-name word
    if any(w.lower() in _NOT_A_NAME_WORDS for w in parts):
        return None
    return parts[0], " ".join(parts[1:])


def pick_best_person(people: list[dict]) -> dict | None:
    if not people:
        return None
    return max(people, key=lambda p: score_title(p.get("title", "")))


def derive_email(person: dict, domain: str) -> tuple[str | None, str]:
    """Try email patterns for the person at domain. Returns (email, confidence)."""
    parsed = _parse_name(person.get("name", ""))
    if not parsed:
        return None, "unverifiable"
    first, last_parts = parsed
    last = last_parts.split()[-1]  # use only the final surname
    for email in build_patterns(first, last, domain):
        ok, conf, reason = verify(email)
        if ok:
            return email, conf
        if "no MX" in reason:
            break
    return None, "unverifiable"


def build_found_contact(person: dict, domain: str) -> FoundContact | None:
    parsed = _parse_name(person.get("name", ""))
    if not parsed:
        return None
    first, last_parts = parsed
    last = last_parts.split()[-1]
    email, confidence = derive_email(person, domain)
    return FoundContact(
        first_name=first,
        last_name=last,
        title=person.get("title", ""),
        domain=domain,
        email=email,
        linkedin_url=person.get("linkedin_url"),
        source=person.get("source", "unknown"),
        email_confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

def find_contact_with_page(
    page: Page,
    company_name: str,
    domain: str | None = None,
    slug: str | None = None,
    job_url: str | None = None,
    website_url: str | None = None,
    verbose: bool = True,
) -> FoundContact | None:
    """
    Find the best named contact using an already-open Playwright page.
    The caller owns the browser/context lifecycle.

    `domain` may be None when no company website was found — the function
    will still attempt discovery via Station F profile and job page.
    """
    # No domain? Resolve the company's real one (name → domain) so we can crawl its team
    # pages AND derive a verified email. This is what makes the discovery-only sources
    # (Welcome to the Jungle, HelloWork) enrichable — their rows arrive without a domain.
    effective_domain = domain
    if not effective_domain:
        resolved = company_resolver.resolve_domain(company_name)
        if resolved:
            effective_domain = resolved
            if not website_url:
                website_url = f"https://{resolved}"
            if verbose:
                print(f"    [contact_finder] resolved domain: {resolved}")

    all_people: list[dict] = []

    if slug:
        ppl = find_people_on_stationf_profile(page, slug)
        if ppl and verbose:
            print(f"    [contact_finder] station_f profile: {len(ppl)} candidate(s)")
        all_people.extend(ppl)

    if job_url:
        ppl = find_people_on_job_page(page, job_url)
        if ppl and verbose:
            print(f"    [contact_finder] job page: {len(ppl)} candidate(s)")
        all_people.extend(ppl)

    # Crawl the company's own team/about pages unless we already have a *parseable*
    # person. (A SPA job page can return junk "candidates" that aren't real names —
    # those must not suppress the team-page crawl on the real resolved domain.)
    if website_url and not any(_parse_name(p.get("name", "")) for p in all_people):
        ppl = find_people_on_team_pages(page, website_url)
        if ppl and verbose:
            print(f"    [contact_finder] team/about pages: {len(ppl)} candidate(s)")
        all_people.extend(ppl)

    person = pick_best_person(all_people)
    if not person:
        return None

    # Still no domain (resolution failed and none passed in)? Fall back to a name-only
    # contact if we at least have a LinkedIn URL, else give up.
    if not effective_domain and person.get("linkedin_url"):
        # Can't derive email without domain — return contact with name/title only
        parsed = _parse_name(person.get("name", ""))
        if parsed:
            contact = FoundContact(
                first_name=parsed[0],
                last_name=parsed[1].split()[-1],
                title=person.get("title", ""),
                domain="",
                email=None,
                linkedin_url=person.get("linkedin_url"),
                source=person.get("source", "unknown"),
                email_confidence="no_domain",
            )
            if verbose:
                print(f"    [contact_finder] → {contact.display} [no domain — name only]")
            return contact
        return None

    if not effective_domain:
        return None

    contact = build_found_contact(person, effective_domain)
    if contact and verbose:
        print(f"    [contact_finder] → {contact.display} [{contact.email_confidence}]")
    return contact


def find_contact(
    company_name: str,
    domain: str | None = None,
    slug: str | None = None,
    job_url: str | None = None,
    website_url: str | None = None,
    headless: bool = True,
) -> FoundContact | None:
    """Standalone entry point — manages its own browser session. `domain` may be omitted;
    it is then resolved from the company name (company_resolver)."""
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
        try:
            return find_contact_with_page(page, company_name, domain, slug, job_url, website_url)
        finally:
            ctx.close()
            browser.close()


def contact_from_name(
    company_name: str,
    person_name: str,
    title: str = "",
    domain: str | None = None,
) -> FoundContact | None:
    """Turn a person's NAME (found by the /find-contacts skill via web search) into a
    verified email. No browser, no crawl: resolve the company domain if not given, then
    derive + SMTP/API-verify the email pattern. This is the raw-I/O half of Pass 2 — the
    skill finds the right decision-maker, this proves the address."""
    parsed = _parse_name(person_name)
    if not parsed:
        return None
    first, last_parts = parsed
    last = last_parts.split()[-1]
    eff_domain = domain or company_resolver.resolve_domain(company_name)
    if not eff_domain:
        return FoundContact(first_name=first, last_name=last, title=title, domain="",
                            email=None, source="web_search", email_confidence="no_domain")
    email, confidence = derive_email({"name": person_name}, eff_domain)
    return FoundContact(first_name=first, last_name=last, title=title, domain=eff_domain,
                        email=email, source="web_search", email_confidence=confidence)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Find a named contact for a company")
    parser.add_argument("--company", required=True, help="Company display name")
    parser.add_argument("--domain", help="Company email domain (e.g. craft.ai). "
                                         "If omitted, resolved from the company name.")
    parser.add_argument("--person", help="A decision-maker's full name (from web search). "
                                         "Skips crawling — just derives + verifies their email.")
    parser.add_argument("--title", default="", help="The person's title (with --person)")
    parser.add_argument("--slug", help="Station F company slug (e.g. craft-ai)")
    parser.add_argument("--job-url", help="Job detail page URL")
    parser.add_argument("--website", help="Company website URL")
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode")
    args = parser.parse_args(argv)

    if args.person:
        print(f"[contact_finder] verifying {args.person} @ {args.company} "
              f"({args.domain or 'domain to be resolved'})")
        contact = contact_from_name(args.company, args.person, args.title, args.domain)
    else:
        print(f"[contact_finder] searching for contact at {args.company} "
              f"({args.domain or 'domain to be resolved'})")
        contact = find_contact(
            company_name=args.company,
            domain=args.domain,
            slug=args.slug,
            job_url=args.job_url,
            website_url=args.website,
            headless=not args.headed,
        )

    if contact and contact.email:
        print(f"✅  {contact.display}")
        print(f"    email:      {contact.email}")
        print(f"    confidence: {contact.email_confidence}")
        print(f"    source:     {contact.source}")
        print(f"    linkedin:   {contact.linkedin_url or 'n/a'}")
        print(f"    tracker:    {contact.tracker_format}")
        return 0
    elif contact:
        print(f"⚠️  found {contact.display} but no verifiable email [{contact.email_confidence}]")
        return 1
    else:
        print("❌  no named contact found")
        return 1


if __name__ == "__main__":
    sys.exit(main())
