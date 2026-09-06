"""Shared config: env loading, paths, footer string, document resolution."""
from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
DOCUMENTS_DIR = ROOT / "documents"
ABOUT_ME_PATH = ROOT / "about_me.txt"
INSTRUCTIONS_PATH = ROOT / "instructions.txt"

load_dotenv(ROOT / ".env")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Optional: Hunter.io key enables real email verification that works even when
# outbound port 25 is blocked (e.g. on the VM). Free tier is ~100 verifications/month.
HUNTER_API_KEY = os.environ.get("HUNTER_API_KEY", "")

# ── Quota self-throttle (see usage_budget.py) ────────────────────────────────
# Keep the agent inside every external rate limit so it degrades gracefully instead of
# hitting a hard 429 / usage-limit. All overridable via .env.
#
# Hunter (fail-CLOSED — a graceful MX/generic fallback exists when we stop verifying):
#   The authoritative guard reads Hunter's REAL remaining balance (the free /v2/account
#   endpoint), so we never exceed 100/month even if spend happened elsewhere. Stop when the
#   real remaining balance drops to the safety margin; the monthly figure is a local fallback
#   used only if the account check is unreachable.
HUNTER_SAFETY_MARGIN = int(os.environ.get("HUNTER_SAFETY_MARGIN", "8"))    # keep this many in reserve
HUNTER_MONTHLY_BUDGET = int(os.environ.get("HUNTER_MONTHLY_BUDGET", "90"))  # local fallback cap
#
# Claude subscription token (fail-OPEN — a budget-check bug must NEVER halt outreach; only a
# clear runaway is skipped). Caps are RUN counts (a proxy for token spend); the 5h-spaced cron
# already keeps normal ops well under them — these only catch manual-run bunching / retry loops.
# Set a cap to 0 to disable that window's check.
CLAUDE_MAX_RUNS_5H = int(os.environ.get("CLAUDE_MAX_RUNS_5H", "2"))   # 1 scheduled + 1 manual overlap
CLAUDE_MAX_RUNS_7D = int(os.environ.get("CLAUDE_MAX_RUNS_7D", "40"))  # ~25 scheduled/wk + headroom
# Optional: France Travail (ex-Pôle emploi) Offres d'emploi API. Register a free app at
# https://francetravail.io to get these; the francetravail job source stays inert until set.
FRANCE_TRAVAIL_ID = os.environ.get("FRANCE_TRAVAIL_ID", "")
FRANCE_TRAVAIL_SECRET = os.environ.get("FRANCE_TRAVAIL_SECRET", "")
# Optional: La Bonne Alternance (api.apprentissage.beta.gouv.fr) — the state-run "hidden
# market" API. Register a free account to get an API key; the labonnealternance job source
# stays inert until this is set.
LBA_API_KEY = os.environ.get("LBA_API_KEY", "")
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS", "")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD", "")
FROM_NAME = os.environ.get("FROM_NAME", "Zineb Meftah")
INTERNAL_ALERT_EMAIL = os.environ.get("INTERNAL_ALERT_EMAIL", "you@example.com")
IMAP_SERVER = os.environ.get("IMAP_SERVER", "imap.gmail.com")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))

# Addresses an `--kind alert` may be sent to. An alert is an INTERNAL notification and skips
# every safety gate the outreach path has — verification, the bounce blocklist, the daily cap,
# the duplicate guard, tracker logging, and the AI-disclosure footer. That is correct for a
# message to Zineb's own inbox and completely wrong for anyone else: mislabelling a company
# address as `alert` would send it unverified, uncounted and unlogged. So the recipient is
# restricted to this set. Extra addresses can be added via INTERNAL_EXTRA_EMAILS (comma-sep).
INTERNAL_RECIPIENTS = {
    a.strip().lower()
    for a in [EMAIL_ADDRESS, INTERNAL_ALERT_EMAIL, "you@example.com"]
       + os.environ.get("INTERNAL_EXTRA_EMAILS", "").split(",")
    if a and a.strip()
}

COLD_CAP = 7        # max new cold emails per calendar day (the ceiling; see warm-up ramp below)
WARM_CAP = 3        # max follow-ups per calendar day (human replies are notify-only — the agent never auto-answers them)
DAILY_CAP = COLD_CAP + WARM_CAP   # total outbound cap (10)

# How many generic contact@ leads /find-contacts enriches into named decision-makers per run.
# Enrichment is upstream of sending (each named contact = a better opener + a LinkedIn double-tap),
# so this can run well above the send cap to grow the named-contact pool and clear the backlog.
# It's safe to raise: /find-contacts runs in its own fresh 5h Claude-usage window, and the anti-bounce
# gate + graceful MX/generic fallback mean more volume never means more bounces (only stronger
# addresses get sent to a personal mailbox). The VM cron reads this value. Raise if the backlog is
# large and the usage window / Hunter quota allow; lower it if the 19:00 run gets tight.
ENRICH_CAP = 15

# ── Deliverability warm-up ramp ──────────────────────────────────────────────
# Sending 7 cold/day from a fresh mailbox trips spam filters. Ramp the cold cap up
# gradually so the sender reputation climbs naturally: week 1 → 3/day, week 2 → 5/day,
# week 3+ → COLD_CAP. To restart the ramp (new mailbox / domain), set this to that day.
from datetime import date as _date
WARMUP_START_DATE = _date(2026, 7, 4)

# ── International / remote targeting ─────────────────────────────────────────
# Zineb targets international companies too. She stays in France (no relocation), so a FOREIGN
# company is only viable as a REMOTE role — the `remotive` source tags those roles with this
# marker. A tagged lead is handled differently by /daily-agent: English email, and the ask is an
# internship / CDI / full-time role — NEVER "alternance" (that needs a French employer + school).
# Global companies WITH a French office still come through the French sources and keep alternance.
# INTL_RANK_BOOST tilts the priority queue toward international leads (raise for more tilt, 0 = off).
REMOTE_INTL_TAG = "[Remote/International]"
INTL_RANK_BOOST = int(os.environ.get("INTL_RANK_BOOST", "15"))

# Reachable-international lever (global_brands.py): recognizable employers with a real France office
# that hire juniors/alternants. A "cold"-channel brand (Paris-HQ scale-up) is genuinely emailable, so
# it earns a solid boost; a "portal"-channel giant (Google/Datadog…) is surfaced but cold email won't
# land there, so a smaller boost lets the big-corp down-rank steer it to the application path.
GLOBAL_BRAND_BOOST_COLD = int(os.environ.get("GLOBAL_BRAND_BOOST_COLD", "15"))
GLOBAL_BRAND_BOOST_PORTAL = int(os.environ.get("GLOBAL_BRAND_BOOST_PORTAL", "8"))


def is_remote_international(role: str) -> bool:
    """True if a lead's role is a foreign, remote-only role (tagged by the remotive source)."""
    return "remote/international" in (role or "").lower()


# ── Location mode — first-class across the system (Zineb pursues BOTH remote and in-person) ──
# Every lead / offer is classified remote | hybrid | onsite | "" (unknown). Ranking stays neutral
# (no bias toward either mode); /daily-agent uses it only to frame the email (remote-friendly vs
# in-person availability), and the opportunity scout groups her digest by it.
_LOC_HYBRID_RE = re.compile(
    r"\b(hybri\w+|remote[- ]?friendly|t[ée]l[ée]travail\s*partiel|partial\s*remote|"
    r"[123]\s*(?:j(?:ours?)?|days?)\s*(?:/|par|a|à|on[- ]?site|au bureau))\b", re.I)
_LOC_REMOTE_RE = re.compile(
    r"\b(fully?\s*remote|full[- ]?remote|100\s*%?\s*remote|remote|t[ée]l[ée]travail|"
    r"distanciel|work\s*from\s*home|wfh|anywhere|remote/international)\b", re.I)
_LOC_ONSITE_RE = re.compile(
    r"\b(on[- ]?site|on[- ]?premises?|sur\s*site|pr[ée]sentiel|in[- ]?office|in[- ]?person|au\s*bureau)\b",
    re.I)

LOCATION_MODES = ("remote", "hybrid", "onsite")


def classify_location(text: str) -> str:
    """Best-effort location mode from role title / description text.

    Returns 'remote' | 'hybrid' | 'onsite' | '' (unknown). Hybrid is checked first because it is the
    most specific signal ("télétravail partiel", "3 jours/semaine", "remote-friendly" are hybrid, not
    fully remote). Purely lexical + never raises — a best-effort tag, not a guarantee.
    """
    t = text or ""
    if _LOC_HYBRID_RE.search(t):
        return "hybrid"
    if _LOC_REMOTE_RE.search(t):
        return "remote"
    if _LOC_ONSITE_RE.search(t):
        return "onsite"
    return ""


# ── Alternance timing (seasonal urgency) ─────────────────────────────────────
# Zineb's alternance/Master starts in Sept 2026 and French alternance seats fill across the summer,
# so proximity to the start is a genuine lever — a calm "je finalise mes choix pour septembre" reads
# as in-demand, not desperate. This exposes how close we are so /daily-agent can calibrate the cue.
ALTERNANCE_START_DATE = _date(2026, 9, 1)


def weeks_until_alternance(today: "_date | None" = None) -> int:
    """Whole weeks from today to the alternance start (0 if past)."""
    d = today or _date.today()
    return max(0, (ALTERNANCE_START_DATE - d).days // 7)


def _weekdays_left_in_month(today: "_date | None" = None) -> int:
    """Working days remaining this month, today included. The agent only runs on weekdays."""
    import calendar
    d = today or _date.today()
    last = calendar.monthrange(d.year, d.month)[1]
    return max(1, sum(1 for day in range(d.day, last + 1)
                      if _date(d.year, d.month, day).weekday() < 5))


def verification_paced_cap(today: "_date | None" = None) -> int | None:
    """Cold sends per day that the REMAINING verification balance can sustain to month end.

    Verification, not COLD_CAP, is the real ceiling on cold outreach: every cold send spends one
    Hunter check, the free tier gives 100 a month, and the gate that protects it is a CLIFF —
    `remaining > HUNTER_SAFETY_MARGIN`, then nothing. So the month front-loads and then goes dark.
    August 2026 is exactly that shape: 108 cold sends against a 100 budget, at 6-8/day until the
    quota ran out. The bounce rate tracked it precisely — 0.9% in July with quota, 7.2% in August
    past it. The blanket mx_only exemption that let those bounces through is gone, so today the
    same month would simply STOP sending instead, which is safer and just as wasteful.

    Spreading the balance over the working days left turns "7/day for two weeks then nothing" into
    a steady rate that lasts the month — better for deliverability reputation too, since a mailbox
    that sends in bursts and then goes silent looks less like a person than one with a rhythm.

    Returns None when the balance is unknown, so the caller keeps the warm-up ramp: a bookkeeping
    gap must never be able to stop outreach on its own.
    """
    try:
        import email_verify
        rec = email_verify._hunter_acct_cached()
    except Exception:  # noqa: BLE001
        return None
    if not rec:
        return None
    remaining, ts = rec
    # The cache is read at ANY age by design, which is right for "how much is left" but wrong for
    # pacing: a record written before this month's quota reset would report last month's exhausted
    # balance and pin the cap at zero, stopping cold outreach for a month that actually has a full
    # 100 available. A reading from before the 1st tells us nothing about today.
    import datetime as _dt
    d = today or _date.today()
    month_start = _dt.datetime(d.year, d.month, 1, tzinfo=_dt.timezone.utc).timestamp()
    if ts < month_start:
        return None
    spendable = remaining - HUNTER_SAFETY_MARGIN
    if spendable <= 0:
        return 0
    return max(1, spendable // _weekdays_left_in_month(today))


def effective_cold_cap(today: "_date | None" = None) -> int:
    """Today's actual cold cap: the warm-up ramp, paced by the verification budget."""
    d = ((today or _date.today()) - WARMUP_START_DATE).days
    if d < 7:
        cap = min(3, COLD_CAP)
    elif d < 14:
        cap = min(5, COLD_CAP)
    else:
        cap = COLD_CAP
    paced = verification_paced_cap(today)
    return cap if paced is None else min(cap, paced)


# ── Follow-up sequence (multi-touch) ─────────────────────────────────────────
FOLLOWUP_DAYS = 4   # business days before the FIRST follow-up
MAX_FOLLOWUPS = 3   # total follow-ups per lead across the whole sequence
FOLLOWUP_GAP = 2    # extra business days added per subsequent touch → 4, 6, 8 biz days

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

# ── Footer rotation (deliverability) ─────────────────────────────────────────
# The P.S. is the AI-agent disclosure / differentiator on cold emails. Sending the BYTE-IDENTICAL
# block on every message is a templated-content spam signal, so we rotate among a few variants that
# all say the same thing (some tighter than the canonical above — shorter footers also read better).
# FOOTER_FR / FOOTER_EN stay as the canonical variant #1 (referenced elsewhere / by the linter).
FOOTER_FR_VARIANTS = [
    FOOTER_FR,
    ("P.S. Cet email a été rédigé et envoyé sans intervention humaine par un agent IA que j'ai "
     "conçu et mis en production (scraping du board Station F, qualification LLM des offres, "
     "personnalisation par entreprise, envoi et relances automatiques, le tout en skills Claude "
     "Code). C'est ce type d'IA bout-en-bout que je veux construire chez vous."),
    ("P.S. Ce message vous a été envoyé par un agent IA autonome que j'ai développé et déployé "
     "moi-même — il cible, personnalise et relance en production. C'est exactement l'ingénierie "
     "IA de bout en bout que je cherche à approfondir avec vous."),
]
FOOTER_EN_VARIANTS = [
    FOOTER_EN,
    ("P.S. This email was written and sent with no human in the loop by an AI agent I built and "
     "run in production (Station F scraping, LLM opportunity qualification, per-company "
     "personalization, automated sending and follow-ups, all as Claude Code skills). This "
     "end-to-end AI is exactly what I want to build with you."),
    ("P.S. An autonomous AI agent I designed and deployed myself sent you this — it targets, "
     "personalizes and follows up in production. That end-to-end AI engineering is exactly what "
     "I want to go deeper on with you."),
]


def pick_footer(lang: str, seed: "int | str | None" = None) -> str:
    """Return one footer variant. Rotates to avoid a byte-identical block on every cold email
    (a templated-content spam signal). `seed` (e.g. the recipient address) makes it deterministic
    per recipient when supplied; otherwise a variant is chosen at random."""
    import random
    variants = FOOTER_FR_VARIANTS if str(lang).lower().startswith("fr") else FOOTER_EN_VARIANTS
    if seed is not None:
        return variants[hash(str(seed)) % len(variants)]
    return random.choice(variants)


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
