"""SMTP send CLI — self-contained.

Sends one email via Gmail SMTP+STARTTLS, adds the right trailer for the kind,
optionally attaches a CV PDF, and logs the interaction to contacts.xlsx.

Trailer by --kind:
  cold            → "Zineb Meftah" signature + P.S. AI-disclosure footer (FR/EN auto)
  followup/reply  → signature only (footer would be redundant / undermine warmth)
  alert           → raw internal notification: no signature, no footer, not logged,
                    not counted against the daily send caps

Usage:
  python smtp_send.py \\
    --to "Name <email@domain.com>" \\
    --subject "Subject line" \\
    --body-file drafts/2026-05-27/01-cold-company.txt \\
    --company "Company Name" --role "AI Engineer" --kind cold \\
    [--attach documents/CV_Zineb_Meftah_FR.pdf] \\
    [--send]

Defaults to dry-run (pass --send to actually transmit).
"""
from __future__ import annotations

import argparse
import smtplib
import ssl
import sys
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

import json
from datetime import date

import config
import tracker


# ---------------------------------------------------------------------------
# Daily send counter — authoritative source for COLD_CAP / WARM_CAP tracking
# ---------------------------------------------------------------------------

def _counts_path() -> "Path":
    from pathlib import Path
    p = Path(__file__).parent / "cache" / f"daily_counts_{date.today().isoformat()}.json"
    p.parent.mkdir(exist_ok=True)
    return p


def today_send_counts() -> dict[str, int]:
    """Return {'cold': N, 'warm': N} sends completed today."""
    p = _counts_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"cold": 0, "warm": 0}


def _record_send(kind: str) -> None:
    """Increment today's cold or warm counter after a successful real send."""
    p = _counts_path()
    counts = today_send_counts()
    bucket = "cold" if kind == "cold" else "warm"
    counts[bucket] = counts.get(bucket, 0) + 1
    p.write_text(json.dumps(counts))


# ---------------------------------------------------------------------------
# Build & send
# ---------------------------------------------------------------------------

@dataclass
class SendResult:
    ok: bool
    error: str | None = None


_FRENCH_MARKERS = [
    "je ", "vous ", "nous ", "votre ", "mon ", "ma ", " le ", " la ", " les ",
    " un ", " une ", "bonjour", "merci", "chez ", "cette ", "pour ", "dans ",
]


def _detect_lang(body: str) -> str:
    """Detect whether the email body is French (fr) or English (en)."""
    b = (body or "").lower()
    score = sum(1 for m in _FRENCH_MARKERS if m in b)
    return "fr" if score >= 3 else "en"


def _full_body(body: str, add_footer: bool = True) -> str:
    """Body + 'Zineb Meftah' signature, plus the P.S. disclosure footer if requested.

    The footer is the AI-agent disclosure. It belongs on COLD (first-contact) emails
    where it's a differentiator. On follow-ups it's redundant bulk; on replies it
    undermines the now-human conversation — so those carry the signature only.
    """
    out = f"{(body or '').rstrip()}\n\nZineb Meftah"
    if add_footer:
        footer = config.FOOTER_FR if _detect_lang(body) == "fr" else config.FOOTER_EN
        out += f"\n\n{footer}"
    return out + "\n"


def _build_message(*, to_address: str, subject: str, body: str,
                   attachment_path: Path | None,
                   add_signature: bool = True, add_footer: bool = True) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((config.FROM_NAME, config.EMAIL_ADDRESS))
    msg["To"] = to_address
    msg["Subject"] = subject
    # Three modes:
    #   cold            → signature + P.S. footer
    #   followup/reply  → signature only (footer would be redundant / undermine warmth)
    #   alert           → raw (no signature, no footer)
    if add_signature:
        content = _full_body(body, add_footer=add_footer)
    else:
        content = (body or "").rstrip() + "\n"
    msg.set_content(content, charset="utf-8")
    if attachment_path and attachment_path.exists():
        data = attachment_path.read_bytes()
        msg.add_attachment(data, maintype="application", subtype="pdf",
                           filename=attachment_path.name)
    return msg


def send(*, to_address: str, subject: str, body: str,
         attachment_path: Path | None = None, dry_run: bool = True,
         add_signature: bool = True, add_footer: bool = True) -> SendResult:
    if dry_run:
        return SendResult(ok=True)
    if not config.EMAIL_ADDRESS or not config.EMAIL_APP_PASSWORD:
        return SendResult(ok=False, error="EMAIL_ADDRESS / EMAIL_APP_PASSWORD missing")
    msg = _build_message(to_address=to_address, subject=subject, body=body,
                         attachment_path=attachment_path,
                         add_signature=add_signature, add_footer=add_footer)
    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo(); smtp.starttls(context=ctx); smtp.ehlo()
            smtp.login(config.EMAIL_ADDRESS, config.EMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        return SendResult(ok=True)
    except Exception as e:
        return SendResult(ok=False, error=f"{type(e).__name__}: {e}")


def send_and_log(*, to_address: str, subject: str, body: str,
                 attachment_path: Path | None, new_status: str | None,
                 kind: str = "cold", dry_run: bool = True,
                 company: str | None = None,
                 role: str | None = None) -> SendResult:
    is_alert = kind == "alert"
    # Footer (AI disclosure) only on cold first-contact. Follow-ups/replies carry
    # the signature but no footer. Alerts are raw.
    result = send(to_address=to_address, subject=subject, body=body,
                  attachment_path=attachment_path, dry_run=dry_run,
                  add_signature=not is_alert,
                  add_footer=(kind == "cold"))
    if not result.ok or dry_run:
        return result
    # Alerts are internal notifications: never logged to the tracker, never
    # counted against the daily send caps.
    if not is_alert:
        tracker.append_interaction(
            contact_email=to_address,
            direction="Agent",
            message=subject.strip(),
            status=new_status,
            when=date.today(),
            company=company,
            role=role,
        )
        _record_send(kind)
    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

_KIND_STATUS = {
    "cold": "Emailed", "followup": "Followed Up", "reply": "Followed Up",
    "alert": None,  # internal notification — no tracker status change
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Send one outreach email")
    parser.add_argument("--to", required=True)
    parser.add_argument("--subject", required=True)
    body_grp = parser.add_mutually_exclusive_group(required=True)
    body_grp.add_argument("--body-file", help="Path to file containing the email body")
    body_grp.add_argument("--body", help="Email body as inline text")
    parser.add_argument("--attach", help="PDF to attach (optional)")
    parser.add_argument("--company", default=None)
    parser.add_argument("--role", default=None)
    parser.add_argument("--kind", default="cold",
                        choices=["cold", "followup", "reply", "alert"],
                        help="alert = internal notification (no footer, not logged, not counted)")
    parser.add_argument("--send", action="store_true",
                        help="Actually transmit. Omit for dry-run.")
    args = parser.parse_args()

    body = Path(args.body_file).read_text(encoding="utf-8").strip() if args.body_file else (args.body or "").strip()
    attach = Path(args.attach) if args.attach else None

    result = send_and_log(
        to_address=args.to,
        subject=args.subject,
        body=body,
        attachment_path=attach,
        new_status=_KIND_STATUS[args.kind],
        kind=args.kind,
        dry_run=not args.send,
        company=args.company,
        role=args.role,
    )

    if result.ok:
        print(f"[smtp] {'sent' if args.send else 'dry-run OK'} -> {args.to} | {args.subject}")
        return 0
    print(f"[smtp] FAILED: {result.error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
