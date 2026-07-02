"""Lead-fact sidecar cache — one researched "hook fact" per company, computed during the CHEAP
enrichment phase (/find-contacts) and reused by /daily-agent so the send step doesn't re-research
every company from cold. That both grounds the opener in something real AND cuts the agent's
per-email research load — which matters directly under the Claude subscription's 5-hour usage
window (see vm/crontab.txt).

Architecture: raw I/O only. This module just stores and serves strings — the SKILLS (Claude) do
the reasoning that produces a fact. It never touches contacts.xlsx (the strict 6-column schema is
untouched); the cache is a side file at cache/lead_facts.json:

    { "<normalized company>": {"company": str, "fact": str, "source": str, "ts": "YYYY-MM-DD"} }

Best-effort by design: a missing or corrupt cache returns nothing, so the agent simply falls back
to full research. Facts older than FRESH_DAYS are treated as stale (companies move fast).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re

_PATH = os.path.join(os.path.dirname(__file__), "cache", "lead_facts.json")
FRESH_DAYS = 21


def _norm(company: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (company or "").lower())


def _load() -> dict:
    try:
        with open(_PATH, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, _PATH)  # atomic


def put(company: str, fact: str, source: str = "") -> None:
    """Store one hook fact for a company (overwrites any previous one). No-op on empty input."""
    key = _norm(company)
    fact = (fact or "").strip()
    if not key or not fact:
        return
    d = _load()
    d[key] = {
        "company": (company or "").strip(),
        "fact": fact,
        "source": (source or "").strip(),
        "ts": _dt.date.today().isoformat(),
    }
    _save(d)


def get(company: str, fresh_days: int = FRESH_DAYS) -> dict | None:
    """Return {company, fact, source, ts} if a FRESH fact exists for the company, else None."""
    rec = _load().get(_norm(company))
    if not isinstance(rec, dict) or not rec.get("fact"):
        return None
    try:
        age = (_dt.date.today() - _dt.date.fromisoformat(rec["ts"])).days
    except (ValueError, KeyError, TypeError):
        return None
    return rec if age <= fresh_days else None


def stats() -> dict:
    """Small summary for /status and preflight."""
    d = _load()
    return {"total": len(d)}
