"""Excel tracking module for contacts.xlsx.

Strictly enforces the 6-column schema defined in instructions.txt:
Company | Role | Contact Email | Conversation Log | Last Interaction Date | Status
"""
from __future__ import annotations

import re
import shutil
from datetime import date, datetime
from email.utils import formataddr, parseaddr
from pathlib import Path
from typing import Iterable

import pandas as pd

EXCEL_PATH = Path(__file__).parent / "contacts.xlsx"
BACKUPS_DIR = Path(__file__).parent / "backups"
BACKUP_RETENTION = 30

COLUMNS: list[str] = [
    "Company",
    "Role",
    "Contact Email",
    "Conversation Log",
    "Last Interaction Date",
    "Status",
]

VALID_STATUSES = {
    "Pending",
    "Emailed",
    "Replied",
    "Followed Up",
    "Rejected",
    "Interview Scheduled",
}


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame({col: pd.Series(dtype="object") for col in COLUMNS})


def load() -> pd.DataFrame:
    """Load contacts.xlsx, creating it with the canonical schema if missing."""
    if not EXCEL_PATH.exists():
        df = _empty_df()
        save(df)
        return df

    df = pd.read_excel(EXCEL_PATH, engine="openpyxl", dtype=object)

    for col in COLUMNS:
        if col not in df.columns:
            df[col] = pd.Series(dtype="object")
    df = df[COLUMNS]
    return df


def backup() -> Path | None:
    """Snapshot contacts.xlsx into backups/ with a timestamp.

    Keeps the most recent BACKUP_RETENTION files; older ones are pruned.
    Returns the backup path, or None if no source file exists.
    """
    if not EXCEL_PATH.exists():
        return None
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = BACKUPS_DIR / f"contacts_{stamp}.xlsx"
    shutil.copy2(EXCEL_PATH, dest)

    backups = sorted(BACKUPS_DIR.glob("contacts_*.xlsx"))
    for old in backups[:-BACKUP_RETENTION]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


def save(df: pd.DataFrame) -> None:
    """Persist a dataframe to contacts.xlsx with the canonical column order."""
    out = df.reindex(columns=COLUMNS)
    out.to_excel(EXCEL_PATH, index=False, engine="openpyxl")


def _norm_email(email: str | None) -> str:
    """Extract the bare addr (lowercased) from any address form.

    Accepts plain 'foo@bar.com' or RFC 5322 '"Foo Bar" <foo@bar.com>'.
    """
    if not email:
        return ""
    _, addr = parseaddr(str(email))
    return (addr or str(email)).strip().lower()


def _display_email(email: str | None) -> str:
    """Preserve the full display form (name + addr) for storage."""
    return (email or "").strip()


def extract_name(email: str | None) -> str:
    """Return the display-name portion of an RFC 5322 address, or empty string."""
    if not email:
        return ""
    name, _ = parseaddr(str(email))
    return (name or "").strip()


def format_address(name: str | None, addr: str) -> str:
    """Build an RFC 5322 'Name <addr>' string. Returns just addr if name is empty."""
    return formataddr(((name or "").strip(), addr.strip()))


def row_exists(df: pd.DataFrame, company: str, contact_email: str, role: str | None = None) -> bool:
    """Return True if a row for the same (Company, Role) — or same email when
    no role is supplied — already exists. Multiple roles per company are allowed."""
    if df.empty:
        return False

    company_norm = (company or "").strip().lower()
    role_norm = (role or "").strip().lower()
    email_norm = _norm_email(contact_email)

    companies = df["Company"].fillna("").astype(str).str.strip().str.lower()
    roles = df["Role"].fillna("").astype(str).str.strip().str.lower()
    emails = df["Contact Email"].fillna("").astype(str).map(_norm_email)

    if company_norm and role_norm:
        return bool(((companies == company_norm) & (roles == role_norm)).any())
    if email_norm:
        return bool((emails == email_norm).any())
    return False


# Non-company names that job-board scrapers occasionally mis-extract into the Company field
# (the board's own name, or a generic French placeholder). A lead named like this has no real
# domain/decision-maker, so it can never be reached — refuse it at add time and skip it in ranking.
_JUNK_COMPANIES = {
    "hellowork", "welcome to the jungle", "wttj", "apec", "france travail", "pôle emploi",
    "pole emploi", "free-work", "free work", "la bonne alternance", "linkedin", "indeed",
    "collectivite", "collectivité", "collectivité territoriale", "entreprise", "société",
    "societe", "confidentiel", "confidentielle", "anonyme", "n/a", "na", "none", "-", "",
}


def is_junk_company(name: str) -> bool:
    """True if a Company value is a scraper artefact / generic placeholder, not a real employer."""
    return (name or "").strip().lower() in _JUNK_COMPANIES


# Training providers and job boards that post job ads without being the employer. A CFA or a
# private école posts "Alternance Développeur IA" to recruit STUDENTS into its own programme —
# the ad is bait, the "employer" is a course, and an alternance ask sent there is answered with a
# tuition quote. They arrived at the very top of the priority queue (ISCOD, KAISCHOOL, NEXA
# Digital School, ECOLE 18.06 ALSACE, jobs_that_makesense all sat in the first fifteen), where
# each one costs a daily cold slot and a Hunter verification.
#
# DOWN-RANKED, not dropped: telling a school apart from an edtech EMPLOYER by name alone is not
# reliable (OpenClassrooms hires engineers), so a false positive here must cost a rank position,
# never a lead. The pattern is deliberately narrow for the same reason — "Institut Pasteur" and a
# university lab are real employers, so neither `institut` nor `universit*` is matched.
_TRAINING_BODIES = {
    "iscod", "kaischool", "nexa digital school", "isefac", "mbway", "esupcom", "ifocop",
    "ipac bachelor factory", "jobs that makesense", "jobs_that_makesense", "jobsthatmakesense",
}
_TRAINING_RX = re.compile(
    r"\b(cfa|centre de formation|organisme de formation|[ée]cole|business school|"
    r"digital school|bachelor factory|job ?board)\b", re.I)


def is_training_body(name: str) -> bool:
    """True if a Company is a school / CFA / job board — it posts ads but does not employ."""
    n = (name or "").strip().lower()
    return n in _TRAINING_BODIES or bool(_TRAINING_RX.search(n))


def add_contact(
    company: str,
    role: str,
    contact_email: str,
    status: str = "Pending",
    conversation_log: str = "",
    last_interaction_date: str | date | None = None,
) -> bool:
    """Append a new row if it does not already exist. Returns True if added."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of {VALID_STATUSES}")
    if is_junk_company(company):
        return False  # scraper artefact (board name / generic placeholder) — never a reachable lead

    df = load()
    if row_exists(df, company, contact_email, role):
        return False

    new_row = {
        "Company": (company or "").strip(),
        "Role": (role or "").strip(),
        "Contact Email": _display_email(contact_email),
        "Conversation Log": conversation_log or "",
        "Last Interaction Date": _format_date(last_interaction_date),
        "Status": status,
    }
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    save(df)
    # Date the lead in the sidecar (the 6-column schema has nowhere to put it). Best-effort:
    # a bookkeeping failure must never lose the row that was just saved.
    try:
        import lead_age
        lead_age.record(company, role, last_interaction_date or None)
    except Exception:
        pass
    return True


def _format_date(value: str | date | datetime | None) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _find_row_index(
    df: pd.DataFrame,
    contact_email: str,
    company: str | None = None,
    role: str | None = None,
) -> int | None:
    """Find a row. If company+role are supplied, match (Company, Role, Email);
    otherwise fall back to email-only (returns first match)."""
    email_norm = _norm_email(contact_email)
    emails = df["Contact Email"].fillna("").astype(str).map(_norm_email)

    if company and role:
        companies = df["Company"].fillna("").astype(str).str.strip().str.lower()
        roles = df["Role"].fillna("").astype(str).str.strip().str.lower()
        base = (companies == company.strip().lower()) & (roles == role.strip().lower())
        # Exact match first: same company, same role, same mailbox.
        if email_norm:
            matches = df.index[base & (emails == email_norm)].tolist()
            if matches:
                return matches[0]
        # Then the SAME LEAD reached at a DIFFERENT address. This is the case a company
        # redirecting us creates ("adressez votre candidature à recruitment@…"), which is the
        # most valuable reply the pipeline gets — and ANDing the email in meant the follow-up
        # to that new inbox matched nothing, so the send succeeded and vanished from the
        # tracker. (Company, Role) is unique by construction, so this cannot pick a wrong row.
        matches = df.index[base].tolist()
        if matches:
            return matches[0]

    if not email_norm:
        return None
    matches = df.index[emails == email_norm].tolist()
    return matches[0] if matches else None


def append_interaction(
    contact_email: str,
    direction: str,
    message: str,
    status: str | None = None,
    when: str | date | datetime | None = None,
    company: str | None = None,
    role: str | None = None,
    strategy: str | None = None,
) -> bool:
    """Safely append a single interaction line to the Conversation Log.

    direction must be "Agent" or "Contact". The line is formatted as
        "[YYYY-MM-DD] Agent: <msg>" and joined to existing content with " \\n ".
    When company+role are supplied the matching (Company, Role) row is updated
    (multiple roles can share an email); otherwise the first email match wins.
    Updates Last Interaction Date and optionally Status. Returns True on success.

    ``strategy`` (a single letter in ALL_STRATEGIES) tags an Agent entry as
        "[YYYY-MM-DD] Agent (Strategy:X): <msg>"
    — the exact format strategy_stats()/the bandit parse. This is how the outreach
    remembers which approach it tried on each lead; recording it here (not by hand)
    guarantees the memory has no gaps. Ignored for Contact entries / unknown letters.
    """
    if direction not in {"Agent", "Contact"}:
        raise ValueError("direction must be 'Agent' or 'Contact'")
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'")

    df = load()
    idx = _find_row_index(df, contact_email, company=company, role=role)
    if idx is None:
        return False

    label = direction
    if direction == "Agent" and strategy:
        s = str(strategy).strip().upper()
        if len(s) == 1 and s in ALL_STRATEGIES:
            label = f"Agent (Strategy:{s})"

    when_str = _format_date(when or date.today())
    summary = (message or "").strip().replace("\n", " ").replace("\r", " ")
    entry = f"[{when_str}] {label}: {summary}"

    current = df.at[idx, "Conversation Log"]
    existing = "" if pd.isna(current) else str(current).strip()
    df.at[idx, "Conversation Log"] = f"{existing} \n {entry}" if existing else entry
    df.at[idx, "Last Interaction Date"] = when_str
    if status is not None:
        df.at[idx, "Status"] = status

    save(df)
    return True


def has_linkedin_touch(company: str, role: str | None = None,
                       contact_email: str | None = None) -> bool:
    """True if a LinkedIn connection note was already drafted for this lead.

    Detected from the Conversation Log marker written by `note_linkedin_draft`. Lets both the
    daily double-tap and the on-demand /linkedin-draft skill avoid drafting the same note twice.
    """
    df = load()
    idx = _find_row_index(df, contact_email or "", company=company, role=role)
    if idx is None:
        # company/role-only fallback (no email on hand)
        companies = df["Company"].fillna("").astype(str).str.strip().str.lower()
        mask = companies == (company or "").strip().lower()
        if role:
            roles = df["Role"].fillna("").astype(str).str.strip().str.lower()
            mask = mask & (roles == role.strip().lower())
        matches = df.index[mask].tolist()
        if not matches:
            return False
        idx = matches[0]
    return "(linkedin)" in str(df.at[idx, "Conversation Log"] or "").lower()


def note_linkedin_draft(company: str, role: str | None = None,
                        contact_email: str | None = None,
                        when: str | date | datetime | None = None) -> bool:
    """Record that a LinkedIn connection note was drafted for a lead — OFF-BOOK.

    Appends `[YYYY-MM-DD] Agent (LinkedIn): connection note drafted` to the row's Conversation
    Log ONLY. Deliberately does NOT touch Last Interaction Date or Status: LinkedIn is a manual,
    human-sent channel — it must not reset the email follow-up timer (`overdue_followups` keys off
    Last Interaction Date) nor count against any cap. Idempotent per day. Returns True if logged
    (False if the row wasn't found or a LinkedIn line already exists for today).
    """
    df = load()
    idx = _find_row_index(df, contact_email or "", company=company, role=role)
    if idx is None:
        return False
    when_str = _format_date(when or date.today())
    log = "" if pd.isna(df.at[idx, "Conversation Log"]) else str(df.at[idx, "Conversation Log"]).strip()
    entry = f"[{when_str}] Agent (LinkedIn): connection note drafted"
    if entry in log:            # already drafted today — don't duplicate
        return False
    df.at[idx, "Conversation Log"] = f"{log} \n {entry}" if log else entry
    save(df)                    # note: Last Interaction Date and Status intentionally untouched
    return True


def bulk_add(rows: Iterable[dict]) -> int:
    """Add multiple new contacts, skipping duplicates. Returns count of inserted rows."""
    added = 0
    for r in rows:
        if add_contact(
            company=r.get("Company", ""),
            role=r.get("Role", ""),
            contact_email=r.get("Contact Email", ""),
            status=r.get("Status", "Pending"),
            conversation_log=r.get("Conversation Log", ""),
            last_interaction_date=r.get("Last Interaction Date"),
        ):
            added += 1
    return added


def overdue_followups(followup_days: int | None = None) -> list[dict]:
    """Return leads due for their NEXT follow-up in a multi-touch sequence.

    Covers both 'Emailed' (0 follow-ups yet) and 'Followed Up' (mid-sequence) rows.
    The number of follow-ups already sent is derived from the Conversation Log
    ('Agent:' lines: 1 = cold only, 2 = cold+FU1, …). A lead is due when the
    business days since its last interaction exceed an escalating threshold
    (FOLLOWUP_DAYS, then +FOLLOWUP_GAP per touch → 4, 6, 8) and it has not yet
    received MAX_FOLLOWUPS follow-ups. Replied/Rejected/Interview rows are excluded.

    Each entry is a plain dict with the tracker columns plus 'biz_days_waiting'
    and 'followup_number' (1 = first follow-up, 2 = second, …). Most overdue first.
    """
    from datetime import date, datetime, timedelta
    import config as _cfg
    if followup_days is None:
        followup_days = _cfg.FOLLOWUP_DAYS
    max_fu = getattr(_cfg, "MAX_FOLLOWUPS", 1)
    gap = getattr(_cfg, "FOLLOWUP_GAP", 2)

    df = load()
    active = df[df["Status"].isin(["Emailed", "Followed Up"])].copy()

    # A COMPANY where a real person has replied is out of the automated sequence, on every row.
    # The Status filter above is per-ROW, and a company usually has several: Doctolib carries a
    # genuine human reply on one row and two other roles still marked `Emailed`, so an automated
    # follow-up was queued against a company that is mid-conversation with her. That is the exact
    # opposite of the draft-and-approve rule replies are meant to follow — another machine-sent
    # mail from the same address, on a different thread, while a person is waiting on an answer.
    # Keyed on the conversation LOG rather than Status: bounces and out-of-office autoresponders
    # are stamped `Replied` too, and those must NOT stop a follow-up.
    in_conversation = {
        str(r.get("Company") or "").strip().lower()
        for _, r in df.iterrows()
        if has_genuine_human_reply(r.get("Conversation Log"), str(r.get("Status") or ""))
    }
    in_conversation.discard("")

    def _biz_days(d_str: str) -> int:
        try:
            start = datetime.fromisoformat(str(d_str)[:10]).date()
        except Exception:
            return 0
        n, cur = 0, start
        today = date.today()
        while cur < today:
            cur += timedelta(days=1)
            if cur.weekday() < 5:
                n += 1
        return n

    due: list[dict] = []
    for rec in active.to_dict(orient="records"):
        if str(rec.get("Company") or "").strip().lower() in in_conversation:
            continue  # a person there is already talking to her — she answers, not the agent
        agent_touches = str(rec.get("Conversation Log", "")).count("Agent:")
        if agent_touches < 1 or agent_touches > max_fu:
            continue  # no cold on record, or the sequence is already exhausted
        waiting = _biz_days(rec.get("Last Interaction Date"))
        threshold = followup_days + gap * (agent_touches - 1)
        if waiting > threshold:
            rec["biz_days_waiting"] = waiting
            rec["followup_number"] = agent_touches  # this send is FU #agent_touches
            due.append(rec)

    return sorted(due, key=lambda r: r["biz_days_waiting"], reverse=True)


def today_send_counts() -> dict[str, int]:
    """Return {'cold': N, 'warm': N} emails actually sent today.

    Delegates to smtp_send._counts_path() so the number is always authoritative
    (written by smtp_send.py at send time, never derived from log parsing).
    """
    from smtp_send import today_send_counts as _smtp_counts
    return _smtp_counts()


# A logged "Contact:" line is NOT necessarily a human reply: imap_fetch also records hard
# bounces and auto-responders there (e.g. "[date] Contact: BOUNCED | Address not found …") before
# it flips the row's Status to Rejected. If the reply-rate learners count those, they train on
# delivery failures instead of people — which is exactly what was happening (145 of 162 Contact:
# lines were bounces). These patterns mark a Contact: line as non-human so it can be filtered out.
_NONHUMAN_REPLY_RE = re.compile(
    r"bounced|address not found|delivery status|delivery has failed|delivery failure|"
    r"undeliverable|mail delivery|mailer-daemon|failure notice|returned mail|address rejected|"
    r"out of office|auto[-\s]?reply|automatic reply|réponse automatique|absence|congés|"
    r"indisponible|ne pas répondre|do not reply|no[-\s]?reply|noreply|unsubscribe|désabonn|"
    # The markers imap_fetch itself stamps on the line (imap_fetch.AUTOREPLY_KINDS). This pattern
    # knew "auto-reply" but not "auto-ack", so a message the INGESTION had already identified as an
    # auto-acknowledgement — and labelled as such, in the very text being tested — still counted as
    # a genuine human reply. Doctolib's "votre demande n'a pas pu être prise en compte" was
    # emailed to Zineb weekly as a warm lead going cold, and was blocking its own follow-ups.
    r"\[auto[-\s]?ack\]|\[out[-\s]?of[-\s]?office\]",
    re.I)
_CONTACT_LINE_RE = re.compile(r"\]\s*Contact:\s*(.+)", re.I)

# Templated brush-offs that are NOT a person engaging with the pitch. These slipped past
# _NONHUMAN_REPLY_RE because they carry no bounce/OOO marker: a ticket-system notice
# ("Fermeture de votre demande", sent from a support desk with a "view in browser" footer),
# and the "thanks — here's our job board" canned reply, which one company sent verbatim to
# two different people on the same day. Counting them as replies inflated the warm-lead nudge
# list to 75% noise, which is why it stopped being read.
_TEMPLATE_REPLY_RE = re.compile(
    r"fermeture de votre demande|afficher dans le navigateur|view (this )?(e-?mail )?in (your )?browser|"
    r"[ée]quipe care|besoin de notre aide|"
    r"retrouve[rz][^.!?]{0,40}nos offres|consulte[rz][^.!?]{0,40}nos offres|nos offres d.emploi sur|"
    r"page emploi|abonn\w*[^.!?]{0,25}notre page|"
    r"browse our (current )?(job )?(openings|opportunities)|check out our (open )?(roles|positions)",
    re.I)

# An explicit "no". This IS a genuine human reply — someone read the pitch and answered, so it
# still counts for reply-rate learning — but the conversation is CLOSED, so it must not sit in
# the warm-lead nudge list pretending to be a near-miss. Grounded in the actual replies received
# (Sand to Green: "ne prévoyons pas de recrutement … pas donner suite … bonne continuation";
# the declining company: "nous n'avons pas de poste ouvert en alternance … le meilleur dans votre recherche").
# Deliberately phrase-level, never single words: "malheureusement" alone also prefixes "malheureusement
# je ne suis pas dispo cette semaine, mais la semaine prochaine", which is a LIVE thread.
_DECLINED_REPLY_RE = re.compile(
    r"pas donner suite|pas en mesure de donner suite|n.?a pas [ée]t[ée] retenue|"
    r"n.?(avons|ai|avez) pas de poste|pas de poste (ouvert|disponible|à pourvoir)|"
    r"ne pr[ée]voyons (toutefois )?pas de recrutement|pas de recrutement (en|pour|prévu)|"
    r"ne correspond pas [àa] (nos|notre|ce)|bonne continuation|"
    r"le meilleur dans (votre|vos) recherche|succ[èe]s dans (votre|vos) recherche|"
    r"not (be )?moving forward|decided not to (move|proceed|continue)|"
    r"(are|will) not be proceeding|unable to offer|no (open )?(positions?|openings?)|"
    r"best of luck (with|in) your|wish you (all )?the best in your",
    re.I)

# A reply that hands us a BETTER address ("adressez votre candidature à recruitment@acme.io").
# The single highest-value reply the system can receive — an invitation from the company itself —
# and it was being dropped: replies are draft-and-approve, so nothing acted on it, and the warm
# nudge showed only the first 120 characters, which cut off before the address. the fintech sent one on
# 2026-08-06 and it sat unused for 27 days.
# Everything from here on is the QUOTED original we sent, not what they wrote. Cutting it off
# matters: one decline quoted its own `À : Contact <contact@testco.com>` header, which
# reads as a redirect to the very inbox that just declined us.
_QUOTED_TAIL_RE = re.compile(
    r"-{2,}\s*message d.origine|-{2,}\s*original message|^\s*>|"
    r"\bLe .{0,60}?\ba [ée]crit\s*:|\bOn .{0,60}?\bwrote\s*:|"
    r"\bDe\s*:\s|\bFrom\s*:\s|\bEnvoy[ée]\s*:\s|\bSent\s*:\s",
    re.I | re.M)

_REDIRECT_RE = re.compile(
    r"(?:adress\w*|envoy\w*|transmett\w*|postul\w*|candidat\w*|[ée]cri\w*|invit\w*|"
    r"send|write|apply|forward|reach out)"
    r"[^.!?]{0,90}?([A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?\.[A-Za-z]{2,})",
    re.I)


# A calendar invitation. The rarest and most valuable event the pipeline produces — and the one
# it handled worst: a founder agreed to an alternance, negotiated a slot, and sent an invite for
# 3 July 2026 14:00. The thread then went silent for 44 business days while the nudge list showed
# it as an ordinary stall, its one-line preview filled with the invite's tracking URL.
_MEETING_INVITE_RE = re.compile(
    # A meeting NOUN is required. "vous invite à" alone also opens "je vous invite à adresser
    # votre candidature à recruitment@…", which is a redirect, not an invitation to meet.
    r"invite[zsr]?[^.!?]{0,20}[àa]\s+(?:un[e]?\s+|l['e]\s*)?(?:entretien|r[ée]union|rendez[- ]vous|"
    r"[ée]change|meeting|call)|"
    r"invitation[^.!?]{0,30}(?:entretien|r[ée]union|rendez[- ]vous|meeting|call)|"
    r"invites you to[^.!?]{0,30}(?:interview|meeting|call|chat)|"
    r"calendar\.[\w.-]+/|calendly\.com/|meet\.google\.com/|zoom\.us/j/|teams\.microsoft\.com/l/|"
    r"serez[- ]vous pr[ée]sent", re.I)
_URL_RE = re.compile(r"https?://\S+", re.I)


def looks_like_meeting_invite(text) -> bool:
    """True when a reply is (or contains) a calendar invitation to meet."""
    return bool(_MEETING_INVITE_RE.search(str(text or "")))


def strip_urls(text, keep: int = 0) -> str:
    """Drop URLs from a preview line. Tracking links are most of the bulk and none of the meaning."""
    return re.sub(r"\s{2,}", " ", _URL_RE.sub("", str(text or ""))).strip()


def looks_like_template_reply(text) -> bool:
    """True for a canned brush-off (ticket closure, 'see our job board') — not real engagement."""
    return bool(_TEMPLATE_REPLY_RE.search(str(text or "")))


def looks_like_rejection(text) -> bool:
    """True when the reply is an explicit no. Still a human reply; no longer a live thread."""
    return bool(_DECLINED_REPLY_RE.search(str(text or "")))


def redirect_address(text, exclude: "Iterable[str]" = ()) -> str | None:
    """The address a reply redirects us to ('write to recruitment@…'), or None.

    ``exclude`` drops addresses we already know — our own, and the row's current contact —
    so a signature block or a quoted header doesn't read as a redirect.
    """
    skip = {parseaddr(str(e))[1].strip().lower() for e in exclude if e}
    skip |= {"you@example.com", "you@example.com", "you@example.com"}
    body = str(text or "")
    cut = _QUOTED_TAIL_RE.search(body)
    if cut:
        body = body[:cut.start()]
    for m in _REDIRECT_RE.finditer(body):
        addr = m.group(1).strip().lower().rstrip(".,;:)")
        if addr in skip or addr.endswith((".png", ".jpg", ".gif")):
            continue
        return addr
    return None



# Written by scraper.persist() when the address is the slug fallback rather than a resolved
# domain. Rows carrying it cannot be emailed: the send gate refuses a generic inbox without
# positive evidence, and the domain is frequently not even the company's.
GUESSED_DOMAIN_MARK = "⚠ GUESSED DOMAIN"


def domain_is_guessed(conversation_log) -> bool:
    """True when the row's address was invented from the company name, not resolved."""
    return GUESSED_DOMAIN_MARK in str(conversation_log or "")


def reachability_stats() -> dict:
    """How much of the Pending queue is actually workable.

    The headline "1,469 pending" is misleading: most of it is generic inboxes on invented
    domains that no send will ever be allowed to use. This separates the queue into leads that
    can be worked now and leads that need enrichment first, so the dashboard stops implying
    ten months of runway that does not exist.
    """
    from email.utils import parseaddr as _pa
    GEN = {"contact", "hello", "info", "team", "jobs", "job", "career", "careers",
           "recrutement", "recrute", "rh", "hr", "talent", "hiring", "join", "work",
           "apply", "recruitment"}
    df = load()
    pend = df[df["Status"].astype(str).str.strip() == "Pending"]
    named = guessed = generic_resolved = 0
    for _, r in pend.iterrows():
        email = str(r.get("Contact Email") or "")
        local = _pa(email)[1].lower().split("@")[0]
        if "<" in email or (local and local not in GEN):
            named += 1
        elif domain_is_guessed(r.get("Conversation Log")):
            guessed += 1
        else:
            generic_resolved += 1
    return {"pending": len(pend), "named": named,
            "generic_resolved_domain": generic_resolved,
            "generic_guessed_domain": guessed,
            "workable_now": named,
            "needs_enrichment": generic_resolved + guessed}


def has_genuine_human_reply(conversation_log, status="") -> bool:
    """True iff a REAL person replied — bounces and auto-responders excluded.

    The CONVERSATION LOG is the authority, not the Status field. Status was treated as
    authoritative until the 2026-09 audit, which found it demonstrably unreliable: of 67 rows
    carrying a `Contact:` line, 55 were bounces and 6 were autoresponders, yet several were
    stamped `Replied` (Sekoia's out-of-office, Phalsbourg's and STEEL's acknowledgements, a the fintech
    bounce). imap_fetch no longer creates such rows, but ~1,500 historical rows still carry the
    bad stamp, and this predicate feeds stalled_conversations, strategy_stats (the strategy
    bandit) and learning.py (WS4) — so trusting Status would keep training all three on
    delivery failures and mailer-daemon.

    Rule: a lead has a genuine human reply iff at least one `Contact:` line is not a bounce, not
    an autoresponder, and not a canned template (ticket closure, "here's our job board" — see
    _TEMPLATE_REPLY_RE). `Interview Scheduled` is still trusted on its own, because that status is
    only ever set deliberately by a human, never by the inbox sync.

    An explicit REJECTION still counts here: a person did read the pitch and answer, which is the
    signal the reply-rate learners want. It is `stalled_conversations` that must drop it, because a
    closed thread is not a near-miss to re-engage.
    """
    if str(status or "").strip() == "Interview Scheduled":
        return True
    for m in _CONTACT_LINE_RE.finditer(str(conversation_log or "")):
        line = m.group(1)
        if _NONHUMAN_REPLY_RE.search(line) or _TEMPLATE_REPLY_RE.search(line):
            continue
        return True
    return False


def stalled_conversations(days: int = 5) -> list[dict]:
    """Warm leads going cold — a human replied but the thread has had no movement in `days` business
    days. These are near-misses: an interview or offer left on the table because a reply wasn't
    carried forward. Surfaced by /status and /followup-check so Zineb re-engages before it dies.

    A lead qualifies when it has a GENUINE human reply on record and its Status is neither resolved
    (Interview Scheduled) nor dead (Rejected). Most-stale first. Each entry carries 'biz_days_idle'
    and 'last_reply' (the last human line, trimmed) so the nudge can reference what they said.
    """
    from datetime import date as _d, datetime as _dt, timedelta as _td

    def _biz_idle(d_str: str) -> int:
        try:
            start = _dt.fromisoformat(str(d_str)[:10]).date()
        except Exception:
            return 0
        n, cur, today = 0, start, _d.today()
        while cur < today:
            cur += _td(days=1)
            if cur.weekday() < 5:
                n += 1
        return n

    df = load()
    out: list[dict] = []
    for rec in df.to_dict(orient="records"):
        status = str(rec.get("Status") or "").strip()
        if status in ("Rejected", "Interview Scheduled", "Pending"):
            continue
        log = str(rec.get("Conversation Log") or "")
        if not has_genuine_human_reply(log, status):
            continue
        idle = _biz_idle(rec.get("Last Interaction Date"))
        if idle < days:
            continue
        # last genuine human line, for context in the nudge
        last_reply, redirect_to = "", None
        for m in _CONTACT_LINE_RE.finditer(log):
            line = m.group(1)
            if _NONHUMAN_REPLY_RE.search(line) or _TEMPLATE_REPLY_RE.search(line):
                continue
            last_reply = line.strip()
            redirect_to = redirect_address(line, exclude=[rec.get("Contact Email")]) or redirect_to
        # An explicit "no" is a closed thread, not a near-miss. Leaving these in was most of why
        # this list read as noise — Sand to Green and the declining company both declined in writing while
        # still sitting here as leads to re-engage, because their Status was never moved off
        # `Replied` (and Status is not writable from here: the VM owns contacts.xlsx).
        if looks_like_rejection(last_reply):
            continue
        rec["biz_days_idle"] = idle
        rec["last_reply"] = strip_urls(last_reply)[:400]
        rec["meeting_invite"] = any(
            looks_like_meeting_invite(m.group(1)) for m in _CONTACT_LINE_RE.finditer(log))
        # Surfaced verbatim: the 120-char trim used to cut off exactly the part that mattered.
        rec["redirect_to"] = redirect_to
        out.append(rec)
    out.sort(key=lambda r: r["biz_days_idle"], reverse=True)
    return out


def strategy_stats() -> dict[str, dict]:
    """Parse Conversation Log entries and return reply-rate stats per strategy.

    Looks for lines matching:  [YYYY-MM-DD] Agent (Strategy:X): ...
    A row is counted as "replied" only on a GENUINE human reply (bounces / auto-responders
    excluded — see has_genuine_human_reply).

    Returns a dict keyed by strategy letter, e.g.:
        {'V': {'sent': 5, 'replied': 2, 'rate': 0.40}, ...}
    Only strategies that have been used at least once are included.
    """
    df = load()
    STRATEGY_RE = re.compile(r"\[[\d-]+\]\s+Agent\s+\(Strategy:([QOVMUAG])\):", re.IGNORECASE)

    stats: dict[str, dict] = {}

    for _, row in df.iterrows():
        log = str(row.get("Conversation Log") or "")
        strategies_in_row = STRATEGY_RE.findall(log)
        if not strategies_in_row:
            continue

        has_reply = has_genuine_human_reply(log, row.get("Status", ""))

        # Credit the FIRST strategy used (the cold email strategy that opened the thread)
        strategy = strategies_in_row[0].upper()
        if strategy not in stats:
            stats[strategy] = {"sent": 0, "replied": 0}
        stats[strategy]["sent"] += 1
        if has_reply:
            stats[strategy]["replied"] += 1

    for s in stats.values():
        s["rate"] = round(s["replied"] / s["sent"], 2) if s["sent"] else 0.0

    return stats


# All cold-email strategies (see /daily-agent). A = Agent Demo (the strongest card
# for AI-native companies). The agent still matches strategy to the company, but
# biases its choice toward proven winners once data exists.
ALL_STRATEGIES = {
    "Q": "Technical Question",
    "O": "Precise Observation",
    "V": "Value Proof First",
    "M": "Mirrored Challenge",
    "U": "Ultra-short",
    "A": "Agent Demo",
    "G": "Insight Gift",
}


def _wilson_lower_bound(replied: int, sent: int, z: float = 1.96) -> float:
    """95% Wilson lower bound on the reply rate — a confidence-adjusted score that ranks a
    strategy by how good it *reliably* is, not its raw (small-sample-noisy) rate. A 1/1 (raw
    100%) scores LOW; a 6/10 scores higher than a 1/1. Solves premature convergence on a
    strategy that was just lucky early."""
    if sent <= 0:
        return 0.0
    import math
    phat = replied / sent
    denom = 1 + z * z / sent
    centre = phat + z * z / (2 * sent)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * sent)) / sent)
    return max(0.0, (centre - margin) / denom)


def recommend_strategy_order(min_samples: int = 3, seed: int | None = None) -> dict:
    """Which cold-email opener to favour next — Thompson sampling over a Beta posterior.

    WHY NOT A POINT ESTIMATE. The previous version ranked arms by their Wilson lower bound and
    exploited the winner. With the real data that behaves badly: an arm with 0 replies scores
    exactly 0 and can NEVER climb back, so three strategies were permanently locked out on ~15
    samples each — and 3/23 vs 0/17 is not a real difference (Fisher exact p ≈ 0.24). The bandit
    was converging on noise and then refusing to re-test its own assumption.

    Thompson sampling fixes that structurally: each arm draws θ ~ Beta(1+replied, 1+not-replied)
    and the highest draw wins. A 0-reply arm still has posterior mass, so it keeps getting
    occasional shots; a genuinely better arm wins more and more often as evidence accumulates.
    Explore/exploit stops being a phase we switch between and becomes a property of the maths.

    Returns the same shape as before (`phase`, `ranked`, `recommend`, `note`) so callers and the
    /status dashboard are unaffected. `seed` makes a run reproducible for tests.
    """
    import random as _random

    stats = strategy_stats()
    rng = _random.Random(seed)
    rows = []
    for letter, name in ALL_STRATEGIES.items():
        st = stats.get(letter, {"sent": 0, "replied": 0, "rate": 0.0})
        sent, replied = int(st["sent"]), int(st["replied"])
        # Beta(1,1) prior = uniform: with no data every opener is equally plausible, which is
        # the honest starting belief.
        draw = rng.betavariate(1 + replied, 1 + max(sent - replied, 0))
        rows.append({
            "letter": letter, "name": name, "sent": sent, "replied": replied,
            "rate": st.get("rate", 0.0),
            "score": round(_wilson_lower_bound(replied, sent), 3),  # kept for display
            "posterior_mean": round((1 + replied) / (2 + sent), 3),
            "draw": round(draw, 4),
        })

    ranked = sorted(rows, key=lambda r: -r["draw"])
    top = ranked[0]
    total_sent = sum(r["sent"] for r in rows)
    total_rep = sum(r["replied"] for r in rows)
    undersampled = [r for r in rows if r["sent"] < min_samples]
    phase = "explore" if undersampled else "exploit"

    # Say plainly when the data cannot support a preference. Presenting a 13%-vs-0% split as a
    # finding, on 20-odd sends per arm, invites optimising the wrong thing.
    best_rate = max((r["rate"] for r in rows), default=0.0)
    worst_rate = min((r["rate"] for r in rows), default=0.0)
    thin = total_sent < 200 or total_rep < 15
    if thin:
        note = (f"Thompson sampling over {total_sent} sends / {total_rep} replies — still too "
                f"thin to call a winner ({best_rate*100:.0f}% vs {worst_rate*100:.0f}% at this "
                f"sample size is noise, not signal). Today's draw favours '{top['letter']}' "
                f"({top['name']}); take it as a die-roll weighted by evidence, not a verdict, "
                f"and let the company decide when another opener obviously fits better.")
    else:
        note = (f"Thompson sampling: '{top['letter']}' ({top['name']}, {top['rate']*100:.0f}% "
                f"over {top['sent']} sends) drew highest. Evidence is now thick enough "
                f"({total_sent} sends / {total_rep} replies) to lean on this.")
    return {"phase": phase, "ranked": ranked, "recommend": top["letter"], "note": note,
            "total_sent": total_sent, "total_replied": total_rep, "evidence_thin": thin}



# ── Lead prioritisation — spend the scarce daily cold slots on the best targets ──
# Short tokens (ai, ml, ia) are matched as WHOLE WORDS; multi-word phrases as
# substrings. This avoids the substring trap where "media"→"ia" or "domain"→"ai".

_AI_WORDS = {"ai", "ml", "ia", "llm", "nlp", "genai", "mlops", "dl"}
_AI_PHRASES = ("machine learning", "deep learning", "data scientist", "computer vision",
               "intelligence artificielle", "ingénieur ia", "ingenieur ia", "gen ai")
_BACKEND_WORDS = {"backend", "devops", "sre", "api", "fullstack", "platform"}
_BACKEND_PHRASES = ("back-end", "back end", "software engineer", "software developer",
                    "full-stack", "full stack", "développeur", "developpeur", "platform engineer")
_DATA_WORDS = {"data", "analytics", "données", "donnees"}
_DATA_PHRASES = ("data engineer", "data analyst", "data platform", "analytics engineer")


def _role_fit(role: str) -> tuple[int, str]:
    """Return (points, label) for how well a role matches Zineb's core skills."""
    import re as _re
    rl = (role or "").lower()
    words = set(_re.findall(r"[a-zà-ÿ]+", rl))
    if words & _AI_WORDS or any(p in rl for p in _AI_PHRASES):
        return 45, "AI/ML core fit"
    if words & _BACKEND_WORDS or any(p in rl for p in _BACKEND_PHRASES):
        return 32, "backend fit"
    if words & _DATA_WORDS or any(p in rl for p in _DATA_PHRASES):
        return 28, "data fit"
    return 12, "adjacent role"


def _is_named_email(email: str) -> bool:
    e = str(email or "")
    return "<" in e and ">" in e


def _email_quality(email, conversation_log="") -> str:
    """Classify a contact address: 'confirmed' named, 'guessed' named, 'generic', or 'none'.
    A named contact is 'guessed' when find-contacts flagged the email (catch-all/mx_only) with
    the '⚠ guessed email' note in the log. Shared by lead ranking and enrichment stats.

    NOTE: 'confirmed' here means the address is *name-formatted* (a real person, not a generic
    inbox) — NOT that the mailbox was verified deliverable. Deliverability is learned later, at
    send time; ranking layers that in via _cached_email_verdict() so a name-formatted but dead
    address doesn't keep a top slot it doesn't deserve."""
    e = str(email or "").strip()
    if e and _is_named_email(e):
        return "guessed" if "guessed email" in str(conversation_log or "").lower() else "confirmed"
    return "generic" if "@" in e else "none"


def _cached_email_verdict(email) -> str | None:
    """A KNOWN verification verdict from the local verify cache — free, no network, no quota.

    Returns 'valid' | 'invalid' | None (unknown). Lets ranking reflect deliverability we already
    learned at send time (e.g. Hunter flagged a name-formatted address as undeliverable) without
    spending any Hunter quota here. Unknown addresses keep the name-format heuristic unchanged."""
    from email.utils import parseaddr
    addr = parseaddr(str(email or ""))[1]
    if "@" not in addr:
        return None
    try:
        import email_verify
        rec = email_verify._cache_get(addr)
    except Exception:
        return None
    if not rec:
        return None
    conf = rec[1]
    if conf == "api_invalid":
        return "invalid"
    if conf == "api_valid":
        return "valid"
    return None  # api_risky / anything else → not decisive, leave the heuristic alone


def _domain_of(email: str) -> str:
    e = _norm_email(email)
    return e.split("@", 1)[1] if "@" in e else ""


# ESN / staffing / consulting bodyshops — high volume on the boards but low reply value for a
# candidate targeting AI/product startups. A MODEST down-rank (a bias, not exclusion) so genuine
# product companies surface first; a strong ESN AI role can still rank well. Shown in reasons.
_ESN_SIGNALS = (
    "consulting", "conseil", "consultant", "ingénierie", "ingenierie", "infogérance",
    "infogerance", " esn", "ssii", "services informatiques",
    "capgemini", "atos", "akkodis", "sopra", "inetum", "devoteam", "astek", "amaris",
    "viveris", "expleo", "assystem", "segula", "sogeti", "umanis", "keyrus", "micropole",
    "aubay", "meritis", "cellenza", "alten", "mc2i", "davidson",
)


def _is_esn(company: str) -> bool:
    return any(s in (company or "").lower() for s in _ESN_SIGNALS)


# Large employers (banks, CAC40 industry, big retail/logistics, global tech & consulting).
# For an ALTERNANCE search these are structurally low-yield on cold email: applications route
# through an ATS / formal campus-recruiting program (a cold inbox goes unread), and the aide
# unique à l'apprentissage is legally capped at employers < 250 salariés — so the cost lever
# that helps a small startup say "yes" does not even apply here. A MODEST down-rank (a bias, not
# exclusion) so reachable product startups fill the scarce daily slots first; a big-corp lead
# with a named decision-maker + a real alternance posting can still surface. Shown in reasons,
# and flagged (`likely_big_corp`) so /daily-agent prefers the careers-portal path over a cold send.
_BIG_CORP_SIGNALS = (
    # banks / insurance
    "bnp paribas", "société générale", "societe generale", "crédit agricole", "credit agricole",
    "crédit mutuel", "credit mutuel", "banque postale", "bpce", "natixis", "axa", "allianz",
    "generali", "cnp assurances", "groupama",
    # CAC40 / large industry & utilities
    "safran", "thales", "airbus", "dassault", "renault", "stellantis", "michelin",
    "schneider electric", "saint-gobain", "vinci", "bouygues", "orange", "edf", "engie",
    "totalenergies", "l'oréal", "l'oreal", "loreal", "danone", "veolia", "alstom", "sanofi",
    "legrand", "pernod ricard", "publicis", "capgemini",
    # big retail / logistics / transport
    "carrefour", "auchan", "leclerc", "decathlon", "mondial relay", "la poste", "sncf",
    "ratp", "air france", "fnac", "darty",
    # global tech & consulting
    "accenture", "deloitte", " kpmg", "ernst & young", " pwc", "wavestone",
    " ibm", "amazon", "microsoft", " google", "oracle", " sap ", "salesforce", "cognizant",
)


def _is_big_corp(company: str) -> bool:
    return any(s in f" {(company or '').lower()} " for s in _BIG_CORP_SIGNALS)


def recently_contacted_domains(days: int = 7) -> set[str]:
    """Domains we've already sent to (Emailed/Followed Up/Replied/Interview) within `days`.

    Used to avoid cold-emailing the same company twice in a short window — e.g. when a
    company has several open roles, we should not hit the same inbox repeatedly.
    """
    from datetime import date, datetime, timedelta
    cutoff = date.today() - timedelta(days=days)
    contacted_statuses = {"Emailed", "Followed Up", "Replied", "Interview Scheduled"}
    df = load()
    out: set[str] = set()
    for _, r in df.iterrows():
        if str(r.get("Status", "")).strip() not in contacted_statuses:
            continue
        try:
            d = datetime.fromisoformat(str(r.get("Last Interaction Date"))[:10]).date()
        except Exception:
            continue
        if d >= cutoff:
            dom = _domain_of(str(r.get("Contact Email") or ""))
            if dom:
                out.add(dom)
    return out


def funnel() -> dict:
    """Outreach conversion funnel + rates. Surfaced by /status.

    Returns counts per stage and the key conversion rates:
      reply_rate    = replied(+interview) / contacted
      interview_rate= interview / contacted
    where 'contacted' = rows ever emailed (Emailed/Followed Up/Replied/Interview/Rejected-after-contact).
    """
    df = load()
    status = df["Status"].astype(str).str.strip()
    counts = {s: int((status == s).sum()) for s in VALID_STATUSES}
    # 'contacted' = anything that left Pending via an email (everything except Pending)
    contacted = int((status != "Pending").sum())
    replied = counts.get("Replied", 0) + counts.get("Interview Scheduled", 0)
    interview = counts.get("Interview Scheduled", 0)
    # Status overstates this. Of the 11 rows stamped `Replied` on 2026-09-02, several were
    # canned templates or bounces the inbox sync mis-stamped before the 2026-09 audit. The raw
    # counts stay (they match the spreadsheet), but the honest number is reported next to them
    # so /status and the dashboard stop showing progress that did not happen.
    genuine = sum(
        1 for rec in df.to_dict(orient="records")
        if str(rec.get("Status") or "").strip() != "Pending"
        and has_genuine_human_reply(rec.get("Conversation Log"), rec.get("Status"))
    )
    return {
        "total": len(df),
        "pending": counts.get("Pending", 0),
        "emailed": counts.get("Emailed", 0),
        "followed_up": counts.get("Followed Up", 0),
        "replied": counts.get("Replied", 0),
        "interview": interview,
        "rejected": counts.get("Rejected", 0),
        "contacted": contacted,
        "reply_rate": round(replied / contacted, 3) if contacted else 0.0,
        "interview_rate": round(interview / contacted, 3) if contacted else 0.0,
        "replied_genuine": genuine,
        "reply_rate_genuine": round(genuine / contacted, 3) if contacted else 0.0,
    }


def enrichment_stats() -> dict:
    """How enriched the pipeline is — named decision-maker vs generic inbox, and (among named)
    confirmed vs guessed (from the '⚠ guessed email' note the find-contacts skill writes).
    Counts active rows (excludes Rejected). Surfaced by /status.
    """
    df = load()
    status = df["Status"].astype(str).str.strip()
    active = df[status != "Rejected"]
    named_confirmed = named_guessed = generic = 0
    for _, r in active.iterrows():
        q = _email_quality(r.get("Contact Email"), r.get("Conversation Log"))
        if q == "confirmed":
            named_confirmed += 1
        elif q == "guessed":
            named_guessed += 1
        else:
            generic += 1
    total = len(active)
    named = named_confirmed + named_guessed
    return {
        "active": total,
        "named": named,
        "named_confirmed": named_confirmed,
        "named_guessed": named_guessed,
        "generic": generic,
        "named_rate": round(named / total, 3) if total else 0.0,
    }


def enrichment_queue(limit: int | None = None, include_contacted: bool = False) -> list[dict]:
    """Which rows /find-contacts should enrich TODAY, in the order they will actually be emailed.

    Enrichment is the highest-leverage thing that happens off the send path: a named
    decision-maker scores +25 in rank_pending_leads against +8 for a generic inbox, and it is the
    difference between a message a person reads and one that lands in a shared contact@ nobody
    owns. But it is also capped (config.ENRICH_CAP, 15/day) against ~1,070 generic rows — roughly
    seventy days of work — so WHICH fifteen get done is the entire decision.

    Until now nothing made that choice: the skill dumped all ~1,720 rows as JSON and picked by
    eye. So a day's enrichment could land on leads sitting 900 deep in the queue, or on postings
    already three months stale, while the companies about to be emailed tomorrow stayed generic.
    This orders the queue the way the sender does — same score, same staleness penalty — so the
    fifteen enriched are the fifteen about to be used.

    Two exceptions to score order, both deliberate:
      * a hard-BOUNCED address comes first at any score. Its row is invisible everywhere else
        (the send gate refuses it, rank_pending_leads drops it entirely), so nothing but this
        skill will ever look at it again — and one dead scraped address commonly strands every
        open role at that company.
      * rows already contacted are only included with include_contacted (the skill's --all),
        since a generic inbox that already received mail is not blocking anything.

    Returns [{Company, Role, Contact Email, why, score, blocked, reasons}] highest-priority first.
    """
    import config as _cfg

    df = load()
    keep = {"Pending", "Emailed", "Followed Up"} if include_contacted else {"Pending"}
    rows = df[df["Status"].astype(str).str.strip().isin(keep)]

    try:
        import bounce_guard as _bg
        blocked_of = lambda e: _bg.is_blocked(e)
    except Exception:
        blocked_of = lambda e: (False, "")

    # Send-queue position, by company: the ranker already dropped bounced rows, so a blocked row
    # simply has no score here and is ordered by its own rule below.
    ranked = {}
    for row in rank_pending_leads(dedupe_by_company=False):
        k = str(row.get("Company") or "").strip().lower()
        ranked[k] = max(ranked.get(k, -999), row.get("raw_score", row.get("score", 0)))

    out = []
    for _, r in rows.iterrows():
        email = str(r.get("Contact Email") or "")
        company = str(r.get("Company") or "")
        if is_junk_company(company):
            continue
        hit, why_blocked = blocked_of(email)
        quality = _email_quality(email, r.get("Conversation Log"))
        if not hit and quality != "generic":
            continue  # already has a named contact — nothing to enrich
        out.append({
            "Company": company,
            "Role": str(r.get("Role") or ""),
            "Contact Email": email,
            "blocked": bool(hit),
            "why": (f"hard-bounced — row is invisible until a new address is found ({why_blocked})"
                    if hit else "generic inbox — no named decision-maker"),
            "score": ranked.get(company.strip().lower(), 0),
        })

    # One row per company: enrichment finds a PERSON, and that person serves every open role there.
    seen, deduped = set(), []
    for row in sorted(out, key=lambda x: (not x["blocked"], -x["score"])):
        k = row["Company"].strip().lower()
        if k in seen:
            continue
        seen.add(k)
        deduped.append(row)
    return deduped[:limit] if limit else deduped


def rank_pending_leads(limit: int | None = None, cooldown_days: int = 7,
                       dedupe_by_company: bool = True) -> list[dict]:
    """Score & order `Pending` rows so the limited daily cold slots go to the best leads.

    Transparent 0–100 score (higher = email sooner):
      role fit            up to 45  (AI/ML core 45 > backend 32 > data 28 > other 12)
      contract match      up to 28  (alternance POSTING 28 ≫ cdi/unspecified reframe 14 > stage 6)
      deliverability      up to 25  (named decision-maker w/ <addr> 25 > generic contact@ 8)
      posting age      +6/-22       (fresh ≤21d … likely-closed >90d; unknown & speculative = 0)
      speculative bonus    +8       ([Suggested] hidden-market = proactive, less competition)
      big-corp penalty    -18       (large employer: ATS/campus-only, AUA aid n/a < 250 salariés)
      ESN penalty         -12       (staffing bodyshop — lower fit)
      cooldown penalty    -60       (domain already emailed within cooldown_days)
    Rows whose address is on the hard-bounce blocklist are EXCLUDED entirely (not scored): the
    send gate would refuse them, so ranking them only wastes a research pass. They need a new
    address from /find-contacts before they are workable again.
    Alternance-intent is the scarce, decisive signal for a work-study search, so an explicit
    alternance posting far outweighs a generic CDI reframe. Big employers are down-ranked because
    cold email doesn't reach them and the apprenticeship aid legally excludes them (< 250 rule).
    Returns highest-first list of
      {Company, Role, Contact Email, score, on_cooldown, likely_big_corp, reasons}.
    """
    import config as _cfg

    df = load()
    cooled = recently_contacted_domains(cooldown_days)
    pending = df[df["Status"].astype(str).str.strip() == "Pending"]
    try:
        import lead_age as _la
        _age_cache = _la.load()      # read the sidecar once, not once per row
    except Exception:
        _age_cache = {}
    out = []
    # A lead whose stored address already hard-bounced cannot be emailed at all — the send gate
    # refuses it. Surfacing it would spend a research pass (and a slice of the 5h Claude window)
    # on a message that can never leave. The pattern is common: one scraped decision-maker address
    # bounces, and every Pending row for that company inherits it — ten open roles stranded on a
    # single dead mailbox, while the company answers fine on a different one. These are not dead
    # leads, they are leads missing a usable address, so they are excluded here and handed to
    # /find-contacts instead.
    try:
        import bounce_guard as _bg
        _addr_blocked = lambda e: _bg.is_blocked(e)[0]
    except Exception:
        _addr_blocked = lambda e: False      # bookkeeping failure must never hide real leads

    for _, r in pending.iterrows():
        if is_junk_company(str(r.get("Company") or "")):
            continue  # scraper artefact — never surfaces in the priority queue
        if _addr_blocked(str(r.get("Contact Email") or "")):
            continue  # address hard-bounced — unworkable until /find-contacts finds another
        role = str(r.get("Role") or "")
        rl = role.lower()
        email = str(r.get("Contact Email") or "")
        score = 0
        reasons = []

        # role fit (word-boundary aware)
        pts, label = _role_fit(role)
        score += pts; reasons.append(label)

        # contract match
        ct = _cfg.guess_contract_type(role)
        if ct == "alternance":
            score += 28; reasons.append("★ alternance posting (they already want an alternant)")
        elif ct in ("cdi", "unspecified", "cdd"):
            score += 14; reasons.append(f"{ct} (reframe applies)")
        elif ct == "speculative":
            score += 14; reasons.append("speculative")
        else:  # stage
            score += 6; reasons.append("stage (upsell)")

        # international / remote-foreign tilt — Zineb wants these prioritised (config.INTL_RANK_BOOST).
        # These are English, internship/CDI-ask leads (never alternance); the boost is tunable via .env.
        if _cfg.is_remote_international(role) and _cfg.INTL_RANK_BOOST:
            score += _cfg.INTL_RANK_BOOST; reasons.append("★ international (remote) — priority target")

        # deliverability — a CONFIRMED named contact beats a guessed one beats a generic inbox.
        # A free (cache-only) verification peek keeps the priority list honest: a name-formatted
        # address we've ALREADY learned is undeliverable will fall back to contact@ at send time,
        # so it should rank like a generic inbox, not hold a top slot.
        quality = _email_quality(email, r.get("Conversation Log"))
        verdict = _cached_email_verdict(email)
        if quality in ("confirmed", "guessed") and verdict == "invalid":
            score += 8; reasons.append("named email known-undeliverable → contact@ fallback")
        elif quality == "confirmed":
            score += 25
            reasons.append("named decision-maker (verified live)" if verdict == "valid"
                           else "named decision-maker (confirmed)")
        elif quality == "guessed":
            score += 16; reasons.append("named decision-maker (guessed email)")
        elif quality == "generic":
            score += 8; reasons.append("generic email")

        # speculative bonus (proactive, often less competition)
        if rl.startswith("[suggested]"):
            score += 8; reasons.append("proactive pitch")

        # ★★ WARM/REFERRAL path — Zineb knows someone here. Referrals convert 5-10× cold, so this
        # dominates the ranking (a warm lead should be emailed before any cold one). Lazy import
        # avoids a warm_network<->tracker cycle; the boost is 0 when she has no contact there.
        try:
            import warm_network as _wn
            _warm = _wn.summary(str(r.get("Company") or ""))
            if _warm:
                score += 40; reasons.append(f"★★ WARM: {_warm}")
        except Exception:
            pass

        # ★ SCHOOL/CFA partner — a company that recruits alternants from Zineb's M1 program. Reachable
        # for alternance THROUGH the school (even if a big corp where cold email dies). +18 roughly
        # offsets the big-corp/ESN penalty so partners aren't buried; the flag lets /daily-agent route
        # a big-corp partner to the application path (portal + /cover-letter) with the school angle.
        school_partner = False
        try:
            import school_partners as _sp
            _sch = _sp.summary(str(r.get("Company") or ""))
            if _sch:
                school_partner = True
                score += 18; reasons.append(f"★ school partner ({_sch})")
        except Exception:
            pass

        # ★ GLOBAL BRAND — a recognizable international employer that hires juniors/alternants IN
        # France. This is the reachable-international lever (Zineb wants outreach to lean intl, but
        # only intl that can sign a French contract). A "cold" brand (Paris-HQ scale-up) is emailable
        # → solid boost; a "portal" giant is surfaced but cold email won't land → smaller boost, and
        # the big-corp down-rank below steers it to the application path. The channel flag lets
        # /daily-agent pick the right move (warm cold email vs. portal + /cover-letter).
        global_brand = ""
        try:
            import global_brands as _gb
            _brand = _gb.match(str(r.get("Company") or ""))
            if _brand:
                global_brand = _brand.get("channel", "cold")
                boost = (_cfg.GLOBAL_BRAND_BOOST_COLD if global_brand == "cold"
                         else _cfg.GLOBAL_BRAND_BOOST_PORTAL)
                score += boost; reasons.append(f"★ global brand ({_gb.summary(str(r.get('Company') or ''))})")
        except Exception:
            pass

        # school / CFA / job board — posts the ad, does not employ (see is_training_body)
        if is_training_body(str(r.get("Company") or "")):
            score -= 30; reasons.append("⛔ school/CFA/job board — posts ads, does not employ")

        # ESN / staffing bodyshop — modest down-rank vs genuine product startups
        if _is_esn(str(r.get("Company") or "")):
            score -= 12; reasons.append("ESN/staffing — lower fit")

        # Large employer — cold email won't reach them (ATS/campus recruiting) and the
        # apprenticeship aid excludes ≥250 salariés. Down-rank + flag for the portal path.
        likely_big_corp = _is_big_corp(str(r.get("Company") or ""))
        if likely_big_corp:
            score -= 18; reasons.append("⛔ large employer — apply via careers portal, not cold email")

        # ★ POSTING AGE — the queue is ~10 months deep at the daily cold cap, so without this the
        # agent works its way through leads whose roles closed long ago: 46% of the Pending pool is
        # already older than 45 days. Writing "au sujet de votre offre" about a filled post reads as
        # spam and burns a scarce slot plus a Hunter verification. Unknown age stays neutral, and a
        # [Suggested] speculative pitch is exempt — it has no posting to expire.
        try:
            import lead_age as _age
            adelta, areason = _age.age_bucket(str(r.get("Company") or ""), role, _age_cache)
            if adelta:
                score += adelta; reasons.append(areason)
        except Exception:
            pass

        # learned nudge (WS4): a small, DATA-GATED adjustment from observed reply rates per
        # company-type / contract-intent. Returns 0 until a bucket has enough real replies, so
        # ranking is unchanged while data is thin. Lazy import avoids a learning<->tracker cycle.
        try:
            import learning as _learning
            ldelta, lreason = _learning.score_delta(str(r.get("Company") or ""), role)
            if ldelta:
                score += ldelta; reasons.append(lreason)
        except Exception:
            pass

        # over-contact cooldown: this company's domain was emailed in the last N days
        on_cooldown = _domain_of(email) in cooled
        if on_cooldown:
            score -= 60; reasons.append(f"⏳ contacted <{cooldown_days}d ago — wait")

        out.append({
            "Company": r.get("Company"), "Role": role,
            "Contact Email": email, "score": max(min(score, 100), 0),
            # Ranking sorts on the RAW score. The 0-100 clamp is presentation only: warm (+40),
            # school partner (+18) and global brand (+15) stack past 100, so sorting on the
            # clamped value flattened the whole top of the queue into a tie at 100 and broke it
            # by spreadsheet row order — losing exactly the distinctions that pick the day's sends.
            "raw_score": score,
            "on_cooldown": on_cooldown,
            "likely_big_corp": likely_big_corp,
            "school_partner": school_partner,
            "global_brand": global_brand,
            # Location mode (remote|hybrid|onsite|"") — informative only, NO ranking bias: Zineb
            # pursues both remote and in-person. /daily-agent uses it to frame the email
            # (remote-from-France vs in-person availability). [Remote/International] → "remote".
            "location_mode": _cfg.classify_location(role),
            "reasons": ", ".join(reasons),
        })

    out.sort(key=lambda x: x["raw_score"], reverse=True)

    if dedupe_by_company:
        # One company should occupy ONE slot in the shortlist — a company with 10 open roles was
        # flooding the top of the queue (e.g. Mistral held 7/15), crowding out other employers and
        # halving the distinct companies the daily agent reaches. Keep the highest-scoring role per
        # company (the list is already score-sorted). The cooldown makes multi-role sends pointless
        # within a week anyway. Pass dedupe_by_company=False for the full per-role view.
        seen, deduped = set(), []
        for row in out:
            key = str(row.get("Company") or "").strip().lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        out = deduped

    return out[:limit] if limit else out



# Statuses that mean "this row's address actually received a delivered email". A bounce is
# never one of them: imap_fetch flips a bounced row to `Rejected` and adds the address to
# bounce_guard, so surviving in this set is positive proof the mailbox accepted mail.
DELIVERED_STATUSES = {"Emailed", "Followed Up", "Replied", "Interview Scheduled"}


def address_has_delivered_mail(address: str) -> bool:
    """True when `address` has already received a delivered email from us.

    This is the strongest mailbox evidence the system can hold — stronger than any
    verification API — because the mail was accepted and never bounced back. Used by
    smtp_send to let a FOLLOW-UP through when the verifier is unavailable: refusing to
    follow up with someone we already reached is a pure loss (it costs the highest-
    converting channel) with no deliverability upside.

    Keyed on the ADDRESS, not the row: one mailbox is reused across many rows, and a
    re-scrape mints fresh `Pending` rows carrying an address that was emailed long ago.
    """
    addr = parseaddr(str(address or ""))[1].strip().lower()
    if "@" not in addr:
        return False
    df = load()
    status = df["Status"].fillna("").astype(str).str.strip()
    emails = df["Contact Email"].fillna("").astype(str).map(
        lambda v: parseaddr(v)[1].strip().lower())
    return bool(((emails == addr) & status.isin(DELIVERED_STATUSES)).any())


if __name__ == "__main__":
    df = load()
    print(f"contacts.xlsx at {EXCEL_PATH}")
    print(f"rows: {len(df)}")
    print(df.head(10).to_string(index=False) if not df.empty else "(empty)")
