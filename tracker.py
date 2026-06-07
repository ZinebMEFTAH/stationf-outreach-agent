"""Excel tracking module for contacts.xlsx.

Strictly enforces the 6-column schema defined in instructions.txt:
Company | Role | Contact Email | Conversation Log | Last Interaction Date | Status
"""
from __future__ import annotations

import os
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
    STRATEGY_RE = re.compile(r"\[[\d-]+\]\s+Agent\s+\(Strategy:([QOVMU])\):", re.IGNORECASE)
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


if __name__ == "__main__":
    df = load()
    print(f"contacts.xlsx at {EXCEL_PATH}")
    print(f"rows: {len(df)}")
    print(df.head(10).to_string(index=False) if not df.empty else "(empty)")
