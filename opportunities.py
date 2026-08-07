#!/usr/bin/env python3
"""Opportunity digest — emails Zineb a curated list of jobs she can apply to, remote AND in-person.

This is a SCOUTING digest for Zineb to review — the opposite direction of the outreach agent
(which emails companies). It contacts no one. It fetches roles, keeps only those that (a) fit her
profile (AI / ML / Data / Backend / Software) and (b) she could realistically get (drops senior /
staff / lead / director / architect / N+ years — she's an M1 with ~1 yr experience), dedupes against
what she's already been shown, and emails her the NEW ones — grouped by location mode.

Both modes are covered (she pursues remote AND in-person, and is open to relocating) — grouped into
three digest sections, each capped so none starves another:
  • REMOTE — Remotive (global) + Jobicy (Europe) + RemoteOK (global JSON) + WeWorkRemotely (EU-RSS).
  • IN-PERSON / HYBRID — France (Île-de-France) — APEC + France Travail, via _fetch_france_inperson().
  • ON-SITE ABROAD — elsewhere in the EU (relocation) — Arbeitnow + The Muse (keyless), via
    _fetch_arbeitnow() / _fetch_themuse(); a France location routes back to the France section.
Each source is filtered by the same role/seniority gates; offers are tagged with a `mode`
(remote|hybrid|onsite), deduped by URL AND normalized company|role (same posting on two boards),
capped per company AND per section (a reviewable digest — the overflow rolls into following days via
the seen-cache). Extensible — add more fetchers to _fetch_all().

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
    r"\b(sales|support|account|customer|marketing|martech|gtm|go[- ]to[- ]market|success|"
    r"recruit|hr|finance|legal|graphic|ux|ui designer|product manager|project manager|"
    r"program manager|qa|test engineer|tester|consultant|consulting|salesforce|scrum|php|ruby|"
    r"wordpress|web developer|webmaster|embedded|firmware|ios|android|mobile|pam|sre|security|"
    # Ops/QA titles spelled out — the abbreviations above miss these (seen in real digests:
    # "Site Reliability Engineer in Network Infrastructure", "Software Development Engineer in Test")
    r"site reliability|reliability engineer|sdet|in test|quality assurance|"
    # Non-engineering roles that slip through because a stack word (AI/data) is in the title
    r"producer|creative|artist|writer|copywriter|content|community|evangelist|advocate|"
    r"influencer|teacher|instructor|educator|designer|analyst relations|"
    # Off-domain noise (gambling/casino roles surface on the generic boards)
    r"casino|gambling|betting|sportsbook)\b", re.I)
# Off-stack tokens that contain non-word chars (so they don't fit inside \b…\b groups).
_STACK_EXCLUDE = re.compile(r"(front[- ]?end|\.net|c#|c\+\+)", re.I)

# "May be accepted": drop roles clearly above a strong-junior level. She reviews the rest.
_TOO_SENIOR = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|distinguished|head\s+of|director|vp|vice[- ]president|"
    r"chief|c[te]o|expert|architect|manager|1[0-9]\s*\+?\s*(?:years|yrs|ans)|"
    # French seniority markers — APEC/France Travail titles say "confirmé", never "senior"
    r"confirm[ée]e?|exp[ée]riment[ée]e?|s[ée]nior|"
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


# ── Fit scoring — decides WHICH offers she actually sees ──────────────────────
# The filters above answer "could she apply?"; this answers "is it worth her attention?".
# It matters because every section is capped: without a score the caps kept whatever sorted
# first alphabetically, so a strong match at "Spotify" lost its slot to a generic ESN posting
# at "ACCENTURE". Offers are now ranked by score and the caps keep the BEST ones.
# Scored on the title + location + company — the only fields every board gives us.

_FIT_FLOOR = 40  # below this it isn't worth her time; dropped from the digest entirely

# Category = how close the role sits to what she's targeting (AI/ML first, then Data, then Backend).
_CAT_POINTS = {"ai": 30, "data": 24, "backend": 20}

# Her real stack, weighted by how distinctive it is. Generic "AI"/"ML" words are deliberately absent —
# they're already paid for by _CAT_POINTS, so they can't be counted twice.
_STACK_SIGNALS = (
    (re.compile(r"\b(rag|llm|genai|generative|agentic|agents?|nlp|langchain|embedding|vector)\b", re.I), 12, "LLM/RAG"),
    (re.compile(r"\b(mlops|kubeflow|mlflow|airflow|sagemaker|vertex ai|feature store)\b", re.I), 10, "MLOps"),
    (re.compile(r"\b(pytorch|tensorflow|deep learning|computer vision|cv)\b", re.I), 8, "deep learning"),
    (re.compile(r"\bpython\b", re.I), 7, "Python"),
    (re.compile(r"\b(docker|kubernetes|k8s|aws|gcp|azure|cloud)\b", re.I), 5, "cloud"),
    (re.compile(r"\b(sql|postgres|spark|etl|pipeline|dbt)\b", re.I), 4, "data pipeline"),
)
_STACK_CAP = 18  # one very buzzwordy title shouldn't outrank a genuinely closer role

# Île-de-France — where she lives, so no relocation friction. Departments + the usual tech suburbs.
_IDF = re.compile(
    r"\b(paris|[îi]le[- ]de[- ]france|idf|hauts[- ]de[- ]seine|seine[- ]saint[- ]denis|"
    r"val[- ]de[- ]marne|essonne|yvelines|val[- ]d'?oise|seine[- ]et[- ]marne|"
    r"saclay|orsay|palaiseau|boulogne|issy|montrouge|levallois|courbevoie|nanterre|"
    r"la d[ée]fense|clichy|saint[- ]denis|massy|v[ée]lizy|meudon|malakoff)\b|"
    r"\b(7[58]|9[12345]|77)\s*-", re.I)

# Outside the EU: reachable in principle, but a work visa turns a click into a months-long process,
# so these must not outrank a role she can start in September. The UK is the big one — post-Brexit
# it needs sponsorship, yet the boards still file London under "Europe".
_NON_EU = re.compile(
    r"\b(united kingdom|uk|england|london|manchester|edinburgh|bristol|cambridge|oxford|"
    r"switzerland|zurich|geneva|united states|usa|us[- ]only|canada|remote \(us)\b", re.I)

# Contract shape. Alternance is what she needs for September 2026, so it leads; a 2-3 month
# research CDD is a real posting but useless to someone starting a Master.
_ALTERNANCE = re.compile(r"\b(alternan[ct]e?|apprentissage|apprentice|contrat pro|work[- ]study|working student)\b", re.I)
_INTERNSHIP = re.compile(r"\b(intern(ship)?|stage|stagiaire|trainee)\b", re.I)
_CDI = re.compile(r"\b(cdi|permanent|full[- ]time)\b", re.I)
_SHORT_CDD = re.compile(r"\b(cdd|contract)\b.{0,30}?\b([1-5])[.,]?\d*\s*mois\b|\b([1-5])[.,]?\d*\s*mois\b", re.I)

# ESN / SSII bodyshops. Not excluded — they hire juniors and she may well want them — but they
# flood APEC and would otherwise fill every slot, so they yield to product companies on a tie.
_ESN = {
    "accenture", "atos", "capgemini", "sopra steria", "sopra", "cgi", "akkodis", "alten",
    "expleo", "devoteam", "talan", "onepoint", "davidson consulting", "davidson", "inetum",
    "sii", "assystem", "altran", "segula", "ausy", "modis", "econocom", "umanis", "keyrus",
    "micropole", "cgilanum", "alliance concept informatique", "exalt", "exalt lyon",
}
# Job-board names that leak into the company field as if they were the employer.
_JUNK_COMPANIES = {"hellowork", "apec", "france travail", "francetravail", "pole emploi",
                   "pôle emploi", "indeed", "linkedin", "welcome to the jungle", "glassdoor"}


def _norm_company(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (name or "").strip().lower()).strip()


def fit_score(offer: dict) -> tuple[int, list[str]]:
    """Score an offer 0-100 for how well it suits Zineb, with the reasons that produced it.

    The reasons are not debug output — they're printed in the digest so she can triage a role in
    one glance instead of opening every link.
    """
    title = offer.get("role") or ""
    loc = offer.get("location") or ""
    company = _norm_company(offer.get("company"))
    blob = f"{title} {loc}"
    score = 0
    why: list[str] = []

    if company in _JUNK_COMPANIES:
        return 0, ["job-board name in the company field, not a real employer"]

    cat = offer.get("category") or category_of(title)
    score += _CAT_POINTS.get(cat, 18)
    why.append({"ai": "AI/ML role", "data": "Data role"}.get(cat, "Backend/software role"))

    # Explicitly junior postings are the ones she converts; an unmarked title is usually open but
    # written for someone with a couple of years, so it scores lower rather than being dropped.
    if _JUNIOR.search(title):
        score += 22
        why.append("junior/entry-level")
    else:
        score += 8

    stack = 0
    for rx, pts, label in _STACK_SIGNALS:
        if rx.search(blob):
            stack += pts
            why.append(label)
    score += min(stack, _STACK_CAP)

    if _IDF.search(loc):
        score += 14
        why.append("Île-de-France")
    elif offer.get("mode", "remote") == "remote" and not _NON_EU.search(blob):
        score += 11
        why.append("remote")
    elif _NON_EU.search(blob):
        why.append("visa/sponsorship needed")
    elif "france" in loc.lower() or offer.get("source", "").lower() in _FR_SOURCES:
        score += 9
        why.append("elsewhere in France")
    else:
        score += 5
        why.append("relocation")

    if _ALTERNANCE.search(blob):
        score += 16
        why.append("alternance")
    elif _CDI.search(blob):
        score += 8
        why.append("CDI")
    elif _INTERNSHIP.search(blob):
        score += 3
        why.append("internship")
    if _SHORT_CDD.search(blob):
        score -= 12
        why.append("short contract")

    if company in _ESN or any(company.startswith(e + " ") for e in _ESN):
        score -= 12
        why.append("ESN/consultancy")

    return max(0, min(100, score)), why


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


def _fetch_remoteok() -> list[dict]:
    """RemoteOK public JSON API (global remote board). One flat feed — filter to workable
    locations (its `location` field is the candidate restriction; empty = worldwide = keep)."""
    out = []
    try:
        req = urllib.request.Request("https://remoteok.com/api",
                                     headers={"User-Agent": js.DEFAULT_UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            rows = json.load(r)
    except Exception as e:  # noqa: BLE001
        print(f"[opps]   remoteok error: {type(e).__name__}: {e}", file=sys.stderr)
        return out
    for j in rows:
        # The first element is a legal/notice object with no 'position' — skip anything unlike a job.
        if not isinstance(j, dict) or not j.get("position"):
            continue
        title = (j.get("position") or "").strip()
        company = (j.get("company") or "").strip()
        if not title or len(company) < 2:
            continue
        tags = " ".join(j.get("tags") or []) if isinstance(j.get("tags"), list) else ""
        if "freelance" in tags.lower() or any(f in title.lower() for f in remotive._FREELANCE):
            continue
        # RemoteOK `location` = candidate region restriction; reuse the same allowlist as Remotive.
        loc = (j.get("location") or "").strip()
        if not remotive.is_workable_location(loc):
            continue
        if not role_fit(title) or not seniority_ok(title):
            continue
        url = (j.get("apply_url") or j.get("url") or "").strip()
        if not url and j.get("slug"):
            url = f"https://remoteok.com/remote-jobs/{j['slug']}"
        out.append({"company": company, "role": title, "url": url,
                    "location": loc or "Remote", "category": category_of(title),
                    "source": "RemoteOK"})
    return out


# programming + back-end carry the AI/ML/Data/Backend roles that fit Zineb. The devops-sysadmin
# category was dropped: it floods generic enterprise-ops noise (Splunk/OCI/securitization admin),
# and genuine MLOps/platform roles still surface via role_fit in the programming feed.
_WWR_CATEGORIES = ("remote-programming-jobs", "remote-back-end-programming-jobs")
_WWR_NS = "{https://weworkremotely.com/}"


def _wwr_workable(text: str) -> bool:
    """WWR puts region in the title/region as '[EUROPE ONLY]', '[USA ONLY]', etc. Keep EU/worldwide
    and unspecified; drop an explicit non-EU 'X only' restriction."""
    t = (text or "").lower()
    if any(k in t for k in ("europe", "emea", "worldwide", "anywhere", "global", " uk", "united kingdom")):
        return True
    if "only" in t and any(k in t for k in (
            "usa", "u.s", "united states", "north america", "americas", "latam", "latin america",
            "apac", "asia", "africa", "canada", "australia")):
        return False
    return True  # unspecified → assume open


def _fetch_wwr() -> list[dict]:
    """WeWorkRemotely — curated remote board (RSS per category). Title format is 'Company: Position'."""
    import xml.etree.ElementTree as ET
    out, seen = [], set()
    for cat in _WWR_CATEGORIES:
        try:
            req = urllib.request.Request(f"https://weworkremotely.com/categories/{cat}.rss",
                                         headers={"User-Agent": js.DEFAULT_UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                root = ET.fromstring(r.read())
        except Exception as e:  # noqa: BLE001
            print(f"[opps]   wwr '{cat}' error: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        for it in root.findall(".//item"):
            raw = (it.findtext("title") or "").strip()
            region = (it.findtext(_WWR_NS + "region") or "").strip()
            url = (it.findtext("link") or "").strip()
            company, _, title = raw.partition(":")
            company = company.strip(" ⭐️")
            # WWR titles sometimes carry a leading emoji/variation-selector — strip leading non-word junk.
            title = re.sub(r"^[^\w\[(]+", "", title.strip()).strip()
            if not title:  # no 'Company: Position' split → skip malformed
                continue
            if len(company) < 2 or len(company) > 60:
                continue
            key = url or f"{company}|{title}".lower()
            if key in seen:
                continue
            if not _wwr_workable(raw + " " + region):
                continue
            if any(f in title.lower() for f in remotive._FREELANCE):
                continue
            if not role_fit(title) or not seniority_ok(title):
                continue
            seen.add(key)
            out.append({"company": company, "role": title, "url": url,
                        "location": region or "Remote (EU-ok)", "category": category_of(title),
                        "source": "WeWorkRemotely"})
    return out


def _fetch_france_inperson() -> list[dict]:
    """In-person / hybrid roles at FRENCH companies she could apply to directly (APEC + France
    Travail — the richest keyless/keyed French APIs). These complete the 'both remote AND in-person'
    coverage: the remote boards above never surface an on-site French role. Nationwide (all of France,
    not just Paris — she's open to relocating within France); each offer shows its real city and its
    mode is classified from the title + location text (defaults to on-site)."""
    import importlib
    out, seen = [], set()
    for name in ("apec", "france_travail"):
        try:
            listings = importlib.import_module(name).discover()
        except Exception as e:  # noqa: BLE001
            print(f"[opps]   {name} error: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        for j in listings:
            title = (getattr(j, "role", "") or "").strip()
            company = (getattr(j, "company", "") or "").strip()
            if not title or len(company) < 2:
                continue
            if not role_fit(title) or not seniority_ok(title):
                continue
            url = getattr(j, "job_url", "") or ""
            key = (url or f"{company}|{title}").lower()
            if key in seen:
                continue
            seen.add(key)
            loc = (getattr(j, "location", None) or "").strip()
            out.append({"company": company, "role": title, "url": url,
                        "location": loc or "France",
                        "category": getattr(j, "category", None) or category_of(title),
                        "source": getattr(j, "source", "french-board"),
                        "mode": config.classify_location(f"{title} {loc}") or "onsite"})
    return out


def _fetch_arbeitnow() -> list[dict]:
    """Arbeitnow public API (EU jobs, no key) — on-site/hybrid roles ABROAD she could relocate for
    (Berlin, Amsterdam, Zurich…), plus some remote. Fills the 'open to relocating in the EU' gap the
    France + remote-board feeds don't cover. Uses the API's own `remote` flag, else classifies."""
    out = []
    try:
        req = urllib.request.Request("https://www.arbeitnow.com/api/job-board-api",
                                     headers={"User-Agent": js.DEFAULT_UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            jobs = json.load(r).get("data", []) or []
    except Exception as e:  # noqa: BLE001
        print(f"[opps]   arbeitnow error: {type(e).__name__}: {e}", file=sys.stderr)
        return out
    for j in jobs:
        title = (j.get("title") or "").strip()
        company = (j.get("company_name") or "").strip()
        if not title or len(company) < 2:
            continue
        jt = " ".join(j.get("job_types") or []) if isinstance(j.get("job_types"), list) else ""
        if "freelance" in jt.lower() or any(f in title.lower() for f in remotive._FREELANCE):
            continue
        if not role_fit(title) or not seniority_ok(title):
            continue
        loc = (j.get("location") or "").strip()
        mode = "remote" if j.get("remote") else (config.classify_location(f"{title} {loc}") or "onsite")
        out.append({"company": company, "role": title, "url": (j.get("url") or "").strip(),
                    "location": loc or "EU", "category": category_of(title),
                    "source": "Arbeitnow", "mode": mode})
    return out


_MUSE_CATS = ("Data Science", "Software Engineering", "Data and Analytics")
_MUSE_EU = (
    "france", "paris", "germany", "berlin", "munich", "netherlands", "amsterdam", "ireland",
    "dublin", "spain", "madrid", "barcelona", "italy", "milan", "belgium", "brussels", "portugal",
    "lisbon", "sweden", "stockholm", "poland", "warsaw", "switzerland", "zurich", "austria",
    "vienna", "luxembourg", "denmark", "copenhagen", "united kingdom", "london", "europe", "emea",
    "remote", "flexible")


def _fetch_themuse(pages: int = 3) -> list[dict]:
    """The Muse public API (no key) — junior international roles with real location + level fields.
    Broadens the abroad pool beyond Arbeitnow's DACH focus (Celonis Munich/Paris, etc.). Filtered to
    EU/remote-workable locations and entry-level/internship, then the usual role/seniority gates."""
    out = []
    base = [("category", c) for c in _MUSE_CATS] + [("level", "Entry Level"), ("level", "Internship")]
    for p in range(1, pages + 1):
        try:
            url = "https://www.themuse.com/api/public/jobs?" + urllib.parse.urlencode(base + [("page", p)])
            req = urllib.request.Request(url, headers={"User-Agent": js.DEFAULT_UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                results = json.load(r).get("results", []) or []
        except Exception as e:  # noqa: BLE001
            print(f"[opps]   themuse p{p} error: {type(e).__name__}: {e}", file=sys.stderr)
            break
        for j in results:
            title = (j.get("name") or "").strip()
            company = ((j.get("company") or {}).get("name") or "").strip()
            if not title or len(company) < 2:
                continue
            locs = [l.get("name", "") for l in (j.get("locations") or [])]
            loc_text = " ".join(locs).lower()
            if not any(k in loc_text for k in _MUSE_EU):
                continue
            if any(f in title.lower() for f in remotive._FREELANCE):
                continue
            if not role_fit(title) or not seniority_ok(title):
                continue
            mode = "remote" if ("remote" in loc_text or "flexible" in loc_text) else "onsite"
            loc_display = next((l for l in locs if any(k in l.lower() for k in _MUSE_EU)),
                               locs[0] if locs else "EU")
            out.append({"company": company, "role": title,
                        "url": (j.get("refs") or {}).get("landing_page", ""),
                        "location": loc_display, "category": category_of(title),
                        "source": "The Muse", "mode": mode})
    return out


def _fetch_all() -> list[dict]:
    offers, seen_url, seen_cr = [], set(), set()
    remote = _fetch_remotive() + _fetch_jobicy() + _fetch_remoteok() + _fetch_wwr()
    for o in remote:
        o.setdefault("mode", "remote")   # everything from the remote boards is remote-workable
    for o in remote + _fetch_france_inperson() + _fetch_arbeitnow() + _fetch_themuse():
        k = _offer_key(o)
        # dedup by URL AND by normalized company|role — the same posting appears on two boards with
        # different URLs (e.g. APEC + France Travail), which the URL key alone wouldn't catch.
        cr = f"{(o.get('company') or '').strip().lower()}|{(o.get('role') or '').strip().lower()}"
        if k in seen_url or cr in seen_cr:
            continue
        seen_url.add(k)
        seen_cr.add(cr)
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


# The digest groups by SECTION (a geography-aware view of mode), each with its own cap so no bucket
# starves another — France in-person can be 100+/day and would otherwise crowd out EU-relocation roles.
_FR_SOURCES = {"apec", "francetravail", "france_travail"}
_SECTION_ORDER = {"remote": 0, "france": 1, "relocate": 2}
_SECTION_CAP = {"remote": 12, "france": 12, "relocate": 8}  # reviewable; overflow rolls to next day


def _section(o: dict) -> str:
    """Which digest section an offer belongs to: remote | france (in-person/hybrid France) |
    relocate (on-site/hybrid elsewhere in the EU — she's open to relocating). A France location wins
    the 'france' section regardless of which board surfaced it (e.g. a Paris role from The Muse)."""
    if o.get("mode", "remote") == "remote":
        return "remote"
    loc = (o.get("location") or "").lower()
    if o.get("source", "").lower() in _FR_SOURCES or "france" in loc or "paris" in loc:
        return "france"
    return "relocate"


def new_offers(min_fit: int = _FIT_FLOOR) -> list[dict]:
    """Fetched, profile+seniority-filtered offers not already shown to Zineb (fresh in cache).
    Grouped into sections (remote → France in-person → EU relocation), each capped so the digest stays
    reviewable and no section starves another; overflow stays 'unseen' and rolls into the next digest."""
    seen = _seen_load()
    now = time.time()
    out = [o for o in _fetch_all()
           if not (seen.get(_offer_key(o)) and now - seen[_offer_key(o)].get("ts", 0) < _SEEN_TTL)]

    # Score first, then drop anything below the floor — a capped digest is only as good as its
    # ordering, and this is what decides which offers survive the caps below.
    for o in out:
        o["fit"], o["why"] = fit_score(o)
    out = [o for o in out if o["fit"] >= min_fit]
    out.sort(key=lambda o: (_SECTION_ORDER.get(_section(o), 9), -o["fit"], o["company"].lower()))
    out = _cap_per_company(out)
    per_section: dict[str, int] = {}
    capped = []
    for o in out:
        s = _section(o)
        if per_section.get(s, 0) >= _SECTION_CAP.get(s, 12):
            continue
        per_section[s] = per_section.get(s, 0) + 1
        capped.append(o)
    return capped


def record_seen(offers: list[dict]) -> None:
    seen = _seen_load()
    now = time.time()
    for o in offers:
        seen[_offer_key(o)] = {"ts": now, "company": o["company"], "role": o["role"]}
    seen = {k: v for k, v in seen.items() if now - v.get("ts", 0) < _SEEN_TTL}
    _seen_save(seen)


# ── Digest formatting ─────────────────────────────────────────────────────────

_CAT_LABEL = {"ai": "AI / ML", "data": "Data", "backend": "Backend / Software"}
_SECTION_LABEL = {
    "remote": "🌍 REMOTE — workable from France (some worldwide/EU)",
    "france": "🏢 IN-PERSON / HYBRID — France (nationwide)",
    # Not "the EU": these boards file London under Europe, and post-Brexit the UK needs sponsorship.
    # Roles flagged "visa/sponsorship needed" are scored down rather than hidden.
    "relocate": "✈️ ON-SITE ABROAD — open to relocating (check visa outside the EU)",
}


def format_digest(offers: list[dict], min_fit: int = _FIT_FLOOR) -> str:
    if not offers:
        return ("No new roles matched your profile today — remote or in-person. I check daily and "
                "will email you the moment good ones appear, so you don't have to hunt.")
    from collections import Counter
    by = Counter(_section(o) for o in offers)
    bits = []
    if by.get("remote"):   bits.append(f"{by['remote']} remote")
    if by.get("france"):   bits.append(f"{by['france']} in-person (France)")
    if by.get("relocate"): bits.append(f"{by['relocate']} abroad (EU)")
    lines = [
        f"{len(offers)} new role{'s' if len(offers)!=1 else ''} that fit your profile "
        f"(AI/ML/Data/Backend) and look realistic for a strong junior — {', '.join(bits)}. "
        "Best match first in each section; the ★ score is fit, and the line under each role says "
        "why it scored that way, so you can skip the weak ones without opening them.",
        "Apply to the ones you like; reply with any you want the outreach agent to chase.",
        "",
    ]
    current = None
    for o in offers:
        sec = _section(o)
        if sec != current:
            current = sec
            lines.append("")
            lines.append(_SECTION_LABEL.get(sec, sec.title()))
            lines.append("")
        tag = _CAT_LABEL.get(o["category"], o["category"].title())
        lines.append(f"• ★{o.get('fit', 0):>3}  [{tag}] {o['role']}")
        lines.append(f"       {o['company']}  ·  {o['location']}  ·  {o['source']}")
        if o.get("why"):
            lines.append(f"       ↳ {' · '.join(o['why'])}")
        if o["url"]:
            lines.append(f"       {o['url']}")
        lines.append("")
    lines.append(f"— Your opportunity scout. Anything scoring under {min_fit} was dropped before "
                 "you saw it. These are for YOU to review; nothing was contacted.")
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="International opportunity digest for Zineb")
    ap.add_argument("--send", action="store_true", help="email the digest + record offers as seen")
    ap.add_argument("--min", type=int, default=1, help="minimum new offers to bother sending")
    ap.add_argument("--to", default=config.INTERNAL_ALERT_EMAIL, help="recipient (default: Zineb)")
    ap.add_argument("--min-fit", type=int, default=_FIT_FLOOR, dest="min_fit",
                    help=f"drop offers scoring below this fit score (default {_FIT_FLOOR}; "
                         "lower it for a wider net, raise it for a stricter digest)")
    args = ap.parse_args(argv)

    offers = new_offers(min_fit=args.min_fit)
    body = format_digest(offers, min_fit=args.min_fit)
    print(f"[opps] {len(offers)} new offer(s)\n")
    print(body)

    if not args.send:
        print("\n[opps] dry-run (no --send): nothing emailed, nothing recorded.")
        return 0
    if len(offers) < args.min:
        print(f"\n[opps] only {len(offers)} new offer(s) (< --min {args.min}) — not sending.")
        return 0

    import smtp_send
    subject = f"🎯 {len(offers)} new job match(es) — remote + in-person — {time.strftime('%d %b')}"
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
