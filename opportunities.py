#!/usr/bin/env python3
"""International opportunity digest — emails Zineb a curated list of international offers.

This is a SCOUTING digest for Zineb to review — the opposite direction of the outreach agent
(which emails companies). It contacts no one. It fetches international/remote roles, keeps only
those that (a) fit her profile (AI / ML / Data / Backend / Software) and (b) she could realistically
get (drops senior / staff / lead / director / architect / N+ years — she's an M1 with ~1 yr
experience), dedupes against what she's already been shown, and emails her the NEW ones.

Sources (remote, workable from France): Remotive (global) + Jobicy (Europe-available, has an
explicit job-level field). Extensible — add more to _fetch_all().

Usage:
  python opportunities.py            # dry-run: print the digest, don't send, don't record
  python opportunities.py --send     # email the digest to Zineb + record offers as seen
  python opportunities.py --min N    # only send if at least N new offers (default 1)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import config
import jobsource as js
import remotive

_SEEN_PATH = Path(__file__).parent / "cache" / "opportunities_seen.json"
_SEEN_TTL = 45 * 24 * 3600  # forget an offer after 45 days so re-posts can resurface

# ── Profile fit (broad, review-appropriate) ──────────────────────────────────
_ROLE_INCLUDE = re.compile(
    r"(software|backend|back[- ]?end|front[- ]?end|full[- ]?stack|developer|développeur|"
    r"engineer|machine learning|\bml\b|\bai\b|artificial intelligence|data|mlops|nlp|"
    r"computer vision|\bllm\b|deep learning|python|research engineer|research scientist)", re.I)
_ROLE_EXCLUDE = re.compile(
    r"\b(sales|support|account|customer|marketing|success|recruit|hr|finance|legal|"
    r"graphic|ux|ui designer|product manager|project manager|program manager|qa|"
    r"test engineer|tester|consultant|salesforce|scrum|php|ruby|wordpress|embedded|"
    r"firmware|ios|android|mobile|pam|sre|security|"
    # Non-engineering roles that slip through because a stack word (AI/data) is in the title
    r"producer|creative|artist|writer|copywriter|content|community|evangelist|advocate|"
    r"teacher|instructor|educator|designer|analyst relations)\b", re.I)
# Off-stack tokens that contain non-word chars (so they don't fit inside \b…\b groups).
_STACK_EXCLUDE = re.compile(r"(front[- ]?end|\.net|c#|c\+\+)", re.I)

# "May be accepted": drop roles clearly above a strong-junior level. She reviews the rest.
_TOO_SENIOR = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|distinguished|head\s+of|director|vp|vice[- ]president|"
    r"chief|c[te]o|expert|architect|manager|1[0-9]\s*\+?\s*(?:years|yrs|ans)|"
    r"[4-9]\s*\+\s*(?:years|yrs|ans))\b", re.I)
_JUNIOR = re.compile(
    r"\b(intern(ship)?|junior|jr\.?|graduate|new[- ]grad|entry[- ]level|apprentice|"
    r"working student|alternance|alternant|stage|stagiaire|associate|trainee)\b", re.I)


def role_fit(title: str) -> bool:
    t = title or ""
    return bool(_ROLE_INCLUDE.search(t) and not _ROLE_EXCLUDE.search(t)
                and not _STACK_EXCLUDE.search(t))


def seniority_ok(title: str, level: str = "") -> bool:
    """True if a strong junior could realistically apply."""
    t = title or ""
    lv = (level or "").lower()
    if _JUNIOR.search(t) or lv in ("junior", "entry", "entry-level", "intern", "any"):
        return True
    if lv in ("senior", "lead", "principal", "manager", "director", "executive"):
        return False
    return not _TOO_SENIOR.search(t)


def category_of(title: str) -> str:
    t = (title or "").lower()
    if re.search(r"machine learning|\bml\b|\bai\b|artificial intelligence|nlp|computer vision|\bllm\b|deep learning|research", t):
        return "ai"
    if "data" in t:
        return "data"
    return "backend"


# ── Seen-cache (so a daily digest only shows NEW offers) ──────────────────────

def _seen_load() -> dict:
    try:
        return json.loads(_SEEN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _seen_save(d: dict) -> None:
    try:
        _SEEN_PATH.parent.mkdir(exist_ok=True)
        tmp = _SEEN_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_SEEN_PATH)
    except Exception:
        pass


def _offer_key(o: dict) -> str:
    return (o.get("url") or f"{o.get('company','')}|{o.get('role','')}").strip().lower()


# ── Sources ───────────────────────────────────────────────────────────────────

_REMOTIVE_QUERIES = ["machine learning", "AI engineer", "data scientist", "data engineer",
                     "backend engineer", "python developer", "MLOps", "NLP", "computer vision"]


def _fetch_remotive() -> list[dict]:
    out, seen = [], set()
    for query in _REMOTIVE_QUERIES:
        try:
            rows = remotive._search(query, limit=50)
        except Exception as e:  # noqa: BLE001
            print(f"[opps]   remotive '{query}' error: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        for j in rows:
            title = (j.get("title") or "").strip()
            company = (j.get("company_name") or "").strip()
            url = (j.get("url") or "").strip()
            key = url or f"{company}|{title}".lower()
            if not title or len(company) < 2 or key in seen:
                continue
            if not remotive.is_workable_location(j.get("candidate_required_location", "")):
                continue
            if any(f in title.lower() for f in remotive._FREELANCE):
                continue
            if not role_fit(title) or not seniority_ok(title):
                continue
            seen.add(key)
            out.append({"company": company, "role": title, "url": url,
                        "location": (j.get("candidate_required_location") or "Remote").strip(),
                        "category": category_of(title), "source": "Remotive"})
    return out


def _fetch_jobicy() -> list[dict]:
    """Jobicy remote jobs available in Europe (has an explicit jobLevel field)."""
    out = []
    try:
        url = "https://jobicy.com/api/v2/remote-jobs?" + urllib.parse.urlencode(
            {"count": 100, "geo": "europe"})
        req = urllib.request.Request(url, headers={"User-Agent": js.DEFAULT_UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            rows = json.load(r).get("jobs", []) or []
    except Exception as e:  # noqa: BLE001
        print(f"[opps]   jobicy error: {type(e).__name__}: {e}", file=sys.stderr)
        return out
    for j in rows:
        title = (j.get("jobTitle") or "").strip()
        company = (j.get("companyName") or "").strip()
        url = (j.get("url") or "").strip()
        level = (j.get("jobLevel") or "").strip()
        if not title or len(company) < 2:
            continue
        jobtype = " ".join(j.get("jobType") or []) if isinstance(j.get("jobType"), list) else str(j.get("jobType") or "")
        if "freelance" in jobtype.lower() or "freelance" in title.lower():
            continue
        if not role_fit(title) or not seniority_ok(title, level):
            continue
        geo = (j.get("jobGeo") or "Remote").strip()
        out.append({"company": company, "role": title, "url": url,
                    "location": geo, "category": category_of(title), "source": "Jobicy"})
    return out


def _fetch_all() -> list[dict]:
    offers, seen = [], set()
    for o in _fetch_remotive() + _fetch_jobicy():
        k = _offer_key(o)
        if k in seen:
            continue
        seen.add(k)
        offers.append(o)
    return offers


_MAX_PER_COMPANY = 2  # keep the digest diverse — no single careers-page flooding it


def _cap_per_company(offers: list[dict], limit: int = _MAX_PER_COMPANY) -> list[dict]:
    """Keep at most `limit` offers per company so one big hirer can't dominate the digest."""
    counts: dict[str, int] = {}
    out = []
    for o in offers:
        key = (o.get("company") or "").strip().lower()
        if counts.get(key, 0) >= limit:
            continue
        counts[key] = counts.get(key, 0) + 1
        out.append(o)
    return out


def new_offers() -> list[dict]:
    """Fetched, profile+seniority-filtered offers not already shown to Zineb (fresh in cache)."""
    seen = _seen_load()
    now = time.time()
    out = [o for o in _fetch_all()
           if not (seen.get(_offer_key(o)) and now - seen[_offer_key(o)].get("ts", 0) < _SEEN_TTL)]
    order = {"ai": 0, "data": 1, "backend": 2}
    out.sort(key=lambda o: (order.get(o["category"], 9), o["company"].lower()))
    return _cap_per_company(out)


def record_seen(offers: list[dict]) -> None:
    seen = _seen_load()
    now = time.time()
    for o in offers:
        seen[_offer_key(o)] = {"ts": now, "company": o["company"], "role": o["role"]}
    seen = {k: v for k, v in seen.items() if now - v.get("ts", 0) < _SEEN_TTL}
    _seen_save(seen)


# ── Digest formatting ─────────────────────────────────────────────────────────

_CAT_LABEL = {"ai": "AI / ML", "data": "Data", "backend": "Backend / Software"}


def format_digest(offers: list[dict]) -> str:
    if not offers:
        return ("No new international offers matched your profile today. I check daily and will "
                "email you the moment good ones appear — so you don't have to hunt.")
    lines = [
        f"{len(offers)} new international opportunit{'y' if len(offers)==1 else 'ies'} that fit your "
        "profile (AI/ML/Data/Backend) and look realistic for a strong junior — remote, workable from "
        "France. Apply to the ones you like; reply with any you want the outreach agent to chase.",
        "",
    ]
    current = None
    for o in offers:
        if o["category"] != current:
            current = o["category"]
            lines.append(f"── {_CAT_LABEL.get(current, current.title())} ──")
        lines.append(f"• {o['role']}")
        lines.append(f"  {o['company']}  ·  {o['location']}  ·  {o['source']}")
        if o["url"]:
            lines.append(f"  {o['url']}")
        lines.append("")
    lines.append("— Your opportunity scout. These are for YOU to review; nothing was contacted.")
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="International opportunity digest for Zineb")
    ap.add_argument("--send", action="store_true", help="email the digest + record offers as seen")
    ap.add_argument("--min", type=int, default=1, help="minimum new offers to bother sending")
    ap.add_argument("--to", default=config.INTERNAL_ALERT_EMAIL, help="recipient (default: Zineb)")
    args = ap.parse_args(argv)

    offers = new_offers()
    body = format_digest(offers)
    print(f"[opps] {len(offers)} new offer(s)\n")
    print(body)

    if not args.send:
        print("\n[opps] dry-run (no --send): nothing emailed, nothing recorded.")
        return 0
    if len(offers) < args.min:
        print(f"\n[opps] only {len(offers)} new offer(s) (< --min {args.min}) — not sending.")
        return 0

    import smtp_send
    subject = f"🌍 {len(offers)} new international offer(s) for you — {time.strftime('%d %b')}"
    res = smtp_send.send_and_log(
        to_address=args.to, subject=subject, body=body,
        attachment_path=None, new_status=None, kind="alert", dry_run=False,
    )
    if res.ok:
        record_seen(offers)
        print(f"\n[opps] ✅ digest sent to {args.to}; {len(offers)} offer(s) recorded as seen.")
        return 0
    print(f"\n[opps] ❌ send failed: {res.error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
