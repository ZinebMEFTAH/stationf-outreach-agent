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
(remote|hybrid|onsite) and deduped by URL AND normalized company|role (same posting on two boards).
Survivors are then SCORED for fit (fit_score) — anything below _FIT_FLOOR is dropped outright, and
the digest spends a fixed budget (_DIGEST_CAP) on the best of the rest: each section is guaranteed a
floor (_SECTION_MIN) so none starves, then remaining slots go to the highest scores wherever they
are. Overflow rolls into following days via the seen-cache. Extensible — add fetchers to _fetch_all().

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
    r"engineer|machine learning|\bml\b|\bai\b|artificial intelligence|\bdata|mlops|nlp|"
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
    r"casino|gambling|betting|sportsbook|"
    # French non-engineering titles. Every term above is English, so APEC and France Travail —
    # the two boards that actually supply in-person Paris alternance — were filtered by nothing:
    # "Formateur Référent ML + Finances Publiques" reached the digest scored 52 as an AI/ML role.
    r"formateur|formatrice|enseignant|professeur|p[ée]dagogique|"
    r"commercial|commerciale|vendeur|vendeuse|caissier|serveur|livreur|magasinier|"
    r"recrutement|ressources humaines|comptable|comptabilit[ée]|juridique|paie|"
    r"chef de projet|chef de produit|charg[ée] d'affaires|charg[ée] de client[èe]le|"
    r"technicien|technicienne|assistant administratif|secr[ée]taire|"
    r"cybers[ée]curit[ée]|infographiste|int[ée]grateur)\b", re.I)
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
    if re.search(r"\bdata", t):  # \b so "OData" is not a Data role
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
    # Department code, either side of the dash: France Travail writes "93 - Saint-Ouen",
    # APEC writes "Saint-Ouen - 93". Only the first form was matched, so every APEC posting
    # in the Paris region was scored — and labelled in the digest — "elsewhere in France".
    r"\b(7[58]|9[12345]|77)\s*-|-\s*(7[58]|9[12345]|77)\b", re.I)

# "Île-de-France or close." Close means a daily commute she would actually make: roughly 1h15 door
# to door by train from Paris. These departments ring IDF and their préfectures are all inside that
# — Compiègne 45min (60), Reims 45min (51), Chartres and Orléans and Évreux and Sens ~1h (28/45/27/
# 89), Rouen and Amiens ~1h15 (76/80), Soissons 1h20 (02). Kept, but below a role in Paris itself:
# an hour and a quarter each way is real, and it is not the same offer as one in the 11e.
_NEAR_IDF = re.compile(
    r"\b(compi[èe]gne|creil|beauvais|senlis|chantilly|reims|ch[âa]lons[- ]en[- ]champagne|"
    r"chartres|dreux|orl[ée]ans|[ée]vreux|vernon|sens|auxerre|rouen|amiens|soissons|"
    r"saint[- ]quentin|laon|[ée]pernay)\b|"
    r"\b(60|27|28|45|02|51|76|80|89)\s*-|-\s*(60|27|28|45|02|51|76|80|89)\b", re.I)

# ON-SITE outside that ring is not an opportunity for her, it is a move. She is starting an M1 in
# Île-de-France in September 2026, so a job that requires being in Béziers or Berlin on Monday is
# unactionable however good it is — and every slot one occupies in a capped digest is taken from a
# role she could actually accept. Remote is the exact opposite: location stops mattering, so remote
# is welcome from anywhere. Flip this to True only if she is genuinely willing to relocate.
ALLOW_ONSITE_ABROAD = False


def is_reachable(offer: dict) -> tuple[bool, str]:
    """Could she actually take this job? (ok, reason).

    Remote → yes, wherever it is. In-person/hybrid → only Île-de-France or the commuter ring
    (_NEAR_IDF). Everything else in-person is dropped before scoring: it cannot be accepted, so
    it must not compete for a slot in the digest.
    """
    if (offer.get("mode") or "remote") == "remote":
        return True, "remote — location does not matter"
    blob = f"{offer.get('location') or ''} {offer.get('role') or ''}"
    if _IDF.search(blob):
        return True, "Île-de-France"
    if _NEAR_IDF.search(blob):
        return True, "commuter ring (~1h from Paris)"
    if ALLOW_ONSITE_ABROAD:
        return True, "on-site, relocation"
    return False, "on-site outside Île-de-France — she cannot commute to it"


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
# Alternance postings that explicitly target a Bac+2/+3. She's entering an M1, so these pay less,
# teach her less, and often can't legally take a Master-level apprentice — down-ranked, not dropped.
_BELOW_LEVEL = re.compile(r"\b(bts|dut|\bbut\b|bac\s*\+\s*[23]|licence pro(fessionnelle)?)\b", re.I)

# ESN / SSII bodyshops. Not excluded — they hire juniors and she may well want them — but they
# flood APEC and would otherwise fill every slot, so they yield to product companies on a tie.
_ESN = {
    "accenture", "atos", "capgemini", "sopra steria", "sopra", "cgi", "akkodis", "alten",
    "expleo", "devoteam", "talan", "onepoint", "davidson consulting", "davidson", "inetum",
    "sii", "assystem", "altran", "segula", "ausy", "modis", "econocom", "umanis", "keyrus",
    "micropole", "cgilanum", "alliance concept informatique", "exalt", "exalt lyon",
}
# Recruitment agencies. Not an employer either — the posting hides who she would work for, so
# she cannot research the company, and the same role is usually posted direct elsewhere.
# Down-ranked like an ESN rather than dropped: some of these do place juniors.
_AGENCIES = {
    "nextgen rh", "rhselect", "lynx rh", "fed it", "fed group", "hays", "michael page",
    "page personnel", "expectra", "randstad", "manpower", "adecco", "proman", "crit",
    "synergie", "walters people", "robert half", "robert walters", "gi group", "kelly services",
    "aston carter", "ltd international", "approach people", "harry hope", "silkhom",
}
# Job-board names that leak into the company field as if they were the employer — plus the
# alternance SCHOOLS (ISCOD & co) that mass-post ads to recruit students into their own
# programme. ISCOD alone supplied the two top-scored lines of the 2026-09-05 digest, both for
# towns 800 km from Paris: the "employer" is a course, and the role is bait.
_JUNK_COMPANIES = {"hellowork", "apec", "france travail", "francetravail", "pole emploi",
                   "pôle emploi", "indeed", "linkedin", "welcome to the jungle", "glassdoor",
                   "iscod", "studi", "walt", "openclassrooms", "mbway", "digital campus",
                   "ifocop", "cfa", "esupcom", "isefac", "ipac bachelor factory"}


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
    elif _NEAR_IDF.search(blob):
        # Reachable, but ~1h15 each way. Ranks below a role in Paris itself and above nothing
        # else: is_reachable() has already dropped every in-person job further out, so this is
        # the last in-person tier that exists.
        score += 6
        why.append("commuter ring — ~1h from Paris")
    elif _NON_EU.search(blob):
        why.append("visa/sponsorship needed")
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
    if _BELOW_LEVEL.search(blob):
        score -= 15
        why.append("aimed below Master level")

    if company in _ESN or any(company.startswith(e + " ") for e in _ESN):
        score -= 12
        why.append("ESN/consultancy")
    elif company in _AGENCIES or any(company.startswith(a + " ") for a in _AGENCIES):
        score -= 10
        why.append("recruitment agency — employer undisclosed")

    return max(0, min(100, score)), why


# ── Link liveness ────────────────────────────────────────────────────────────
# A digest line is only worth reading if the link opens. Job boards keep listing postings in their
# API for days after the page is gone, so a dead link is not an edge case — it is the normal end of
# every posting's life, and it costs her the one thing the digest is meant to save: the click.
#
# FAIL-OPEN by design. Only an explicit 404/410 (the server saying "this is gone") drops an offer.
# A timeout, a refused connection, a 403 from a bot-blocking WAF or any 5xx keeps it: those say
# something about the network or the scraper, not about the job, and a digest that silently empties
# itself the day her VM has a bad DNS resolver would be far worse than one with a stale link in it.
_LINK_CACHE = Path(__file__).parent / "cache" / "link_check.json"
_LINK_TTL = 2 * 24 * 3600      # a link live yesterday is almost certainly live today
_LINK_DEAD = {404, 410}
_LINK_WORKERS = 8


def _link_cache_load() -> dict:
    try:
        return json.loads(_LINK_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _path_segments(url: str) -> list[str]:
    import urllib.parse
    return [p for p in urllib.parse.urlsplit(url).path.split("/") if p]


def link_ok(url: str, timeout: int = 10) -> tuple[bool, str]:
    """(alive, reason) for one posting URL.

    A status code is not enough, because the boards disagree about what "gone" looks like:
      * France Travail returns a clean 404 — the easy case.
      * WeWorkRemotely returns 200 and quietly REDIRECTS to its homepage, so the status says the
        link works while the click lands her nowhere near the job.
      * APEC serves the identical 12,163-byte SPA shell for a live offer and a made-up id, so no
        HTTP-level signal distinguishes them at all. Those are covered structurally instead: every
        offer in a digest was pulled from that board's feed in the SAME run, so an APEC posting is
        live by construction and this check is only guarding the URL itself.

    So: an explicit removal (404/410) is dead, and a redirect that DROPS path depth is dead — that
    is a board bouncing a dead posting to its listing or home page. Everything else is kept,
    deliberately: a timeout, a refused connection, a 403 from a bot-blocking WAF or any 5xx says
    something about the network, not about the job, and a digest that empties itself the day her VM
    gets a bad DNS resolver would be far worse than one carrying a stale link.
    """
    import urllib.error
    import urllib.request
    if not url or not url.startswith(("http://", "https://")):
        return False, "no url"
    want = _path_segments(url)
    req = urllib.request.Request(url, headers={"User-Agent": js.DEFAULT_UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(2048)                      # enough to complete the request; we want the headers
            got = _path_segments(r.url)
            if want and len(got) < len(want):
                return False, f"redirected to /{'/'.join(got)} — posting removed"
            return True, f"{r.status} ok"
    except urllib.error.HTTPError as e:
        if e.code in _LINK_DEAD:
            return False, f"{e.code} — posting removed"
        return True, f"{e.code} (kept — not a removal)"
    except Exception as e:  # noqa: BLE001
        return True, f"unreachable ({type(e).__name__}) — kept"


def check_links(offers: list[dict]) -> list[dict]:
    """Drop offers whose posting is provably gone. Checked in parallel — the digest runs on a cron
    and a serial pass over ~45 candidates would spend a minute waiting on sockets."""
    from concurrent.futures import ThreadPoolExecutor

    cache = _link_cache_load()
    now = time.time()
    todo = [o for o in offers
            if not (cache.get(o.get("url", "")) and now - cache[o["url"]].get("ts", 0) < _LINK_TTL)]
    if todo:
        with ThreadPoolExecutor(max_workers=_LINK_WORKERS) as pool:
            for o, (ok, why) in zip(todo, pool.map(lambda x: link_ok(x.get("url", "")), todo)):
                cache[o.get("url", "")] = {"ts": now, "ok": ok, "why": why}
    cache = {k: v for k, v in cache.items() if now - v.get("ts", 0) < _LINK_TTL}
    try:
        _LINK_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _LINK_CACHE.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    except Exception:
        pass

    alive, dead = [], 0
    for o in offers:
        rec = cache.get(o.get("url", ""))
        if rec and not rec.get("ok", True):
            dead += 1
            continue
        alive.append(o)
    if dead:
        print(f"[opps] {dead} dead link(s) dropped (404/410 — posting removed)", file=sys.stderr)
    return alive


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
    # La Bonne Alternance is the state-run alternance API. It was feeding the OUTREACH pipeline
    # but not this digest, so the one board dedicated to the contract type she most needs was the
    # one board she never saw. Its "hidden market" recruiter rows are skipped below — they carry no
    # real posting, so they're a lead for the agent to pitch, not something she can apply to.
    # Every French board in the repo that discovers without a browser. WTTJ and Free-Work were
    # already feeding the OUTREACH pipeline but not this digest — WTTJ in particular is where the
    # Paris product startups post, exactly the employers she wants — so two of the best French
    # sources were being scraped daily and never shown to her. (HelloWork is deliberately absent:
    # its discover() needs a live Playwright page, and this job is pure Python on a cron.)
    for name in ("apec", "france_travail", "labonnealternance", "wttj", "free_work"):
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
            if title.startswith("[Suggested]"):
                continue  # a recruiter flagged as hiring, with no posting — nothing for her to apply to
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


def _fetch_company_boards() -> list[dict]:
    """Openings read straight from employers' OWN careers sites (company_boards.py).

    Every other source here is an aggregator, and an aggregator only ever shows what a company
    chose to syndicate. A large employer's real list lives on its own site and is frequently
    nowhere else — GE HealthCare, where she has a warm contact, had 281 "data" postings behind
    careers.gehealthcare.com that no board in this repo could see. These are also the highest-value
    entries in the digest: applying through a company's own site puts her in its ATS rather than in
    an aggregator's forwarding queue.
    """
    import company_boards
    out = []
    for o in company_boards.fetch():
        title = o.get("role") or ""
        if not role_fit(title) or not seniority_ok(title):
            continue
        out.append({"company": o["company"], "role": title, "url": o["url"],
                    "location": o.get("location") or "France",
                    "category": category_of(title),
                    "source": f"{o['company']} careers", "mode": o.get("mode") or "onsite"})
    return out


def _fetch_all() -> list[dict]:
    offers, seen_url, seen_cr = [], set(), set()
    remote = _fetch_remotive() + _fetch_jobicy() + _fetch_remoteok() + _fetch_wwr()
    for o in remote:
        o.setdefault("mode", "remote")   # everything from the remote boards is remote-workable
    for o in (remote + _fetch_france_inperson() + _fetch_company_boards()
              + _fetch_arbeitnow() + _fetch_themuse()):
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
_FR_SOURCES = {"apec", "francetravail", "france_travail", "labonnealternance",
               "welcometothejungle", "wttj", "free-work", "freework", "free_work"}
_SECTION_ORDER = {"remote": 0, "france": 1, "relocate": 2}

# Budget, not fixed quotas. Fixed per-section caps (12/12/8) went wrong the moment the alternance
# sources landed: France had 140 offers above the floor and showed 12, cutting 128 whose best scored
# 76 — as good as what she was reading — while remote and relocate left 18 of their 20 slots empty.
# The seen-cache means a cut offer only rolls to a later digest, which for alternance is the same as
# losing it: postings fill in days, and at 12/day a queue of 140 takes a fortnight to drain.
# So: each section is guaranteed a floor (a flood in one can't erase the others), then the remaining
# budget is filled purely by fit score, wherever the best offers happen to be.
_DIGEST_CAP = 30
# 'relocate' keeps no reserved floor: is_reachable() drops on-site-abroad before selection, so
# reserving slots for a section that is normally empty would only shrink the usable budget.
_SECTION_MIN = {"remote": 5, "france": 8, "relocate": 0}


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


def new_offers(min_fit: int = _FIT_FLOOR, max_offers: int = _DIGEST_CAP) -> list[dict]:
    """Fetched, profile+seniority-filtered offers not already shown to Zineb (fresh in cache).
    Grouped into sections (remote → France in-person → EU relocation), each capped so the digest stays
    reviewable and no section starves another; overflow stays 'unseen' and rolls into the next digest."""
    seen = _seen_load()
    now = time.time()
    out = [o for o in _fetch_all()
           if not (seen.get(_offer_key(o)) and now - seen[_offer_key(o)].get("ts", 0) < _SEEN_TTL)]

    # Can she actually take it? An in-person job outside the Paris commuter ring is not an
    # opportunity, it is a relocation — she starts an M1 in Île-de-France in September 2026 — and
    # in a capped digest every slot one of those holds is taken from a role she could accept.
    # Dropped BEFORE scoring, so a high fit score cannot buy an unreachable job a place.
    before = len(out)
    out = [o for o in out if is_reachable(o)[0]]
    if before != len(out):
        print(f"[opps] {before - len(out)} unreachable (on-site outside IDF + ring) dropped",
              file=sys.stderr)

    # Score first, then drop anything below the floor — a capped digest is only as good as its
    # ordering, and this is what decides which offers survive the caps below.
    for o in out:
        o["fit"], o["why"] = fit_score(o)
    out = [o for o in out if o["fit"] >= min_fit]
    out.sort(key=lambda o: (_SECTION_ORDER.get(_section(o), 9), -o["fit"], o["company"].lower()))
    out = _cap_per_company(out)

    # Verify links on a shortlist BEFORE selecting, not after: a dead posting must be replaced by
    # the next best offer, not just deleted, or a bad link day quietly shrinks the digest. The
    # shortlist is bounded (checking all ~500 candidates would hammer the boards for nothing) and
    # generous enough that the dead ones can be backfilled.
    by_score = sorted(out, key=lambda o: -o["fit"])
    shortlist = check_links(by_score[:int(max_offers * 2.5)])
    by_score = shortlist + by_score[int(max_offers * 2.5):]
    chosen: list[dict] = []
    picked = {id(o): False for o in out}
    per_section: dict[str, int] = {}
    for o in by_score:
        s = _section(o)
        if per_section.get(s, 0) < _SECTION_MIN.get(s, 0) and len(chosen) < max_offers:
            per_section[s] = per_section.get(s, 0) + 1
            picked[id(o)] = True
            chosen.append(o)

    # Pass 2 — spend what's left of the budget on the best offers anywhere.
    for o in by_score:
        if len(chosen) >= max_offers:
            break
        if not picked[id(o)]:
            picked[id(o)] = True
            chosen.append(o)

    # Restore reading order (section, then score) — pass 1/2 selected, they didn't sort.
    chosen.sort(key=lambda o: (_SECTION_ORDER.get(_section(o), 9), -o["fit"], o["company"].lower()))
    return chosen


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
    "france": "🏢 IN-PERSON / HYBRID — Île-de-France + ~1h commuter ring",
    # Only reachable if ALLOW_ONSITE_ABROAD is flipped on; is_reachable() drops these by default.
    "relocate": "✈️ ON-SITE ABROAD — requires relocating",
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
        "Apply to the ones you like. To have the outreach agent chase one for you, reply with its "
        "link pasted into your own text at the top — links inside the quoted digest below your "
        "reply are ignored, so forwarding this back untouched asks for nothing.",
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
    ap.add_argument("--max", type=int, default=_DIGEST_CAP, dest="max_offers",
                    help=f"most offers to include in one digest (default {_DIGEST_CAP})")
    args = ap.parse_args(argv)

    offers = new_offers(min_fit=args.min_fit, max_offers=args.max_offers)
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
