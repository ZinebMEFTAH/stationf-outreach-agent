"""Excel tracking module for contacts.xlsx.

Strictly enforces the 6-column schema defined in instructions.txt:
Company | Role | Contact Email | Conversation Log | Last Interaction Date | Status
"""
from __future__ import annotations

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
    """Return Emailed rows with no reply for longer than followup_days business days.

    Sorted by most overdue first. Each entry is a plain dict with the tracker
    columns plus 'biz_days_waiting'.
    """
    from datetime import date, datetime, timedelta
    import config as _cfg
    if followup_days is None:
        followup_days = _cfg.FOLLOWUP_DAYS

    df = load()
    emailed = df[df["Status"] == "Emailed"].copy()

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

    emailed["biz_days_waiting"] = emailed["Last Interaction Date"].apply(_biz_days)
    overdue = emailed[emailed["biz_days_waiting"] > followup_days]
    return overdue.sort_values("biz_days_waiting", ascending=False).to_dict(orient="records")


def today_send_counts() -> dict[str, int]:
    """Return {'cold': N, 'warm': N} emails actually sent today.

    Delegates to smtp_send._counts_path() so the number is always authoritative
    (written by smtp_send.py at send time, never derived from log parsing).
    """
    from smtp_send import today_send_counts as _smtp_counts
    return _smtp_counts()


def strategy_stats() -> dict[str, dict]:
    """Parse Conversation Log entries and return reply-rate stats per strategy.

    Looks for lines matching:  [YYYY-MM-DD] Agent (Strategy:X): ...
    A row is counted as "replied" when its log contains a `Contact:` entry OR
    its Status is Replied / Interview Scheduled.

    Returns a dict keyed by strategy letter, e.g.:
        {'V': {'sent': 5, 'replied': 2, 'rate': 0.40}, ...}
    Only strategies that have been used at least once are included.
    """
    import re

    df = load()
    STRATEGY_RE = re.compile(r"\[[\d-]+\]\s+Agent\s+\(Strategy:([QOVMUAG])\):", re.IGNORECASE)
    CONTACT_RE = re.compile(r"\[[\d-]+\]\s+Contact:", re.IGNORECASE)

    stats: dict[str, dict] = {}

    for _, row in df.iterrows():
        log = str(row.get("Conversation Log") or "")
        strategies_in_row = STRATEGY_RE.findall(log)
        if not strategies_in_row:
            continue

        has_reply = (
            bool(CONTACT_RE.search(log))
            or str(row.get("Status", "")).strip() in {"Replied", "Interview Scheduled"}
        )

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
                     "sent": s["sent"], "replied": s["replied"], "rate": s.get("rate", 0.0)})

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

    # EXPLOIT: rank by reply rate, tie-break by fewer sends (keep exploring)
    ranked = sorted(rows, key=lambda r: (-r["rate"], r["sent"]))
    top = ranked[0]
    note = (f"EXPLOIT phase: enough data on all strategies. Favour '{top['letter']}' "
            f"({top['name']}, {top['rate']*100:.0f}% reply rate) when it fits — but keep "
            f"occasionally trying the least-used arm to stay adaptive.")
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


def _domain_of(email: str) -> str:
    e = _norm_email(email)
    return e.split("@", 1)[1] if "@" in e else ""


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
        email = str(r.get("Contact Email") or "").strip()
        if email and _is_named_email(email):
            if "guessed email" in str(r.get("Conversation Log") or "").lower():
                named_guessed += 1
            else:
                named_confirmed += 1
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
      contract match      up to 20  (alternance posting 20 > cdi/unspecified 14 > stage 6)
      deliverability      up to 25  (named decision-maker w/ <addr> 25 > generic contact@ 8)
      speculative bonus    +8       ([Suggested] = proactive, often less competition)
    Returns highest-first list of {Company, Role, Contact Email, score, reasons}.
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
            score += 20; reasons.append("alternance posting (direct fit)")
        elif ct in ("cdi", "unspecified", "cdd"):
            score += 14; reasons.append(f"{ct} (reframe applies)")
        elif ct == "speculative":
            score += 14; reasons.append("speculative")
        else:  # stage
            score += 6; reasons.append("stage (upsell)")

        # deliverability
        if _is_named_email(email):
            score += 25; reasons.append("named decision-maker")
        elif "@" in email:
            score += 8; reasons.append("generic email")

        # speculative bonus (proactive, often less competition)
        if rl.startswith("[suggested]"):
            score += 8; reasons.append("proactive pitch")

        # over-contact cooldown: this company's domain was emailed in the last N days
        on_cooldown = _domain_of(email) in cooled
        if on_cooldown:
            score -= 60; reasons.append(f"⏳ contacted <{cooldown_days}d ago — wait")

        out.append({
            "Company": r.get("Company"), "Role": role,
            "Contact Email": email, "score": max(min(score, 100), 0),
            "on_cooldown": on_cooldown,
            "reasons": ", ".join(reasons),
        })

    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit] if limit else out


if __name__ == "__main__":
    df = load()
    print(f"contacts.xlsx at {EXCEL_PATH}")
    print(f"rows: {len(df)}")
    print(df.head(10).to_string(index=False) if not df.empty else "(empty)")
