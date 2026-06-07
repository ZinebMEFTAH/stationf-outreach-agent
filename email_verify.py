#!/usr/bin/env python3
"""
Email address reachability check.

Checks whether an email address is likely deliverable using:
  1. MX record lookup  — is the domain wired to receive mail at all?
  2. SMTP RCPT TO probe — does the specific address exist? (best-effort;
     catch-all servers return 250 regardless, but 5xx rejections are definitive)

Usage (CLI):
  python email_verify.py email@domain.com
  → prints result, exits 0 if probably reachable, 1 if definitely not

Usage (module):
  from email_verify import verify, build_patterns

  ok, confidence, reason = verify("alice@acme.io")
  # confidence: "smtp_ok" | "mx_only" | "unverifiable"

  for email in build_patterns("alice", "dupont", "acme.io"):
      ok, conf, reason = verify(email, smtp=True)
      if ok:
          print(f"Use {email} ({conf})")
          break
"""
from __future__ import annotations

import smtplib
import socket
import subprocess
import sys
from unicodedata import normalize, category


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def strip_diacritics(s: str) -> str:
    return "".join(c for c in normalize("NFD", s) if category(c) != "Mn")


def build_patterns(first: str, last: str, domain: str) -> list[str]:
    """
    Return up to 4 common email patterns for a person at a domain.
    Strips diacritics and lowercases automatically.
    """
    f = strip_diacritics(first).lower().strip()
    l = strip_diacritics(last).lower().strip()
    d = domain.lower().strip()
    return [
        f"{f}.{l}@{d}",
        f"{f}@{d}",
        f"{f[0]}.{l}@{d}",
        f"{f}{l}@{d}",
    ]


def _mx_via_dig(domain: str) -> str | None:
    """Return the highest-priority MX hostname, or None."""
    try:
        out = subprocess.check_output(
            ["dig", "+short", "MX", domain],
            timeout=5,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        records = []
        for line in out.strip().splitlines():
            parts = line.split()
            if len(parts) == 2:
                try:
                    records.append((int(parts[0]), parts[1].rstrip(".")))
                except ValueError:
                    pass
        if records:
            return sorted(records)[0][1]
    except Exception:
        pass
    return None


def _mx_fallback(domain: str) -> str | None:
    """Fallback: resolve A record for mail.DOMAIN (common for small providers)."""
    for prefix in ("mail", "smtp"):
        try:
            socket.getaddrinfo(f"{prefix}.{domain}", 25)
            return f"{prefix}.{domain}"
        except OSError:
            pass
    return None


def check_mx(domain: str) -> tuple[bool, str | None]:
    """Return (has_mx, mx_hostname_or_None)."""
    mx = _mx_via_dig(domain)
    if mx:
        return True, mx
    fb = _mx_fallback(domain)
    if fb:
        return True, fb
    # Last resort: check if domain resolves at all
    try:
        socket.getaddrinfo(domain, None)
        return True, None  # domain live but MX unknown
    except OSError:
        return False, None


def _smtp_probe(email: str, mx_host: str, timeout: int = 6) -> tuple[bool | None, str]:
    """
    RCPT TO probe on port 25.
    Returns (True=ok, False=rejected, None=inconclusive), reason_string.
    Inconclusive: connection refused, timeout, TLS required, catch-all 250.
    """
    try:
        with smtplib.SMTP(mx_host, 25, timeout=timeout) as s:
            s.ehlo("verify.check")
            code, _ = s.mail("")
            code, msg = s.rcpt(email)
            if code == 250:
                return True, f"SMTP 250"
            elif code >= 500:
                return False, f"SMTP {code}: {msg.decode(errors='replace')[:80]}"
            else:
                return None, f"SMTP {code} (inconclusive)"
    except smtplib.SMTPConnectError:
        return None, "SMTP port 25 blocked"
    except smtplib.SMTPException as e:
        return None, f"SMTP error: {e}"
    except (OSError, UnicodeEncodeError) as e:
        return None, f"network/encoding: {e}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def verify(email: str, smtp: bool = True) -> tuple[bool, str, str]:
    """
    Returns (reachable, confidence, reason).

    reachable:  True  = send to this address
                False = do NOT send; try another pattern
    confidence: "smtp_ok"       — SMTP returned 250
                "mx_only"       — MX ok, SMTP inconclusive (catch-all or port blocked)
                "unverifiable"  — no MX records; domain probably dead

    Decision rule for callers:
      smtp_ok  → use with confidence
      mx_only  → use cautiously (will bounce if guessed wrong, but domain is alive)
      unverifiable → skip / fall back to contact@domain
    """
    if "@" not in email:
        return False, "unverifiable", "not an email address"

    domain = email.split("@")[-1].lower()
    has_mx, mx_host = check_mx(domain)

    if not has_mx:
        return False, "unverifiable", f"no MX records for {domain}"

    if not smtp or mx_host is None:
        return True, "mx_only", f"MX ok ({mx_host or 'unknown'}), SMTP not probed"

    result, reason = _smtp_probe(email, mx_host)
    if result is True:
        return True, "smtp_ok", reason
    elif result is False:
        return False, "unverifiable", reason
    else:
        return True, "mx_only", reason


def find_valid_pattern(
    first: str,
    last: str,
    domain: str,
    max_tries: int = 3,
    smtp: bool = True,
) -> tuple[str | None, str, str]:
    """
    Try email patterns for (first, last) at domain.
    Returns (email_or_None, confidence, reason).

    Falls back to None if all patterns fail MX check.
    Stops at first pattern that passes (smtp_ok or mx_only).
    """
    for email in build_patterns(first, last, domain)[:max_tries]:
        ok, conf, reason = verify(email, smtp=smtp)
        if ok:
            return email, conf, reason
        if conf == "unverifiable" and "no MX" in reason:
            # Domain is dead — no point trying more patterns
            return None, conf, reason

    # All patterns exhausted — fall back to generic contact address
    generic = f"contact@{domain}"
    ok, conf, reason = verify(generic, smtp=smtp)
    if ok:
        return generic, f"{conf}+fallback", reason
    return None, "unverifiable", f"all patterns + contact@ failed for {domain}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    addr = sys.argv[1]
    use_smtp = "--no-smtp" not in sys.argv
    ok, conf, reason = verify(addr, smtp=use_smtp)
    icon = "✅" if ok else "❌"
    print(f"{icon} {addr}  [{conf}]  {reason}")
    sys.exit(0 if ok else 1)
