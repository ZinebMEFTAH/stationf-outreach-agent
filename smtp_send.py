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


# Distinctive function words per language. Word-boundary matching (tokenized) —
# NOT substring — so English words like "schema"/"common"/"pour" can't false-match.
_FR_WORDS = {
    "je", "j'ai", "vous", "votre", "vos", "nous", "notre", "nos", "mon", "ma", "mes",
    "le", "la", "les", "un", "une", "des", "du", "de", "et", "ou", "avec", "pour",
    "dans", "chez", "cette", "ce", "ces", "que", "qui", "est", "sont", "vos",
    "merci", "bonjour", "alternance", "entreprise", "poste", "à",
}
_EN_WORDS = {
    "i", "i'm", "you", "your", "we", "our", "the", "a", "an", "and", "or", "with",
    "for", "in", "at", "on", "this", "that", "is", "are", "to", "of", "my",
    "thanks", "hello", "looking", "build", "built", "would", "role", "team",
}

_TOKEN_RE = None


def _detect_lang(body: str) -> str:
    """Detect whether the email body is French (fr) or English (en).

    Tokenizes on word boundaries and compares counts of distinctive French vs
    English function words. Ties → French (most targets are French companies).
    """
    global _TOKEN_RE
    if _TOKEN_RE is None:
        import re
        _TOKEN_RE = re.compile(r"[a-zà-ÿ']+")
    tokens = _TOKEN_RE.findall((body or "").lower())
    fr = sum(1 for t in tokens if t in _FR_WORDS)
    en = sum(1 for t in tokens if t in _EN_WORDS)
    return "fr" if fr >= en else "en"


def _full_body(body: str, add_footer: bool = True, footer_seed: str | None = None) -> str:
    """Body + 'Zineb Meftah' signature, plus the P.S. disclosure footer if requested.

    The footer is the AI-agent disclosure. It belongs on COLD (first-contact) emails
    where it's a differentiator. On follow-ups it's redundant bulk; on replies it
    undermines the now-human conversation — so those carry the signature only.
    The footer variant rotates (config.pick_footer) so it isn't byte-identical on every
    cold email; `footer_seed` (the recipient) keeps it stable per person.
    """
    out = f"{(body or '').rstrip()}\n\nZineb Meftah"
    if add_footer:
        footer = config.pick_footer(_detect_lang(body), seed=footer_seed)
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
        content = _full_body(body, add_footer=add_footer, footer_seed=to_address)
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


# Generic role inboxes (contact@, jobs@, …). These are MORE likely than a guessed personal
# mailbox to exist — but they are NOT guaranteed. The 2026-08 audit found 55 bounces, all of
# them generic locals accepted on `mx_only` alone: contact@edusign.fr, contact@kipsum.fr,
# contact@coachello.ai, contact@rossinienergy.com, contact@adlive.io … every one "Address not
# found". A live domain with MX records says nothing about whether contact@ is provisioned.
_GENERIC_LOCALS = {
    "contact", "hello", "info", "team", "jobs", "job", "career", "careers", "recrutement",
    "recrute", "recrut", "rh", "hr", "bonjour", "hi", "sales", "press", "contactez", "talent",
    "hiring", "join", "work", "apply",
}
# Verification tiers that CONFIRM the specific mailbox exists (vs. merely "domain is alive").
_STRONG_CONF = {"smtp_ok", "api_valid"}
# Tiers a GENERIC inbox may additionally be sent on. `api_risky` is the verification API saying
# "catch-all / accept-all": such a server accepts every local-part, so it cannot hard-bounce —
# that is positive evidence, unlike `mx_only`.
#
# `mx_only` means "we did not check the mailbox at all" (port 25 is blocked on the VM, so every
# address degrades to this once the Hunter quota is spent). It is the absence of evidence, and
# treating it as permission is precisely what produced the August bounce spike: 0.9% in July
# while Hunter had quota, 7.2% in August after it ran out. Sending on `mx_only` is therefore
# refused for generic inboxes too — the caller should fall back to LinkedIn, or wait for quota.
_GENERIC_OK_CONF = _STRONG_CONF | {"api_risky"}


def send_and_log(*, to_address: str, subject: str, body: str,
                 attachment_path: Path | None, new_status: str | None,
                 kind: str = "cold", dry_run: bool = True,
                 company: str | None = None,
                 role: str | None = None,
                 strategy: str | None = None) -> SendResult:
    is_alert = kind == "alert"

    # ── Pre-send verification gate ───────────────────────────────────────────
    # Refuse to send to an address that's definitively bad (dead domain, hard 5xx,
    # or API says undeliverable). This stops bounces BEFORE they happen. Alerts go
    # to a known internal address, so they skip verification.
    if not dry_run and not is_alert:
        from email.utils import parseaddr
        from email_verify import verify as _verify
        _, addr = parseaddr(to_address)

        # ── Known-bad blocklist (cheapest check, so it runs first) ───────────
        # An address that hard-bounced once will hard-bounce again; re-sending only
        # damages sender reputation. Costs no network call and no Hunter quota.
        try:
            import bounce_guard
            blocked, why_blocked = bounce_guard.is_blocked(addr or to_address)
            if blocked:
                return SendResult(ok=False, error=(
                    f"recipient is on the hard-bounce blocklist: {why_blocked}. "
                    f"Find a different address (or reach them via LinkedIn) — do not retry this one."))
        except Exception:
            pass  # a bookkeeping failure must never block outreach

        reachable, conf, why = _verify(addr or to_address)
        if not reachable:
            return SendResult(ok=False, error=f"recipient failed verification [{conf}]: {why}")

        # ── Mailbox-evidence gate ────────────────────────────────────────────
        # Bounces damage sender reputation for ALL future mail, so an address is only sent to on
        # positive evidence — the bar just differs by address type.
        #
        # PERSONAL (firstname.lastname@): needs the mailbox itself confirmed (smtp_ok / api_valid).
        #   Knowing only that the domain is alive is not enough, because the local-part is a GUESS
        #   and a wrong guess bounces. This was the #1 bounce source (30/35).
        # GENERIC (contact@, jobs@): a lower bar — api_risky (catch-all) also passes, since a
        #   catch-all server accepts every local-part and therefore cannot hard-bounce. But NOT
        #   `mx_only`, which proves only that the domain resolves. Treating that as permission is
        #   what produced the August 2026 spike: 55 bounces, every one a generic inbox on mx_only.
        #
        # Either way the refusal message is actionable, so the caller can fall back to the generic
        # inbox (personal case), or skip the company and use the LinkedIn double-tap (generic case).
        local = (addr or to_address).split("@", 1)[0].strip().lower()
        is_generic = local in _GENERIC_LOCALS
        allowed = _GENERIC_OK_CONF if is_generic else _STRONG_CONF
        if kind in ("cold", "followup") and conf not in allowed:
            if is_generic:
                return SendResult(ok=False, error=(
                    f"unverified generic inbox [{conf}] for {addr} — not sent (would risk a bounce). "
                    f"`mx_only` only proves the DOMAIN is alive, not that this mailbox exists; the "
                    f"August 2026 bounce spike was 55 generic inboxes accepted on exactly this signal. "
                    f"Reach the company via the LinkedIn double-tap, or retry once Hunter quota resets."))
            return SendResult(ok=False, error=(
                f"unconfirmed personal mailbox [{conf}] for {addr} — not sent (would risk a bounce). "
                f"Use the company's generic inbox (contact@<real-domain>) and reach the person via the "
                f"LinkedIn double-tap instead."))

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
            strategy=strategy,   # records "Agent (Strategy:X):" automatically — no hand-formatting
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
    parser.add_argument("--strategy", default=None,
                        help="Cold-email strategy letter (Q/O/V/M/U/A/G) — recorded as "
                             "'Agent (Strategy:X):' so the bandit remembers what was tried")
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
        strategy=args.strategy,
    )

    if result.ok:
        print(f"[smtp] {'sent' if args.send else 'dry-run OK'} -> {args.to} | {args.subject}")
        return 0
    print(f"[smtp] FAILED: {result.error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
