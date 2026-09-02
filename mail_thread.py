"""Per-recipient mail thread memory — Message-IDs, and a duplicate-send guard.

Two jobs, both about the same sidecar file:

1. THREADING. A follow-up must land *inside* the original conversation, not as a fresh
   message that happens to start with "Re:". Mail clients thread on the `In-Reply-To` /
   `References` headers, not on the subject — Outlook and Apple Mail ignore subject
   entirely. Without them the recipient sees a context-free "Re: ..." from a stranger,
   which is exactly the shape of bulk mail. So every send records its own `Message-ID`
   here, and the next touch on that thread quotes it back.

2. DUPLICATE GUARD. Nothing else stops the same email going out twice — a re-run of the
   daily skill, a cron double-fire, a retry after a logging error. Sending the same
   message twice to a decision-maker is worse than not sending it at all.

State lives in ``cache/mail_threads.json`` and is per-machine: it holds business email
addresses, so it is gitignored for the same reason as the verification cache, and only
the sending host (the VM) needs it.
"""
from __future__ import annotations

import json
import time
from email.utils import parseaddr
from pathlib import Path

_PATH = Path(__file__).parent / "cache" / "mail_threads.json"

# How many Message-IDs to carry in the References chain. RFC 5322 allows the full history;
# real clients only need enough to place the message, and unbounded growth bloats headers.
MAX_REFERENCES = 10
# A send is a duplicate if the same address gets BYTE-IDENTICAL content inside this window.
# Deliberately fingerprinted on subject + body, NOT on the subject alone: a follow-up is
# supposed to reuse the subject with a "Re:" prefix — that is what threads it — and the
# multi-touch sequence sends up to three of them under one subject. Keying on the subject
# would have made correct threading indistinguishable from spam.
DUPLICATE_WINDOW_DAYS = 14
# Prune threads untouched for this long so the file cannot grow without bound.
_RETAIN_DAYS = 180


def _load() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        _PATH.parent.mkdir(exist_ok=True)
        tmp = _PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_PATH)
    except Exception:
        pass  # sidecar bookkeeping must never break a send


def _addr(address: str) -> str:
    return parseaddr(str(address or ""))[1].strip().lower()


def thread_key(address: str, company: str | None = None, role: str | None = None) -> str:
    """Identify one conversation.

    Keyed on address + company + role, because the same mailbox is legitimately used for
    two different openings at one company, and those are separate conversations. Falls
    back to the bare address when the caller has no row context.
    """
    parts = [_addr(address)]
    if company:
        parts.append(str(company).strip().lower())
    if role:
        parts.append(str(role).strip().lower())
    return "|".join(parts)


def get(address: str, company: str | None = None, role: str | None = None) -> dict:
    """Thread record: {'message_id', 'references': [...], 'subjects': {subject: ts}}."""
    return _load().get(thread_key(address, company, role), {})


def reply_headers(address: str, company: str | None = None,
                  role: str | None = None) -> dict[str, str]:
    """`In-Reply-To` / `References` for the next message on this thread ({} if it is new).

    Falls back to any other thread with the same ADDRESS when this exact (company, role)
    has none: a re-scrape mints a fresh row for a person already in conversation, and the
    follow-up should still thread rather than starting over.
    """
    rec = get(address, company, role)
    if not rec.get("message_id"):
        addr = _addr(address)
        best, best_ts = None, -1.0
        for key, other in _load().items():
            if key.split("|", 1)[0] == addr and other.get("message_id"):
                if float(other.get("ts") or 0) > best_ts:
                    best, best_ts = other, float(other.get("ts") or 0)
        rec = best or {}
    mid = rec.get("message_id")
    if not mid:
        return {}
    refs = list(rec.get("references") or [])
    if mid not in refs:
        refs.append(mid)
    return {"In-Reply-To": mid, "References": " ".join(refs[-MAX_REFERENCES:])}


def content_fingerprint(subject: str, body: str) -> str:
    """Stable hash of what the recipient actually reads.

    Whitespace- and case-normalised, and any Re:/Fwd: prefix stripped, so trivial
    reformatting cannot slip a repeat past the guard.
    """
    import hashlib
    payload = _norm_subject(subject) + "\n" + " ".join(str(body or "").lower().split())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def record(address: str, message_id: str, subject: str = "",
           company: str | None = None, role: str | None = None,
           body: str = "") -> None:
    """Remember the Message-ID we just sent, and a fingerprint of what we sent."""
    data = _load()
    now = time.time()
    key = thread_key(address, company, role)
    rec = data.get(key, {})
    refs = list(rec.get("references") or [])
    if rec.get("message_id") and rec["message_id"] not in refs:
        refs.append(rec["message_id"])
    rec["message_id"] = message_id
    rec["references"] = refs[-MAX_REFERENCES:]
    rec["ts"] = now
    sent = dict(rec.get("sent") or {})
    sent[content_fingerprint(subject, body)] = now
    # Keep the window bounded; anything older than the guard window cannot match anyway.
    rec["sent"] = {k: v for k, v in sent.items()
                   if now - float(v) <= DUPLICATE_WINDOW_DAYS * 86400}
    data[key] = rec
    cutoff = now - _RETAIN_DAYS * 86400
    data = {k: v for k, v in data.items() if float(v.get("ts") or now) >= cutoff}
    _save(data)


def _norm_subject(subject: str) -> str:
    """Compare subjects ignoring case, whitespace and any stack of Re:/Fwd: prefixes."""
    s = " ".join(str(subject or "").split()).lower()
    while True:
        for p in ("re:", "re :", "fwd:", "fw:", "tr:"):
            if s.startswith(p):
                s = s[len(p):].lstrip()
                break
        else:
            return s


def recent_duplicate(address: str, subject: str, body: str,
                     days: int = DUPLICATE_WINDOW_DAYS) -> float | None:
    """Timestamp of an identical MESSAGE already sent to this ADDRESS, or None.

    Keyed on the address, not the row: a re-scrape mints new rows carrying an address
    already in conversation, and re-sending the same message from a different row is exactly
    the failure this guards against. A follow-up in a sequence has different words, so it
    passes; a re-run of the same skill produces the same bytes, so it does not.
    """
    addr = _addr(address)
    if "@" not in addr:
        return None
    fp = content_fingerprint(subject, body)
    cutoff = time.time() - days * 86400
    for key, rec in _load().items():
        if key.split("|", 1)[0] != addr:
            continue
        ts = (rec.get("sent") or {}).get(fp)
        if ts and float(ts) >= cutoff:
            return float(ts)
    return None


if __name__ == "__main__":
    import sys
    data = _load()
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        for k, v in sorted(data.items(), key=lambda kv: -float(kv[1].get("ts") or 0)):
            when = time.strftime("%Y-%m-%d", time.localtime(float(v.get("ts") or 0)))
            print(f"  {when}  {k}  msgid={v.get('message_id')}  "
                  f"refs={len(v.get('references') or [])}  sent={len(v.get('sent') or {})}")
    print(f"{len(data)} thread(s) in {_PATH}")
