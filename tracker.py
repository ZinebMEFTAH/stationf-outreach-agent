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
        mask = (companies == company.strip().lower()) & (roles == role.strip().lower())
        if email_norm:
            mask = mask & (emails == email_norm)
        matches = df.index[mask].tolist()
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
) -> bool:
    """Safely append a single interaction line to the Conversation Log.

    direction must be "Agent" or "Contact". The line is formatted as
        "[YYYY-MM-DD] Agent: <msg>" and joined to existing content with " \\n ".
    When company+role are supplied the matching (Company, Role) row is updated
    (multiple roles can share an email); otherwise the first email match wins.
    Updates Last Interaction Date and optionally Status. Returns True on success.
    """
    if direction not in {"Agent", "Contact"}:
        raise ValueError("direction must be 'Agent' or 'Contact'")
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'")

    df = load()
    idx = _find_row_index(df, contact_email, company=company, role=role)
    if idx is None:
        return False

    when_str = _format_date(when or date.today())
    summary = (message or "").strip().replace("\n", " ").replace("\r", " ")
    entry = f"[{when_str}] {direction}: {summary}"

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
    r"indisponible|ne pas répondre|do not reply|no[-\s]?reply|noreply|unsubscribe|désabonn",
    re.I)
_CONTACT_LINE_RE = re.compile(r"\]\s*Contact:\s*(.+)", re.I)


def has_genuine_human_reply(conversation_log, status="") -> bool:
    """True iff a REAL person replied — bounces and auto-responders excluded.

    The clean, authoritative signal is the Status field (imap_fetch sets Replied / Interview
    Scheduled for genuine replies, Rejected for hard bounces). We ALSO accept a non-bounce
    `Contact:` line, to catch a reply logged before a status update — but bounce / out-of-office /
    auto-reply lines are ignored. This is the single source of truth for "did this outreach earn a
    human response", shared by strategy_stats (strategy bandit) and learning.py (WS4) so neither
    trains on delivery failures.
    """
    if str(status or "").strip() in ("Replied", "Interview Scheduled"):
        return True
    for m in _CONTACT_LINE_RE.finditer(str(conversation_log or "")):
        if not _NONHUMAN_REPLY_RE.search(m.group(1)):
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
        last_reply = ""
        for m in _CONTACT_LINE_RE.finditer(log):
            if not _NONHUMAN_REPLY_RE.search(m.group(1)):
                last_reply = m.group(1).strip()[:120]
        rec["biz_days_idle"] = idle
        rec["last_reply"] = last_reply
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


def recommend_strategy_order(min_samples: int = 3) -> dict:
    """Multi-armed-bandit guidance for which cold-email strategy to favour.

    Epsilon-greedy logic (same idea as the project's RL Q-learning work):
      - EXPLORE phase: while any strategy has < min_samples sends, prioritise the
        least-tried strategies so every arm gets data before we judge it.
      - EXPLOIT phase: once all strategies have enough samples, rank by reply rate
        (best first) but always keep the least-used arm in play to avoid premature
        convergence on a strategy that was just lucky early.

    Returns:
      {
        "phase": "explore" | "exploit",
        "ranked": [ {letter, name, sent, replied, rate} ... ],  # best/most-needed first
        "recommend": "Q",          # the single top suggestion for today
        "note": "human-readable guidance for the agent"
      }
    The agent uses this as a BIAS, not a hard rule — strategy must still fit the company.
    """
    stats = strategy_stats()
    rows = []
    for letter, name in ALL_STRATEGIES.items():
        s = stats.get(letter, {"sent": 0, "replied": 0, "rate": 0.0})
        rows.append({"letter": letter, "name": name,
                     "sent": s["sent"], "replied": s["replied"], "rate": s.get("rate", 0.0),
                     "score": round(_wilson_lower_bound(s["replied"], s["sent"]), 3)})

    undersampled = [r for r in rows if r["sent"] < min_samples]

    if undersampled:
        # EXPLORE: least-tried first (gather data on every strategy)
        ranked = sorted(rows, key=lambda r: (r["sent"], -r["rate"]))
        top = ranked[0]
        note = (f"EXPLORE phase: {len(undersampled)} strategy(ies) still under "
                f"{min_samples} samples. Prefer under-used strategies to gather data — "
                f"try '{top['letter']}' ({top['name']}) when it fits the company.")
        return {"phase": "explore", "ranked": ranked,
                "recommend": top["letter"], "note": note}

    # EXPLOIT: rank by the Wilson lower bound (confidence-adjusted), tie-break by raw rate.
    # This favours strategies that are reliably good over ones that were merely lucky early.
    ranked = sorted(rows, key=lambda r: (-r["score"], -r["rate"]))
    top = ranked[0]
    note = (f"EXPLOIT phase: enough data on all strategies. Favour '{top['letter']}' "
            f"({top['name']}, {top['rate']*100:.0f}% over {top['sent']} sends) when it fits — "
            f"ranked by confidence‑adjusted rate; keep occasionally trying the least‑used arm.")
    return {"phase": "exploit", "ranked": ranked, "recommend": top["letter"], "note": note}


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
    the '⚠ guessed email' note in the log. Shared by lead ranking and enrichment stats."""
    e = str(email or "").strip()
    if e and _is_named_email(e):
        return "guessed" if "guessed email" in str(conversation_log or "").lower() else "confirmed"
    return "generic" if "@" in e else "none"


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


def rank_pending_leads(limit: int | None = None, cooldown_days: int = 7) -> list[dict]:
    """Score & order `Pending` rows so the limited daily cold slots go to the best leads.

    Transparent 0–100 score (higher = email sooner):
      role fit            up to 45  (AI/ML core 45 > backend 32 > data 28 > other 12)
      contract match      up to 28  (alternance POSTING 28 ≫ cdi/unspecified reframe 14 > stage 6)
      deliverability      up to 25  (named decision-maker w/ <addr> 25 > generic contact@ 8)
      speculative bonus    +8       ([Suggested] hidden-market = proactive, less competition)
      big-corp penalty    -18       (large employer: ATS/campus-only, AUA aid n/a < 250 salariés)
      ESN penalty         -12       (staffing bodyshop — lower fit)
      cooldown penalty    -60       (domain already emailed within cooldown_days)
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
    out = []
    for _, r in pending.iterrows():
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

        # deliverability — a CONFIRMED named contact beats a guessed one beats a generic inbox
        quality = _email_quality(email, r.get("Conversation Log"))
        if quality == "confirmed":
            score += 25; reasons.append("named decision-maker (confirmed)")
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

        # ESN / staffing bodyshop — modest down-rank vs genuine product startups
        if _is_esn(str(r.get("Company") or "")):
            score -= 12; reasons.append("ESN/staffing — lower fit")

        # Large employer — cold email won't reach them (ATS/campus recruiting) and the
        # apprenticeship aid excludes ≥250 salariés. Down-rank + flag for the portal path.
        likely_big_corp = _is_big_corp(str(r.get("Company") or ""))
        if likely_big_corp:
            score -= 18; reasons.append("⛔ large employer — apply via careers portal, not cold email")

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
            "on_cooldown": on_cooldown,
            "likely_big_corp": likely_big_corp,
            "school_partner": school_partner,
            "reasons": ", ".join(reasons),
        })

    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit] if limit else out


if __name__ == "__main__":
    df = load()
    print(f"contacts.xlsx at {EXCEL_PATH}")
    print(f"rows: {len(df)}")
    print(df.head(10).to_string(index=False) if not df.empty else "(empty)")
