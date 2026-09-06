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
import time
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
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
    # Set when the mail WAS delivered but something after it failed (the tracker write,
    # the counter). The caller must treat this as success — retrying would re-send to a
    # real person — while still surfacing that the record is incomplete.
    warning: str | None = None
    message_id: str | None = None


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
                   add_signature: bool = True, add_footer: bool = True,
                   headers: dict[str, str] | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((config.FROM_NAME, config.EMAIL_ADDRESS))
    msg["To"] = to_address
    msg["Subject"] = subject
    # Set Date and Message-ID ourselves instead of letting the relay fill them in. Date is
    # required by RFC 5322 and its absence is a spam signal; the Message-ID has to be OURS
    # because the next follow-up quotes it back in In-Reply-To to stay in the same thread.
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=(config.EMAIL_ADDRESS.split("@")[-1] or None))
    # Replies land in the sending mailbox by default; set it explicitly so a future change
    # to FROM_NAME/EMAIL_ADDRESS cannot silently orphan someone's reply.
    msg["Reply-To"] = formataddr((config.FROM_NAME, config.EMAIL_ADDRESS))
    for k, v in (headers or {}).items():
        if v:
            msg[k] = v
    # Three modes:
    #   cold            → signature + P.S. footer
    #   followup/reply  → signature only (footer would be redundant / undermine warmth)
    #   alert           → raw (no signature, no footer)
    if add_signature:
        content = _full_body(body, add_footer=add_footer, footer_seed=to_address)
    else:
        content = (body or "").rstrip() + "\n"
    msg.set_content(content, charset="utf-8")
    if attachment_path is not None:
        # A missing file used to be skipped silently, so a follow-up whose body says
        # "CV en pièce jointe" went out with no CV and still reported success. If an
        # attachment was asked for and cannot be attached, that is a failed send.
        if not attachment_path.exists():
            raise FileNotFoundError(f"attachment not found: {attachment_path}")
        data = attachment_path.read_bytes()
        if not data:
            raise ValueError(f"attachment is empty: {attachment_path}")
        subtype = attachment_path.suffix.lstrip(".").lower() or "octet-stream"
        maintype = "application"
        if subtype not in ("pdf", "zip", "json"):
            maintype, subtype = "application", "octet-stream"
        msg.add_attachment(data, maintype=maintype, subtype=subtype,
                           filename=attachment_path.name)
    return msg


# Transient failures of the CONNECT/LOGIN phase only. Retrying these is safe because no
# message data has been transmitted yet. Anything raised by send_message() is never retried:
# the server may already have accepted the mail, and a duplicate to a real decision-maker is
# far worse than a send we skip until tomorrow.
_TRANSIENT_CONNECT = (
    smtplib.SMTPConnectError, smtplib.SMTPHeloError, smtplib.SMTPServerDisconnected,
    ConnectionError, TimeoutError, OSError,
)
CONNECT_ATTEMPTS = 3


def send(*, to_address: str, subject: str, body: str,
         attachment_path: Path | None = None, dry_run: bool = True,
         add_signature: bool = True, add_footer: bool = True,
         headers: dict[str, str] | None = None) -> SendResult:
    if dry_run:
        return SendResult(ok=True)
    if not config.EMAIL_ADDRESS or not config.EMAIL_APP_PASSWORD:
        return SendResult(ok=False, error="EMAIL_ADDRESS / EMAIL_APP_PASSWORD missing")
    try:
        msg = _build_message(to_address=to_address, subject=subject, body=body,
                             attachment_path=attachment_path,
                             add_signature=add_signature, add_footer=add_footer,
                             headers=headers)
    except Exception as e:
        return SendResult(ok=False, error=f"could not build the message: {type(e).__name__}: {e}")
    ctx = ssl.create_default_context()
    last: Exception | None = None
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT, timeout=30) as smtp:
                smtp.ehlo(); smtp.starttls(context=ctx); smtp.ehlo()
                smtp.login(config.EMAIL_ADDRESS, config.EMAIL_APP_PASSWORD)
                # Past this point a retry could duplicate the mail — never wrap it in the loop.
                try:
                    smtp.send_message(msg)
                except Exception as e:
                    return SendResult(ok=False, error=f"{type(e).__name__}: {e}")
            return SendResult(ok=True, message_id=msg.get("Message-ID"))
        except smtplib.SMTPAuthenticationError as e:
            # A revoked Gmail app password caused a 28-day silent outage in 2026-06.
            # Never retry it, and say plainly what to do.
            return SendResult(ok=False, error=(
                f"SMTP authentication refused ({e}). The Gmail app password is revoked or "
                f"wrong — regenerate it and update EMAIL_APP_PASSWORD in .env on the sending "
                f"host. Retrying will not help."))
        except _TRANSIENT_CONNECT as e:
            last = e
            if attempt < CONNECT_ATTEMPTS:
                time.sleep(2 ** attempt)  # 2s, 4s
                continue
        except Exception as e:
            return SendResult(ok=False, error=f"{type(e).__name__}: {e}")
    return SendResult(ok=False, error=(
        f"could not reach {config.SMTP_SERVER}:{config.SMTP_PORT} after {CONNECT_ATTEMPTS} "
        f"attempts — {type(last).__name__}: {last}"))


# Generic role inboxes (contact@, jobs@, …). These are MORE likely than a guessed personal
# mailbox to exist — but they are NOT guaranteed. The 2026-08 audit found 55 bounces, all of
# them generic locals accepted on `mx_only` alone — a `contact@` at a company whose domain
# resolved perfectly, every one answered "Address not found". A live domain with MX records
# says nothing about whether contact@ is actually provisioned. (The specific companies are in
# the private CLAUDE.md; naming third parties' broken mail setups in a public repo is not our
# business.)
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


def cap_check(kind: str) -> tuple[bool, str]:
    """(allowed, reason) for one more send of this kind TODAY. The hard stop.

    Until now the caps existed only in the skill prompt — `_record_send` counted sends but
    nothing ever read the count back, so the entire anti-spam ceiling rested on an LLM
    remembering to stop. A miscount, a re-run, or a cron double-fire could put hundreds of
    messages through a personal Gmail and burn the sending reputation the whole pipeline
    depends on. This makes the ceiling real.

    `alert` is exempt (internal notification, never counted). `reply` is exempt from the
    bucket cap but still counted: it is human-approved content in a live conversation, and
    blocking an answer to someone who is actually talking to Zineb would be worse than the
    spam risk it avoids. Cold and follow-up — the agent-initiated volume — are hard-capped.
    """
    if kind == "alert":
        return True, "alert — never counted"
    counts = today_send_counts()
    cold, warm = int(counts.get("cold", 0)), int(counts.get("warm", 0))
    if cold + warm >= config.DAILY_CAP and kind != "reply":
        return False, (f"daily cap reached: {cold} cold + {warm} warm = {cold + warm}/"
                       f"{config.DAILY_CAP} sent today")
    if kind == "cold":
        cap = config.effective_cold_cap()
        if cold >= cap:
            return False, (f"cold cap reached: {cold}/{cap} sent today"
                           + (f" (warm-up ramp; ceiling is {config.COLD_CAP})"
                              if cap < config.COLD_CAP else ""))
    elif kind == "followup":
        if warm >= config.WARM_CAP:
            return False, f"follow-up cap reached: {warm}/{config.WARM_CAP} sent today"
    return True, "within today's caps"


def send_and_log(*, to_address: str, subject: str, body: str,
                 attachment_path: Path | None, new_status: str | None,
                 kind: str = "cold", dry_run: bool = True,
                 company: str | None = None,
                 role: str | None = None,
                 strategy: str | None = None,
                 force: bool = False) -> SendResult:
    is_alert = kind == "alert"

    # ── Checks that hold for a DRY RUN too ───────────────────────────────────
    # These are about the CONTENT, so a dry run must surface them: finding out at send time
    # that the draft is unsendable wastes the whole research pass. Everything below runs
    # before the transport, and before the dry-run short-circuit inside send().
    from email.utils import parseaddr as _parseaddr
    _to = _parseaddr(to_address)[1].strip().lower()
    if "@" not in _to:
        return SendResult(ok=False, error=f"not a valid recipient address: {to_address!r}")

    if is_alert:
        # An alert bypasses EVERY outreach safety gate — verification, the bounce blocklist,
        # the daily cap, the duplicate guard, tracker logging, and the AI-disclosure footer.
        # That is right for Zineb's own inbox and catastrophic for anyone else, so `alert` is
        # not a label that can be pointed at a third party.
        if _to not in config.INTERNAL_RECIPIENTS:
            return SendResult(ok=False, error=(
                f"--kind alert may only be sent to an internal address ({_to} is not one of "
                f"{sorted(config.INTERNAL_RECIPIENTS)}). Alerts skip verification, the bounce "
                f"blocklist, the daily cap and tracker logging — use cold/followup/reply to "
                f"write to a company."))
    else:
        if not (subject or "").strip():
            return SendResult(ok=False, error="empty subject — an outreach email needs one")
        if not (body or "").strip():
            return SendResult(ok=False, error=(
                "empty body — nothing but the signature and footer would be sent. Usually a "
                "missing or empty --body-file."))
        # Cold emails carry NO attachment. An unsolicited first contact with a PDF is a
        # textbook spam-filter trigger, and CLAUDE.md has said so all along — but nothing
        # enforced it, exactly like the daily cap. The CV goes on follow-ups and replies,
        # where the conversation already exists; on cold, the LinkedIn URL goes inline.
        if kind == "cold" and attachment_path is not None:
            return SendResult(ok=False, error=(
                f"cold emails must not carry an attachment ({attachment_path.name}) — it is a "
                f"spam-filter trigger on unsolicited first contact. Put the LinkedIn URL in the "
                f"body instead; attach the CV on the follow-up."))
        # A missing attachment IS caught on a real send — _build_message raises and the send
        # fails — but that check lives past the dry-run early return, so `--dry-run` reported
        # "OK" for a file that does not exist. A preview that is more permissive than the real
        # send is worse than no preview: /daily-agent --dry-run is exactly how a day's follow-ups
        # are reviewed, and a follow-up whose body promises "CV en pièce jointe" would pass the
        # review and then fail at send time with an opaque "could not build the message". Checked
        # here, with the other pre-transport refusals, so the preview tells the truth.
        if attachment_path is not None:
            if not attachment_path.exists():
                return SendResult(ok=False, error=(
                    f"attachment not found: {attachment_path} — the body promises a document that "
                    f"would not be there. Build it first (python cv_builder.py ...)."))
            if attachment_path.stat().st_size == 0:
                return SendResult(ok=False, error=f"attachment is empty: {attachment_path}")

        # The quality linter was also documented as mandatory ("MUST pass before any send")
        # and also enforced only by the prompt. Hard ERRORS block; warnings are advisory and
        # are surfaced but never block.
        try:
            import email_lint
            errors, _warn = email_lint.lint(body, subject=subject, kind=kind,
                                            company=company or "")
        except Exception:
            errors = []  # a linter crash must never block outreach
        if errors and not force:
            return SendResult(ok=False, error=(
                "draft failed the quality linter — do NOT send it as is:\n  - "
                + "\n  - ".join(errors)))

    # ── Pre-send verification gate ───────────────────────────────────────────
    # Refuse to send to an address that's definitively bad (dead domain, hard 5xx,
    # or API says undeliverable). This stops bounces BEFORE they happen. Alerts go
    # to a known internal address, so they skip verification.
    if not dry_run and not is_alert:
        from email.utils import parseaddr
        from email_verify import verify as _verify
        _, addr = parseaddr(to_address)

        # ── Daily cap (cheapest check of all — no network, no file scan) ────
        allowed, why_cap = cap_check(kind)
        if not allowed and not force:
            return SendResult(ok=False, error=(
                f"refused by the daily send cap — {why_cap}. This is the anti-spam ceiling "
                f"that protects the sending reputation; it resets at midnight. Queue the "
                f"lead for tomorrow rather than forcing it."))

        # ── Duplicate guard ─────────────────────────────────────────────────
        # Nothing else stops the identical email going out twice: a re-run of the skill, a
        # cron double-fire, or a retry after the post-send bookkeeping failed. Sending a
        # decision-maker the same message twice is worse than not sending it at all.
        try:
            import mail_thread
            when = mail_thread.recent_duplicate(addr or to_address, subject, body)
            if when is not None and not force:
                import time as _t
                return SendResult(ok=False, error=(
                    f"duplicate: this exact message (same subject AND same body) already "
                    f"went to {addr} on {_t.strftime('%Y-%m-%d', _t.localtime(when))}. "
                    f"Sending it again would read as a mass mailing. A follow-up must say "
                    f"something new — rewrite it, or skip the lead."))
        except Exception:
            pass  # the guard is best-effort; never block a legitimate send on a cache error

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
        # PRIOR DELIVERY beats every verifier. A follow-up goes to someone we already
        # emailed successfully: the mail was accepted, and a bounce would have flipped the
        # row to `Rejected` and blocklisted the address (checked above). Re-verifying that
        # mailbox adds nothing, and refusing it when the verifier happens to be down costs
        # the highest-converting channel for zero deliverability gain — which is exactly
        # what happened on 2026-09-02: Hunter was unreachable, so three follow-ups to
        # already-delivered mailboxes were drafted and then refused.
        already_delivered = False
        if kind == "followup":
            try:
                already_delivered = tracker.address_has_delivered_mail(addr or to_address)
            except Exception:
                already_delivered = False  # unreadable tracker → fall back to the normal bar

        local = (addr or to_address).split("@", 1)[0].strip().lower()
        is_generic = local in _GENERIC_LOCALS
        allowed = _GENERIC_OK_CONF if is_generic else _STRONG_CONF
        if kind in ("cold", "followup") and conf not in allowed and not already_delivered:
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
    # Thread the message into the existing conversation. Mail clients thread on these
    # headers, not on a "Re:" prefix — without them a follow-up arrives as a context-free
    # message from a stranger, which is the shape of bulk mail.
    thread_headers: dict[str, str] = {}
    if kind in ("followup", "reply") and not is_alert:
        try:
            import mail_thread
            thread_headers = mail_thread.reply_headers(to_address, company, role)
        except Exception:
            thread_headers = {}

    result = send(to_address=to_address, subject=subject, body=body,
                  attachment_path=attachment_path, dry_run=dry_run,
                  add_signature=not is_alert,
                  add_footer=(kind == "cold"),
                  headers=thread_headers)
    if not result.ok or dry_run:
        return result
    # Alerts are internal notifications: never logged to the tracker, never
    # counted against the daily send caps.
    #
    # EVERYTHING BELOW THIS LINE RUNS AFTER THE MAIL IS ALREADY DELIVERED.
    # It must never turn a delivered message into a reported failure: the caller would
    # retry, and the recipient would get it twice. Each step is contained, and anything
    # that goes wrong is reported as a WARNING on a successful result.
    #
    warnings: list[str] = []
    if not is_alert:
        try:
            logged = tracker.append_interaction(
                contact_email=to_address,
                direction="Agent",
                message=subject.strip(),
                status=new_status,
                when=date.today(),
                company=company,
                role=role,
                strategy=strategy,   # records "Agent (Strategy:X):" — no hand-formatting
            )
            if not logged:
                warnings.append(
                    f"DELIVERED but NOT logged: no row matched company={company!r} "
                    f"role={role!r} email={to_address!r}. The send is invisible to the "
                    f"tracker, so follow-up timing and the strategy bandit will both miss "
                    f"it. Add the row or fix the company/role before re-running.")
        except Exception as e:
            warnings.append(f"DELIVERED but the tracker write failed ({type(e).__name__}: "
                            f"{e}). Do NOT re-send; fix contacts.xlsx and log it by hand.")
        try:
            _record_send(kind)
        except Exception as e:
            warnings.append(f"DELIVERED but the daily counter was not incremented "
                            f"({type(e).__name__}: {e}) — today's cap is now understated.")
    # Remember the Message-ID so the next touch threads onto it, and the subject so the
    # duplicate guard can recognise it. Alerts are excluded: they are internal noise.
    if not is_alert and result.message_id:
        try:
            import mail_thread
            mail_thread.record(to_address, result.message_id, subject, company, role, body)
        except Exception as e:
            warnings.append(f"DELIVERED but the thread record failed ({type(e).__name__}: "
                            f"{e}) — the next follow-up may not thread.")
    if warnings:
        result.warning = " | ".join(warnings)
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
    parser.add_argument("--force", action="store_true",
                        help="Override the daily cap and the duplicate guard. HUMAN USE ONLY "
                             "— for a deliberate one-off. The agent must never pass this: the "
                             "caps are what protect the sending reputation.")
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
        force=args.force,
    )

    if result.ok:
        print(f"[smtp] {'sent' if args.send else 'dry-run OK'} -> {args.to} | {args.subject}")
        if result.message_id:
            print(f"[smtp] message-id: {result.message_id}")
        if result.warning:
            # Delivered, but the record is incomplete. Loud, and still exit 0 — a non-zero
            # exit here would read as "not sent" and invite a duplicate.
            print(f"[smtp] ⚠ {result.warning}", file=sys.stderr)
        return 0
    print(f"[smtp] FAILED: {result.error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
