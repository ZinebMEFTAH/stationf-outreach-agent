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
from functools import lru_cache
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
        # The BARE noun, which was absent — so "Développeur Python", the commonest French spelling
        # of her target role, matched NOTHING, in every scraper and the whole outreach pipeline.
        # Only compounds were listed ("développeur backend", "ingénieur data") and a French
        # posting rarely writes one. Measured over 377 raw France Travail / La Bonne Alternance
        # titles: matches went 123 -> 207 (+68%), with no losses. _flatten collapses "/", "·" and
        # "(" to spaces, so this single entry also recovers the inclusive-writing spellings
        # ("développeur/se", "développeur·se", "développeur(se)"), which were invisible too.
        "développeur", "developpeur", "développeuse", "developpeuse",
        "ingénieur informatique", "ingenieur informatique",
        "ingénieur développement", "ingenieur developpement", "python",
    ],
    "data": [
        "data engineer", "data analyst", "data scientist", "analytics engineer",
        "data platform", "data ops", "dataops", "ingénieur data", "ingenieur data",
        "data architect", "data science",
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


@lru_cache(maxsize=512)
def _keyword_rx(keyword: str) -> "re.Pattern":
    """A keyword as a WORD-bounded pattern over flattened text.

    ROLE_KEYWORDS used to anchor short keywords with hand-placed spaces — "ia ", " ml ", " nlp",
    "sre " — and substring-matched them. That silently classified any title containing "media" as
    an AI role, because "media " ends with "ia ". Fifteen rows in contacts.xlsx are Social Media
    Manager and Retail Media postings scraped as AI leads that way, several of them emailed: the
    agent pitched an AI-engineering profile against a social-media job. Word boundaries make the
    intent explicit instead of leaving it to whitespace that the caller may or may not have.
    """
    # (?:e|s|es)? after EVERY word tolerates French inflection. Every keyword in ROLE_KEYWORDS is
    # written masculine-singular; job titles are not, and the inflection lands wherever the noun
    # is — "data analyste" at the end, "ingénieure data" in the middle, "data analysts" in English.
    # Plain \b lost "data analyste F/H", a real target role in the spelling APEC actually uses.
    words = _flatten(keyword).strip().split()
    # "ing" is here for the English gerund, which is just as common in titles as the French
    # inflection: "DATA ENGINEERING", "SOFTWARE ENGINEERING INTERN" and "PLATFORM ENGINEERING"
    # are all the same roles as their -eer spellings, and word boundaries alone dropped them.
    core = r"(?:e|s|es|ing)?\s+".join(re.escape(w) for w in words)
    return re.compile(rf"\b{core}(?:e|s|es|ing)?\b")



# A title can carry a target keyword and still not be the job. This list exists because of one
# specific consequence: the bare noun "développeur" added above is what finally matches
# "Développeur Python" — and it equally matches "Développeur Commercial", "BUSINESS DEVELOPPEUR
# BtoB" and "DEVELOPPEUR FONCIER" (a land developer). It is the French mirror of "Business
# Developer", which passes any filter containing the word "developer".
#
# This is the SHARED gate: every scraper and the whole outreach pipeline runs on it, and until now
# it was a pure include-list with NO exclusions, while the digest maintained a rich one. A match
# here is what puts a row in contacts.xlsx and eventually spends a cold send plus a Hunter
# verification — the scarcest resource in the system at ~3/day.
#
# Deliberately NARROW: only what the new keywords newly admit. Two groups —
#   • commercial / real-estate compounds of "développeur", which are sales jobs;
#   • stacks she does not work in, which the digest already refuses (php / .net / c# / wordpress /
#     cobol / embedded). Those never reached outreach before, because no bare "développeur"
#     existed to let them in. Admitting them now would be a regression, not a gain.
_ROLE_EXCLUSIONS = re.compile(
    r"d[ée]veloppeur(?:s|se|euse)?\s+(?:commercial|foncier|immobilier|de\s+marque|"
    r"d[\s']affaires|btob|b2b|rh)"
    r"|business\s+d[ée]veloppeur"
    r"|\b(?:wordpress|webmaster|joomla|drupal|cobol|as[\s/]?400|mainframe|sage|talend|abap|sap|"
    r"siebel|peoplesoft|delmia|msbi)\b"
    r"|(?:\.net|c#|c\+\+|\bphp\b)"
    r"|\bembarqu[ée]e?s?\b"
    # LEVEL. A BTS/DUT/Bac+2 alternance is not a posting she can take: she holds a Licence and
    # enters an M1. Filtered on the DIPLOMA, not on the school's name — on 2026-09-18 a single
    # search returned SEVEN "Alternance - Développeur web junior - BTS S.I.O" rows from one BTS
    # school, and blocklisting that school would have fixed one school rather than the class.
    # `BUT` is deliberately absent: it is also the ordinary French word "but", and these patterns
    # run on short titles where a false positive costs a real lead.
    r"|\bBTS\b|\bDUT\b|bac\s*\+\s*2\b|licence\s+pro", re.I)


def excluded_role(title: str) -> bool:
    """True when a title carries a target keyword but is not a job she wants. See _ROLE_EXCLUSIONS."""
    return bool(_ROLE_EXCLUSIONS.search(_flatten(title)))


def matches_target_role(title: str) -> str | None:
    """Return the category (ai/backend/data) a title matches, or None."""
    t = f" {_flatten(title).strip()} "
    if excluded_role(title):
        return None
    for category, kws in ROLE_KEYWORDS.items():
        for kw in kws:
            if _keyword_rx(kw).search(t):
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


# ─────────────────────────────────────────────────────────────────────────────
# QUAND L'ANNONCE A-T-ELLE ÉTÉ PUBLIÉE — un seul analyseur, partagé.
#
# POURQUOI ICI. HelloWork savait lire « il y a 5 jours », Indeed ne savait rien lire, et le
# lecteur d'annonces (descriptions.py) avait encore besoin de la même chose. Trois copies d'une
# règle de date finissent par divergera — et cette règle-là décide d'un score : la fraîcheur
# vaut +18 à trois jours et l'âge −14 au-delà de 90. Une date absente, elle, est NEUTRE, donc
# une offre de quatre mois non datée passe devant une offre de trois jours.
#
# ⚠ TOUT CE QUI EST GROSSIER EST ASSUMÉ COMME TEL. « il y a 2 mois » devient J-60 : le score
#   distingue une annonce fraîche d'une annonce qui traîne, il ne date pas un contrat. Mais
#   « il y a plus de 30 jours » (le « 30+ days ago » d'Indeed et de Workday) ne doit PAS devenir
#   J-30 : c'est une borne inférieure, pas une date, et la rendre exacte ferait passer une
#   annonce de six mois pour une annonce d'un mois. On rend J-45, du côté prudent.
_REL_FR = re.compile(
    r"\bil y a\s+(?:plus de\s+)?(\d+)\s*(heures?|jours?|semaines?|mois|ans?)", re.I)
_REL_EN = re.compile(r"\b(\d+)\+?\s*(hour|day|week|month|year)s?\s+ago", re.I)
_REL_PLUS = re.compile(r"\b(?:il y a\s+plus de|\+\s*de)\s*(\d+)\s*(jours?|days?)|(\d+)\+\s*(?:jours?|days?)",
                       re.I)
_JOUR_FR = re.compile(r"\b(?:publi[ée]e?\s*(?:le)?|mise?\s+en\s+ligne\s*(?:le)?|"
                      r"d[ée]pos[ée]e?\s*(?:le)?)\s*:?\s*(\d{1,2})[/\s.-](\d{1,2})[/\s.-](\d{4})", re.I)
_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
# ⚠ « MOIS » SE TERMINE DÉJÀ PAR UN S. Dépluraliser par rstrip("s") en fait « moi », inconnu,
#   donc replié sur 1 jour : « il y a 2 mois » rendait J-2 au lieu de J-60, soit une annonce de
#   deux mois créditée +18 comme une annonce de l'avant-veille. Les deux formes sont donc
#   écrites, et le repli est 0 — refuser plutôt que d'inventer une unité.
_UNITES = {"heure": 0, "heures": 0, "jour": 1, "jours": 1, "semaine": 7, "semaines": 7,
           "mois": 30, "an": 365, "ans": 365,
           "hour": 0, "hours": 0, "day": 1, "days": 1, "week": 7, "weeks": 7,
           "month": 30, "months": 30, "year": 365, "years": 365}


def _unite(mot: str) -> int:
    return _UNITES.get((mot or "").lower(), 0)


def relative_date(texte: str, today=None) -> str:
    """Une formulation de date trouvée dans du texte -> AAAA-MM-JJ. "" si rien d'exploitable.

    Reconnaît, dans cet ordre de fiabilité : une date explicite (« Publié le 12/09/2026 »),
    « aujourd'hui »/« hier », puis une ancienneté relative en français ou en anglais.

    ⚠ RIEN N'EST DEVINÉ : sans formulation reconnue on rend "", et l'appelant garde l'absence.
      Une date fausse coûte plus cher qu'une date manquante — elle fait pénaliser une annonce
      fraîche ou créditer une annonce morte, en silence et avec l'air d'être renseignée.
    """
    from datetime import date, timedelta
    auj = today or date.today()
    t = texte or ""
    if not t:
        return ""

    m = _JOUR_FR.search(t)
    if m:
        j, mo, a = (int(x) for x in m.groups())
        try:
            return date(a, mo, j).isoformat()
        except ValueError:
            return ""                      # 31/02 : une date impossible n'est pas une date
    if re.search(r"\baujourd.hui\b|\b[àa] l.instant\b|\bjust now\b|\btoday\b", t, re.I):
        return auj.isoformat()
    if re.search(r"\bhier\b|\byesterday\b", t, re.I):
        return (auj - timedelta(days=1)).isoformat()

    # « plus de 30 jours » / « 30+ days ago » : une BORNE, pas une date. Workday écrit
    # « 30+ Days Ago » et CLAUDE.md note déjà que ce nombre ne doit pas devenir une date —
    # le score pénalise l'ancienneté sur ce chiffre même, où une approximation trop favorable
    # est pire que l'inconnu. On rend J-45, donc du côté « vieille », jamais « fraîche ».
    mp = _REL_PLUS.search(t)
    if mp:
        n = int(next(g for g in mp.groups() if g))
        return (auj - timedelta(days=max(n, 30) + 15)).isoformat()

    m = _REL_FR.search(t) or _REL_EN.search(t)
    if m:
        n, unite = int(m.group(1)), m.group(2)
        return (auj - timedelta(days=n * _unite(unite))).isoformat()
    return ""


def plausible_date(iso: str, today=None, max_age_days: int = 1095) -> str:
    """Garde une date seulement si elle peut être celle d'une annonce vivante, sinon "".

    DEUX REFUS, chacun payé ailleurs dans ce dépôt. Une date FUTURE n'est pas une publication
    (c'est souvent une date de début de contrat ou une expiration lue par erreur — le même
    piège que `_start` dans descriptions.py, où « Créée en janvier 2015 » devenait la date de
    début). Et une date de plus de trois ans vient d'un pied de page, d'un copyright ou d'un
    identifiant, pas de l'annonce.
    """
    from datetime import date
    auj = today or date.today()
    s = (iso or "")[:10]
    if not _ISO.fullmatch(s or "x"):
        return ""
    try:
        d = date.fromisoformat(s)
    except ValueError:
        return ""
    if d > auj:
        return ""
    if (auj - d).days > max_age_days:
        return ""
    return s
