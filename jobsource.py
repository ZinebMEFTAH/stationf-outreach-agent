"""Shared, source-neutral building blocks for every job-board scraper.

The agent scrapes more than one site (Station F, Welcome to the Jungle, …). The
*discovery* part is specific to each board, but everything around it — the role
filter, the listing record, email/domain/slug helpers, the cookie banner — is the
same everywhere. It lives here so each source module stays small and consistent.

A "source" module (e.g. `wttj.py`) exposes:
    NAME: str
    JOBS_URL: str
    discover(page, max_pages=None) -> list[JobListing]
    resolve_company_site(page, listing) -> str | None   # official website, if findable

`scraper.py` is the orchestrator: it owns the browser, runs the enabled sources,
enriches each company with a named contact (contact_finder), and persists to
contacts.xlsx.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

# A realistic desktop UA — shared so every source looks the same to a board.
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

ROLE_KEYWORDS: dict[str, list[str]] = {
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

# Domains that look like company sites but never are — used to reject bad email guesses.
KNOWN_BAD_EMAIL_DOMAINS = {
    "canva.com", "youtu.be", "youtube.com", "voodoo.io", "welcomekit.co",
    "welcometothejungle.com", "businessdigital.fr",
}

# Hosts that are platforms/socials, never a company's own website.
PLATFORM_HOST_BLOCKLIST = (
    "stationf.co", "linkedin.com", "twitter.com", "x.com", "facebook.com",
    "instagram.com", "welcometothejungle", "welcomekit", "youtube.com", "youtu.be",
    "canva.com", "vimeo.com", "tiktok.com", "medium.com", "notion.so", "notion.site",
    "github.com", "calendly.com", "goo.gl", "google.com", "docs.google.com",
    "apps.apple.com", "play.google.com", "airtable.com", "typeform.com",
)


@dataclass
class JobListing:
    company: str
    role: str
    company_slug: str | None = None
    company_url: str | None = None       # the board's company page (or official site)
    job_url: str | None = None
    category: str | None = None
    source: str = "stationf"
    location: str | None = None          # human-readable place ("Lyon - 69", "75 - PARIS"), if the board gives one
    found_contact: "object | None" = None   # cf.FoundContact, set during enrichment
    # Structured extras a particular board happens to publish — contract type, posting date,
    # "débutant accepté", "peu de candidatures". Free-form because every board names things
    # differently and only some publish anything at all; consumers read what they recognise and
    # ignore the rest. It exists because these fields were being FETCHED and thrown away: France
    # Travail returns an `alternance` boolean and an experience requirement on every offer, and
    # the digest was inferring both from the job title.
    meta: dict = field(default_factory=dict)


# Separators to flatten before matching a title against ROLE_KEYWORDS. Job titles are written by
# hundreds of different employers and the punctuation between two words is arbitrary: "Machine
# Learning Engineer", "Machine-Learning Engineer" and "Data  Engineer" are the same job, and the
# last two matched NOTHING — invisible to the digest, the scrapers and the outreach pipeline alike,
# for a hyphen. French titles make it worse with gender markers: "Développeur(se)", "Ingénieur·e",
# "Apprenti/e". Both sides of the comparison are flattened the same way, so every keyword that
# matched before still matches: "back-end" becomes "back end", which the title has become too.
_TITLE_SEPARATORS = re.compile(r"[\s\-_/.·,()\[\]|]+")


def _flatten(text: str) -> str:
    """Lowercase, with every run of separators collapsed to ONE space.

    Leading and trailing spaces survive on purpose: several keywords (" ml ", " nlp", "ia ") rely
    on them to avoid firing inside a longer word, and stripping them would break that.
    """
    return _TITLE_SEPARATORS.sub(" ", (text or "").lower())


def matches_target_role(title: str) -> str | None:
    """Return the category (ai/backend/data) a title matches, or None."""
    t = f" {_flatten(title).strip()} "
    for category, kws in ROLE_KEYWORDS.items():
        for kw in kws:
            if _flatten(kw) in t:
                return category
    return None


def slugify_company(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def domain_from_url(url: str | None) -> str | None:
    """Registrable-ish domain of a company website, or None for platform/social URLs."""
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
    """Best-effort generic fallback address when no named contact was found."""
    domain = domain_from_url(company_url)
    if domain:
        return f"contact@{domain}"
    slug = slugify_company(company) or (company_slug or "unknown").replace("_", "").replace("-", "")
    return f"contact@{slug}.com"


def accept_cookies(page) -> None:
    """Dismiss the most common cookie banners (best-effort, never raises)."""
    candidates = [
        "button:has-text('Accept all')",
        "button:has-text('Accept')",
        "button:has-text('Accepter')",
        "button:has-text('Tout accepter')",
        "#axeptio_btn_acceptAll",
        "[data-testid='cookie-accept']",
        "#onetrust-accept-btn-handler",
        "button:has-text('Tout refuser')",
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
