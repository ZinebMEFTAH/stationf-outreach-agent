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

import json
import smtplib
import socket
import subprocess
import sys
import time
from pathlib import Path
from unicodedata import normalize, category


# ---------------------------------------------------------------------------
# Verification cache (persistent) — conserves the paid Hunter quota
# ---------------------------------------------------------------------------
# The Hunter free tier is only ~100 verifications/month, and the same address is
# checked repeatedly (every send, every follow-up, re-runs). We cache each API
# verdict so a given mailbox costs at most ONE Hunter call per TTL window.
_CACHE_PATH = Path(__file__).parent / "cache" / "verify_cache.json"
_CACHE_TTL_DAYS = 30


def _cache_load() -> dict:
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _cache_get(email: str) -> tuple[bool, str, str] | None:
    rec = _cache_load().get(email.strip().lower())
    if not rec:
        return None
    if time.time() - rec.get("ts", 0) > _CACHE_TTL_DAYS * 86400:
        return None
    return rec["reachable"], rec["confidence"], rec["reason"]


def _cache_put(email: str, result: tuple[bool, str, str]) -> None:
    reachable, conf, reason = result
    try:
        data = _cache_load()
        data[email.strip().lower()] = {
            "reachable": reachable, "confidence": conf, "reason": reason, "ts": time.time(),
        }
        _CACHE_PATH.parent.mkdir(exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_CACHE_PATH)
    except Exception:
        pass  # cache is best-effort; never let it break verification


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def strip_diacritics(s: str) -> str:
    return "".join(c for c in normalize("NFD", s) if category(c) != "Mn")


def build_patterns(first: str, last: str, domain: str) -> list[str]:
    """Return common email patterns for a person at a domain, ordered by real-world frequency
    at French companies. The first that verifies wins (and on a catch-all domain the first is
    returned as the best guess), so the most-likely pattern leads. Diacritics stripped,
    lowercased; malformed/duplicate patterns dropped.
    """
    f = strip_diacritics(first).lower().strip()
    l = strip_diacritics(last).lower().strip()
    d = domain.lower().strip()
    fi = f[0] if f else ""
    candidates = [
        f"{f}.{l}@{d}",     # prenom.nom  — dominant FR corporate pattern
        f"{f}@{d}",         # prenom      — small teams / founders
        f"{fi}.{l}@{d}",    # p.nom
        f"{f}-{l}@{d}",     # prenom-nom
        f"{f}{l}@{d}",      # prenomnom
        f"{fi}{l}@{d}",     # pnom
        f"{l}@{d}",         # nom
    ]
    seen: set[str] = set()
    out: list[str] = []
    for p in candidates:
        local = p.split("@", 1)[0]
        if not local or local[0] in ".-" or local[-1] in ".-" or ".." in local or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


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


def _smtp_probe(email: str, mx_host: str, timeout: int = 6) -> tuple[str, str]:
    """RCPT TO probe on port 25, WITH catch-all detection.

    Returns (status, reason), status ∈ {"ok", "invalid", "catchall", "inconclusive"}:
      ok          — mailbox accepted AND a random control address was rejected (confirmed)
      catchall    — mailbox accepted but a random control address was ALSO accepted, so the
                    domain accepts everything → the specific mailbox is NOT confirmed
      invalid     — hard 5xx rejection
      inconclusive— port 25 blocked / timeout / soft error
    """
    import random
    import string
    domain = email.split("@")[-1]
    control = "".join(random.choices(string.ascii_lowercase, k=16)) + "@" + domain
    try:
        with smtplib.SMTP(mx_host, 25, timeout=timeout) as s:
            s.ehlo("verify.check")
            s.mail("")
            code, msg = s.rcpt(email)
            if code >= 500:
                return "invalid", f"SMTP {code}: {msg.decode(errors='replace')[:80]}"
            if code != 250:
                return "inconclusive", f"SMTP {code} (inconclusive)"
            # Target accepted — probe a random address to detect a catch-all domain.
            ccode, _ = s.rcpt(control)
            if ccode == 250:
                return "catchall", "SMTP 250 but domain is catch-all (mailbox unconfirmed)"
            return "ok", "SMTP 250 (mailbox confirmed)"
    except smtplib.SMTPConnectError:
        return "inconclusive", "SMTP port 25 blocked"
    except smtplib.SMTPException as e:
        return "inconclusive", f"SMTP error: {e}"
    except (OSError, UnicodeEncodeError) as e:
        return "inconclusive", f"network/encoding: {e}"


# ---------------------------------------------------------------------------
# API verification (Hunter.io) — works even when outbound port 25 is blocked
# (e.g. on the cloud VM where port 25 is blocked), which is exactly when the SMTP probe can't.
# ---------------------------------------------------------------------------

_HUNTER_ACCT_CACHE = Path(__file__).parent / "cache" / "hunter_account.json"
_HUNTER_ACCT_TTL = 1800  # re-check the real balance at most every 30 min (the endpoint is free)


def _hunter_acct_cached() -> tuple[int, float] | None:
    """Last-known (remaining, ts) from the account cache — at ANY age. None if absent."""
    try:
        rec = json.loads(_HUNTER_ACCT_CACHE.read_text(encoding="utf-8"))
        return int(rec["remaining"]), float(rec["ts"])
    except Exception:
        return None


def hunter_remaining(key: str) -> int | None:
    """Real remaining Hunter verifications from the free /v2/account endpoint (cached ~30min).

    Authoritative — reflects spend from ANY source — so the budget guard can never exceed the
    true 100/month even if verifications were used elsewhere. Returns None if unavailable
    (no fresh cache and the live fetch failed). Callers that can tolerate a stale value should
    use _hunter_acct_cached() as a backstop.
    """
    cached = _hunter_acct_cached()
    if cached is not None and time.time() - cached[1] < _HUNTER_ACCT_TTL:
        return cached[0]
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"https://api.hunter.io/v2/account?api_key={key}", timeout=10) as resp:
            v = json.load(resp)["data"]["requests"]["verifications"]
        remaining = int(v["available"]) - int(v["used"])
    except Exception:
        return None
    try:
        _HUNTER_ACCT_CACHE.parent.mkdir(exist_ok=True)
        _HUNTER_ACCT_CACHE.write_text(
            json.dumps({"remaining": remaining, "ts": time.time()}), encoding="utf-8")
    except Exception:
        pass
    return remaining


def _hunter_budget_ok(key: str) -> bool:
    """Fail-CLOSED quota guard: may we spend one more Hunter verification?

    Priority of evidence, most→least authoritative:
      1. Hunter's real remaining balance (fresh cache or live fetch).
      2. If the live fetch is unavailable: the last-known real balance MINUS our own spend
         recorded since that reading (conservative — never assumes external spend stopped).
      3. If we've never seen a real balance: a local monthly-count cap.
    When exhausted, callers degrade to the SMTP/MX path.
    """
    import config
    import usage_budget
    remaining = hunter_remaining(key)
    if remaining is not None:
        return remaining > config.HUNTER_SAFETY_MARGIN
    stale = _hunter_acct_cached()
    if stale is not None:
        spent_since = usage_budget.count("hunter_verify", window_seconds=max(0.0, time.time() - stale[1]))
        return (stale[0] - spent_since) > config.HUNTER_SAFETY_MARGIN
    ok, _ = usage_budget.allow("hunter_verify", per_month=config.HUNTER_MONTHLY_BUDGET)
    return ok


def verify_via_api(email: str) -> tuple[bool, str, str] | None:
    """Verify via Hunter.io if HUNTER_API_KEY is set in the environment.

    Returns (reachable, confidence, reason), or None if no key is configured or
    the API call fails (caller should then fall back to the SMTP probe).

    Hunter result → our confidence:
      result=deliverable / status=valid        → reachable, "api_valid"
      result=undeliverable / status=invalid     → NOT reachable, "api_invalid"
      accept_all / webmail / unknown / risky     → reachable, "api_risky"
    """
    import os
    key = os.environ.get("HUNTER_API_KEY", "").strip()
    if not key:
        return None
    # Serve a fresh cached verdict without spending quota.
    cached = _cache_get(email)
    if cached is not None and cached[1].startswith("api"):
        return cached
    # Quota self-throttle (fail-closed): stop before hitting the monthly limit; the caller
    # then degrades to the SMTP/MX path exactly as when no key is set.
    if not _hunter_budget_ok(key):
        return None
    import urllib.parse
    import urllib.request
    url = ("https://api.hunter.io/v2/email-verifier?"
           + urllib.parse.urlencode({"email": email, "api_key": key}))
    try:
        with urllib.request.urlopen(url, timeout=12) as resp:
            data = json.load(resp).get("data", {})
    except Exception:
        return None  # API unreachable / quota / error → fall back to SMTP probe
    import usage_budget
    usage_budget.record("hunter_verify")  # count the spend for the local ledger / /status
    result = (data.get("result") or "").lower()
    status = (data.get("status") or "").lower()
    if result == "undeliverable" or status in ("invalid", "disposable"):
        verdict = (False, "api_invalid", f"Hunter: status={status} result={result}")
    elif result == "deliverable" or status == "valid":
        verdict = (True, "api_valid", f"Hunter: status={status} result={result}")
    else:
        verdict = (True, "api_risky", f"Hunter: status={status} result={result}")
    _cache_put(email, verdict)  # cache real verdicts only (not transient None/errors)
    return verdict


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def verify(email: str, smtp: bool = True, use_api: bool = True) -> tuple[bool, str, str]:
    """
    Returns (reachable, confidence, reason).

    reachable:  True  = safe to send to this address
                False = do NOT send; try another pattern / skip
    confidence: "api_valid"    — verification API says deliverable (strongest)
                "api_risky"     — API: catch-all/unknown (domain alive, mailbox unsure)
                "api_invalid"   — API says undeliverable (definitively bad)
                "smtp_ok"       — SMTP confirmed the specific mailbox (control addr rejected)
                "smtp_catchall" — domain accepts everything; mailbox NOT confirmed (a guess)
                "mx_only"       — MX ok, SMTP inconclusive (port 25 blocked / soft error)
                "unverifiable"  — no MX / hard 5xx rejection; do not send

    Order of preference: API (works behind blocked port 25) → SMTP probe → MX-only.

    ``use_api=False`` skips the paid Hunter call — used during enrichment/pattern-guessing,
    where we only need a cheap MX signal to propose a candidate. The authoritative Hunter
    check is then spent once, at SEND time, on the single address we actually email.
    """
    if "@" not in email:
        return False, "unverifiable", "not an email address"

    # 1. Authoritative API check first (if a key is configured and API use is allowed)
    if use_api:
        api = verify_via_api(email)
        if api is not None:
            return api

    # 2. Fall back to MX + SMTP probe
    domain = email.split("@")[-1].lower()
    has_mx, mx_host = check_mx(domain)

    if not has_mx:
        return False, "unverifiable", f"no MX records for {domain}"

    if not smtp or mx_host is None:
        return True, "mx_only", f"MX ok ({mx_host or 'unknown'}), SMTP not probed"

    status, reason = _smtp_probe(email, mx_host)
    if status == "ok":
        return True, "smtp_ok", reason
    elif status == "invalid":
        return False, "unverifiable", reason
    elif status == "catchall":
        return True, "smtp_catchall", reason
    else:
        return True, "mx_only", reason


def find_valid_pattern(
    first: str,
    last: str,
    domain: str,
    max_tries: int = 3,
    smtp: bool = True,
    use_api: bool = False,
) -> tuple[str | None, str, str]:
    """
    Try email patterns for (first, last) at domain.
    Returns (email_or_None, confidence, reason).

    Falls back to None if all patterns fail MX check.
    Stops at first pattern that passes (smtp_ok or mx_only).

    ``use_api`` defaults to False here: pattern-guessing is enrichment work, so it stays
    off the paid Hunter quota — the guess is confirmed authoritatively at send time.
    """
    for email in build_patterns(first, last, domain)[:max_tries]:
        ok, conf, reason = verify(email, smtp=smtp, use_api=use_api)
        if ok:
            return email, conf, reason
        if conf == "unverifiable" and "no MX" in reason:
            # Domain is dead — no point trying more patterns
            return None, conf, reason

    # All patterns exhausted — fall back to generic contact address
    generic = f"contact@{domain}"
    ok, conf, reason = verify(generic, smtp=smtp, use_api=use_api)
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
