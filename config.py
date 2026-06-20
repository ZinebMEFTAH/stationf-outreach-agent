"""Shared config: env loading, paths, footer string, document resolution."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
DOCUMENTS_DIR = ROOT / "documents"
ABOUT_ME_PATH = ROOT / "about_me.txt"
INSTRUCTIONS_PATH = ROOT / "instructions.txt"

load_dotenv(ROOT / ".env")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Optional: Hunter.io key enables real email verification that works even when
# outbound port 25 is blocked (e.g. on the VM). Free tier (~50/mo) covers 2/day.
HUNTER_API_KEY = os.environ.get("HUNTER_API_KEY", "")
# Optional: France Travail (ex-Pôle emploi) Offres d'emploi API. Register a free app at
# https://francetravail.io to get these; the francetravail job source stays inert until set.
FRANCE_TRAVAIL_ID = os.environ.get("FRANCE_TRAVAIL_ID", "")
FRANCE_TRAVAIL_SECRET = os.environ.get("FRANCE_TRAVAIL_SECRET", "")
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS", "")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD", "")
FROM_NAME = os.environ.get("FROM_NAME", "Zineb Meftah")
INTERNAL_ALERT_EMAIL = os.environ.get("INTERNAL_ALERT_EMAIL", "you@example.com")
IMAP_SERVER = os.environ.get("IMAP_SERVER", "imap.gmail.com")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))

COLD_CAP = 2        # max new cold emails per calendar day
WARM_CAP = 3        # max follow-ups + replies per calendar day
DAILY_CAP = COLD_CAP + WARM_CAP   # total outbound cap (5)
FOLLOWUP_DAYS = 4

FOOTER_FR = (
    "P.S. Ce message a été entièrement rédigé et envoyé de façon autonome par un agent IA "
    "que j'ai conçu et déployé en production : scraping Playwright du board Station F, "
    "qualification des opportunités par LLM, personnalisation du message selon le profil "
    "de chaque entreprise, envoi SMTP et relances automatiques — le tout orchestré avec "
    "des skills Claude Code. C'est précisément ce type de pipeline IA bout-en-bout que je "
    "veux contribuer à construire avec vous."
)

FOOTER_EN = (
    "P.S. This message was entirely written and sent autonomously by an AI agent I designed "
    "and deployed in production: Playwright scraping of the Station F job board, LLM-based "
    "opportunity qualification, per-company message personalization, SMTP delivery and "
    "automatic follow-ups — all orchestrated as Claude Code skills. This is exactly the "
    "kind of end-to-end AI pipeline I want to help build with you."
)


def about_me_text() -> str:
    return ABOUT_ME_PATH.read_text(encoding="utf-8")


def resolve_cv(lang: str) -> Path | None:
    """Return the path to the most appropriate CV. `lang` is 'fr' or 'en'.

    Tries canonical names first, falls back to glob (handles ' copy' suffixes).
    """
    lang = (lang or "en").lower()
    if lang.startswith("fr"):
        candidates = [
            DOCUMENTS_DIR / "CV_Zineb_Meftah_FR.pdf",
            *DOCUMENTS_DIR.glob("CV_Zineb_Meftah_FR*.pdf"),
            *DOCUMENTS_DIR.glob("CV*FR*.pdf"),
        ]
    else:
        candidates = [
            DOCUMENTS_DIR / "CV_Zineb_Meftah_EN.pdf",
            *DOCUMENTS_DIR.glob("CV_Zineb_Meftah_EN*.pdf"),
            *DOCUMENTS_DIR.glob("CV*EN*.pdf"),
        ]
    for c in candidates:
        if c.exists():
            return c
    return None


FRENCH_HINTS = [
    "h/f", "f/h", "f/m", "m/f", "stage", "alternance", "alternant",
    "césure", "cesure", "ingénieur", "ingenieur", "développeur", "developpeur",
    "données", "donnees", "apprentissage", "cdi", "cdd",
]


def guess_language(role: str, company: str = "") -> str:
    text = f"{role} {company}".lower()
    return "fr" if any(h in text for h in FRENCH_HINTS) else "en"


# Zineb's contract priority (most-to-least preferred): CDI > CDD > Alternance.
# She is genuinely open to all three. The agent leads with whatever fits the
# posting and signals openness to the others — never lists all three as a menu.
CONTRACT_PRIORITY = ["cdi", "cdd", "alternance"]


def guess_contract_type(role: str) -> str:
    """Infer the contract type implied by a scraped role title.

    Returns one of:
      'alternance'  — work-study / apprenticeship posting (direct match)
      'stage'       — internship posting (Zineb is past this; upsell to alternance)
      'cdd'         — fixed-term contract
      'cdi'         — permanent / full-time posting
      'speculative' — a [Suggested] row added by /speculative (no real posting)
      'unspecified' — bare role title with no contract marker (treat like CDI/full-time)
    """
    r = (role or "").lower().strip()
    if r.startswith("[suggested]"):
        return "speculative"
    if any(k in r for k in ("alternance", "alternant", "apprentissage", "apprenti",
                            "work-study", "work study")):
        return "alternance"
    if any(k in r for k in ("stagiaire", "internship", "stage", " intern", "(intern")):
        return "stage"
    if "cdd" in r or "fixed-term" in r or "fixed term" in r:
        return "cdd"
    if any(k in r for k in ("cdi", "permanent", "full-time", "full time", "temps plein")):
        return "cdi"
    # On Station F, a bare title like "Senior AI Engineer" is a full-time/permanent role.
    return "unspecified"
