"""Gmail IMAP sync — self-contained CLI.

Fetches recent inbox messages, matches them against contacts.xlsx,
updates Conversation Log / Status / Last Interaction Date, and prints
a summary of every matched reply so the calling agent can classify them.

Bounce detection: marks the original contact as Rejected.
Cross-run dedup: skips messages already present in the Conversation Log.
"""
from __future__ import annotations

import argparse
import email
import imaplib
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime

import config
import tracker


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class IncomingReply:
    sender: str
    subject: str
    body: str
    received_at: datetime
    is_bounce: bool = False
    bounced_recipient: str | None = None


# ---------------------------------------------------------------------------
# Bounce detection helpers
# ---------------------------------------------------------------------------

_BOUNCE_SENDERS = (
    "mailer-daemon", "postmaster", "mail-noreply", "noreply-bounce", "bounce-",
    "delivery-status", "mdaemon", "mailerdaemon",
)
_BOUNCE_SUBJECTS = (
    "undeliverable", "delivery status notification", "mail delivery failure",
    "returned mail", "failure notice", "address rejected", "delivery has failed",
    "could not be delivered", "n'a pas pu être remis", "échec de remise",
)


def _looks_like_bounce(sender: str, subject: str) -> bool:
    s, sub = (sender or "").lower(), (subject or "").lower()
    return any(p in s for p in _BOUNCE_SENDERS) or any(p in sub for p in _BOUNCE_SUBJECTS)


def _extract_bounced_recipient(body: str) -> str | None:
    if not body:
        return None
    for pat in [
        r"Final-Recipient\s*:\s*[A-Za-z0-9-]+\s*;\s*([^\s<>]+@[^\s<>]+)",
        r"Original-Recipient\s*:\s*[A-Za-z0-9-]+\s*;\s*([^\s<>]+@[^\s<>]+)",
        r"Your message wasn't delivered to\s+([^\s<>]+@[^\s<>]+)",
        r"The following addresses had permanent fatal errors[^\n]*\n[^<]*<([^>]+)>",
        r"to\s+<([^>]+@[^>]+)>:",
        r"failed permanently for the following recipient[^<]*<?([^\s<>]+@[^\s<>]+)>?",
        r"recipient[s]?[:\s]+<?([^\s<>]+@[^\s<>]+)>?",
    ]:
        m = re.search(pat, body, re.I)
        if m:
            return m.group(1).strip().lower().strip("<>")
    return None


# ---------------------------------------------------------------------------
# Email parsing helpers
# ---------------------------------------------------------------------------

def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_body(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()
            if ctype == "text/plain" and "attachment" not in disp:
                try:
                    return part.get_content().strip()
                except Exception:
                    return (part.get_payload(decode=True) or b"").decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    ).strip()
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    html = part.get_content()
                except Exception:
                    html = (part.get_payload(decode=True) or b"").decode(errors="replace")
                return re.sub(r"<[^>]+>", " ", html).strip()
        return ""
    try:
        return msg.get_content().strip()
    except Exception:
        return (msg.get_payload(decode=True) or b"").decode(
            msg.get_content_charset() or "utf-8", errors="replace"
        ).strip()


def _strip_quoted(body: str) -> str:
    lines = body.splitlines()
    cut = len(lines)
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith(">"):
            cut = min(cut, i)
            break
        if re.match(r"^(On .+ wrote:|Le .+ a écrit ?:|De ?:|From ?:)", s):
            cut = min(cut, i)
            break
    return "\n".join(lines[:cut]).strip()


def _norm_text(s: str) -> str:
    """Collapse all runs of whitespace (incl. newlines) to single spaces and strip.

    Cross-run dedup MUST normalize the same way ``tracker.append_interaction`` stores
    log text (it does ``.replace("\\n"," ")``), otherwise a fresh snippet that still
    contains newlines will never substring-match the space-normalized stored log and
    the same message gets appended on every sync.
    """
    return re.sub(r"\s+", " ", (s or "")).strip()


def _match_row(sender: str, known_emails: set[str], known_domains: set[str]) -> str | None:
    s = sender.lower().strip()
    if not s:
        return None
    if s in known_emails:
        return s
    if "@" in s:
        domain = s.split("@", 1)[1]
        for known in known_emails:
            if "@" in known and known.split("@", 1)[1] == domain:
                return known
        if domain in known_domains:
            for known in known_emails:
                if "@" in known and known.split("@", 1)[1] == domain:
                    return known
    return None


# ---------------------------------------------------------------------------
# Core IMAP fetch
# ---------------------------------------------------------------------------

def fetch_recent_replies(since_days: int = 7) -> list[IncomingReply]:
    if not config.EMAIL_ADDRESS or not config.EMAIL_APP_PASSWORD:
        raise RuntimeError("EMAIL_ADDRESS / EMAIL_APP_PASSWORD missing from .env")

    since = (date.today() - timedelta(days=since_days)).strftime("%d-%b-%Y")
    out: list[IncomingReply] = []
    imap = imaplib.IMAP4_SSL(config.IMAP_SERVER, config.IMAP_PORT)
    try:
        imap.socket().settimeout(30)  # prevent indefinite hang on slow/unresponsive server
    except Exception:
        pass
    try:
        imap.login(config.EMAIL_ADDRESS, config.EMAIL_APP_PASSWORD)
        imap.select("INBOX", readonly=True)
        typ, data = imap.search(None, f'(SINCE "{since}")')
        if typ != "OK" or not data or not data[0]:
            return out
        for num in data[0].split():
            typ, raw = imap.fetch(num, "(RFC822)")
            if typ != "OK" or not raw or not raw[0]:
                continue
            msg = email.message_from_bytes(raw[0][1])
            _, sender = parseaddr(_decode(msg.get("From")))
            subject = _decode(msg.get("Subject"))
            date_hdr = msg.get("Date")
            try:
                received = parsedate_to_datetime(date_hdr) if date_hdr else datetime.now()
            except Exception:
                received = datetime.now()
            raw_body = _extract_body(msg)
            is_bounce = _looks_like_bounce(sender, subject)
            bounced = _extract_bounced_recipient(raw_body) if is_bounce else None
            body = raw_body if is_bounce else _strip_quoted(raw_body)
            out.append(IncomingReply(
                sender=sender.lower().strip(),
                subject=subject.strip(),
                body=body,
                received_at=received,
                is_bounce=is_bounce,
                bounced_recipient=bounced,
            ))
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return out


# ---------------------------------------------------------------------------
# Sync (fetch + apply to tracker)
# ---------------------------------------------------------------------------

def sync(since_days: int = 7) -> tuple[int, int, int]:
    """Fetch inbox and apply to contacts.xlsx. Returns (matched, applied, bounces)."""
    df = tracker.load()
    if df.empty:
        print("[inbox] tracker is empty — nothing to match against")
        return 0, 0, 0

    known_emails = {
        e for e in df["Contact Email"].fillna("").astype(str).map(tracker._norm_email).tolist() if e
    }
    known_domains = {e.split("@", 1)[1] for e in known_emails if "@" in e}

    replies = fetch_recent_replies(since_days=since_days)
    print(f"[inbox] fetched {len(replies)} message(s); matching against {len(known_emails)} contacts")

    matched = applied = bounces = 0
    seen: set[tuple[str, str]] = set()

    for r in replies:
        if r.is_bounce:
            target = _match_row(r.bounced_recipient or "", known_emails, known_domains) if r.bounced_recipient else None
            if not target:
                continue
            matched += 1
            key = ("bounce", target)
            if key in seen:
                continue
            seen.add(key)
            snippet = _norm_text(r.body or "")[:300]
            # Cross-run dedup: a bounce re-fetched on a later sync must not be re-logged.
            mask = df["Contact Email"].fillna("").astype(str).map(tracker._norm_email) == target
            existing_log = _norm_text(str(df.loc[mask, "Conversation Log"].iloc[0] or "")) if mask.any() else ""
            bounce_frag = _norm_text(f"BOUNCED | {r.subject}")[:60]
            if bounce_frag and bounce_frag in existing_log:
                continue
            if tracker.append_interaction(
                contact_email=target,
                direction="Contact",
                message=f"BOUNCED | {r.subject} | {snippet}",
                status="Rejected",
                when=r.received_at.date(),
            ):
                bounces += 1
                print(f"[inbox]   BOUNCE {target} -> Rejected")
            continue

        if not r.body:
            continue
        target = _match_row(r.sender, known_emails, known_domains)
        if not target:
            continue
        matched += 1
        snippet = r.body[:600]
        key = (target, _norm_text(snippet)[:80])
        if key in seen:
            continue
        seen.add(key)

        # Cross-run dedup — normalize both sides so newline differences don't defeat the
        # substring match (tracker stores the log with newlines collapsed to spaces).
        mask = df["Contact Email"].fillna("").astype(str).map(tracker._norm_email) == target
        existing_log = _norm_text(str(df.loc[mask, "Conversation Log"].iloc[0] or "")) if mask.any() else ""
        subj_frag = _norm_text(r.subject)[:60]
        body_frag = _norm_text(snippet)[:60]
        if subj_frag and subj_frag in existing_log and body_frag and body_frag in existing_log:
            continue

        if tracker.append_interaction(
            contact_email=target,
            direction="Contact",
            message=f"{r.subject} | {snippet}",
            status="Replied",
            when=r.received_at.date(),
        ):
            applied += 1
            print(f"[inbox]   REPLY  from={r.sender}  matched={target}")
            print(f"[inbox]          subject: {r.subject[:70]}")
            print(f"[inbox]          body:    {r.body[:200].replace(chr(10), ' ')}")

    print(f"[inbox] done: matched={matched} replies_applied={applied} bounces={bounces}")
    return matched, applied, bounces


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Gmail inbox → contacts.xlsx")
    parser.add_argument("--since-days", type=int, default=7,
                        help="Days back to scan IMAP (default: 7)")
    args = parser.parse_args()
    try:
        sync(since_days=args.since_days)
    except Exception as e:
        print(f"[inbox] ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
