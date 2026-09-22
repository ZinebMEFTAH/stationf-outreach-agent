"""Find an employer's applicant-tracking system, then read it.

WHY THIS EXISTS. Half of what the aggregators return does not exist any more. On 2026-09-19,
FIVE OF FIVE HelloWork rows checked against the employer's own system were already closed —
AP-HP, CDC Informatique, Septeo (SmartRecruiters: zero alternances), Younited (Lever: zero),
Hermès (Oracle: 19 alternances open, all leather-craft) — while HelloWork rendered every one of
them as live with a working "Postuler" button. Zineb opened three dead links herself, and a full
CV + letter pack was written for an AP-HP job that no longer existed.

A board page outlives the posting. The EMPLOYER'S OWN ATS does not: it is where the recruiter
closes the requisition, so it is the only honest answer to "is this real?".

The same machinery answers two questions at once, which is why they live in one module:
    verify(company, role)    — does this posting still exist?  (kills dead leads)
    postings(company)        — what does this employer have open right now?  (adds new leads)

HOW IT FINDS THE ATS. Fingerprinting, not guessing. Measured 2026-09-20: probing 19 French
software companies by name through `company_boards.probe` found exactly ONE board (~1 in 20),
whereas fingerprinting careers-page HTML for an ATS hostname found Teamtailor on five of them,
Workday on Murex and Taleo on Infotel. `company_boards.probe` stays useful once you already
suspect a slug; this module starts from the company NAME.

DEAD ENDS, MEASURED — do not spend a session re-testing these:
  · JSON-LD `validThrough` IS NOT A LIVENESS SIGNAL on aggregators. HelloWork publishes a
    JobPosting block on every page, but validThrough is mechanically datePosted + 30 days: the
    AP-HP and Septeo postings, BOTH CONFIRMED DEAD, advertise future expiry dates (2026-10-14 and
    2026-09-30). Its `employmentType` is equally unreliable — "INTERN" on all three alternances
    checked. JSON-LD is useful for DETAIL, never for "does this still exist".
  · iCIMS (AXA, Expleo) wraps its list in an iframe whose inner page answers "Please Enable
    Cookies to Continue" even with a cookie jar — the cookie is set by JavaScript.
  · Talentsoft (Docaposte, Air France) serves a shell with no job links in the HTML at all.

WHAT IT CANNOT READ, and why (probed 2026-09-20, do not re-probe without new information):
  · Taleo, SuccessFactors, iCIMS, Avature — no public JSON, and the pages are JS shells.
  · Teamtailor, Walt — client-side rendered; no job data in the HTML at all.
These cover many large French employers, which is precisely why the CFA partner channel matters:
it reaches Société Générale, BNP, AXA, Capgemini and Expleo through a person, not a parser.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import time
from pathlib import Path

import company_boards as cb
import jobsource as js

_TIMEOUT = 20
# Total seconds to spend working out ONE employer's ATS before answering "unknown".
_FP_BUDGET = 30
_CACHE = Path(__file__).parent / "cache" / "ats_fingerprints.json"

# Hostname fragments that identify a platform in a careers page's HTML. Ordered: the first match
# wins, so the specific (`myworkdayjobs`) precedes anything that could also appear incidentally.
_SIGNATURES: list[tuple[str, str]] = [
    ("myworkdayjobs", "workday"),
    ("smartrecruiters", "smartrecruiters"),
    ("greenhouse.io", "greenhouse"),
    ("api.lever.co", "lever"),
    ("jobs.lever.co", "lever"),
    ("jobs.ashbyhq", "ashby"),
    ("oraclecloud.com", "oracle"),
    ("radancy", "radancy"),
    ("phenompeople", "phenom"),
    # Identified but UNREADABLE — recorded so a caller gets an honest answer instead of silence.
    ("taleo", "taleo"),
    ("successfactors", "successfactors"),
    ("icims", "icims"),
    ("avature", "avature"),
    ("teamtailor", "teamtailor"),
    ("talentsoft", "talentsoft"),
]

READABLE = {"workday", "smartrecruiters", "greenhouse", "lever", "ashby", "oracle",
            "radancy", "phenom"}
UNREADABLE = {"taleo", "successfactors", "icims", "avature", "teamtailor", "talentsoft"}

# Careers-page URL shapes to try for a company, in order of how often they work.
_CAREERS_PATHS = ("careers", "carrieres", "fr/carrieres", "en/careers", "jobs", "nous-rejoindre",
                  "recrutement", "fr/nous-rejoindre")


def _get(url: str, as_json: bool = False):
    req = urllib.request.Request(
        url, headers={"User-Agent": js.DEFAULT_UA,
                      "Accept": "application/json" if as_json else "text/html"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body) if as_json else body


def _slug(company: str) -> str:
    s = unicodedata.normalize("NFKD", company.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s)


# --------------------------------------------------------------------------- fingerprinting

def _cache_load() -> dict:
    try:
        return json.loads(_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _cache_save(d: dict) -> None:
    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass            # a cache write must never break a run



def _sniff(html_body: str, url: str) -> dict:
    """Which ATS, if any, does this page's HTML betray?"""
    low = html_body.lower()
    for frag, platform in _SIGNATURES:
        if frag in low:
            m = re.search(r"https?://([a-z0-9.-]*" + re.escape(frag) + r"[a-z0-9.-]*)", low)
            return {"platform": platform, "host": m.group(1) if m else None, "url": url}
    return {"platform": None, "host": None, "url": None}


_JOB_LINK = re.compile(
    r'href="([^"]*(?:offre|emploi|job|vacanc|career|recrut|postul|nos-postes)[^"]*)"', re.I)


def _job_links(html_body: str, base: str) -> list[str]:
    """Links on a careers page that look like the actual job list, most specific first."""
    out, seen = [], set()
    for href in _JOB_LINK.findall(html_body):
        if href.startswith("#") or href.lower().startswith(("mailto:", "tel:", "javascript:")):
            continue
        u = urllib.parse.urljoin(base, html.unescape(href))
        if u in seen or u == base:
            continue
        seen.add(u)
        out.append(u)
    # a URL naming offers/jobs explicitly beats a generic "careers" landing link
    out.sort(key=lambda u: (0 if re.search(r"(offre|emploi|job|vacanc)", u, re.I) else 1, len(u)))
    return out


def fingerprint(company: str, careers_url: str | None = None, use_cache: bool = True) -> dict:
    """Which ATS does this employer run? -> {'platform': str|None, 'host': str|None, 'url': str}

    `platform` is one of READABLE, one of UNREADABLE, or None when nothing was detected (a
    custom or JS-only careers site). An UNREADABLE answer is still useful: it tells a caller to
    stop trying and use the CFA channel instead of burning a session on it.
    """
    key = _slug(company)
    cache = _cache_load() if use_cache else {}
    if use_cache and key in cache:
        return cache[key]

    urls = [careers_url] if careers_url else []
    if not urls:
        # ASK THE RESOLVER FIRST. Guessing "www.<slug>.com" is why the first scale test came back
        # 37-of-40 UNKNOWN (2026-09-20): it was not the ATS readers failing, it was never finding
        # the site. "Groupe SII" is sii.net, not groupesii.com; "Paris La Défense" is
        # parisladefense.com. company_resolver already solves this for the outreach pipeline.
        hosts = []
        try:
            import company_resolver as cr
            dom = cr.resolve_domain(company)
            if dom:
                hosts.append(dom)
        except Exception:
            pass                      # resolver unavailable or rate-limited: fall back to the guess
        hosts.append(f"{key}.com")
        for h in hosts:
            bare = h.removeprefix("www.")
            # BOTH spellings: resolve_domain returns "docaposte.com", whose /carrieres 404s while
            # www.docaposte.com/nous-rejoindre serves 40KB. Guessing one form loses the employer.
            for hh in (f"www.{bare}", bare):
                urls += [f"https://{hh}/{p}" for p in _CAREERS_PATHS] + [f"https://{hh}"]
            urls += [f"https://careers.{bare}", f"https://jobs.{bare}", f"https://talents.{bare}",
                     f"https://recrutement.{bare}", f"https://emploi.{bare}"]
    out = {"platform": None, "host": None, "url": None}
    # ⚠ A TIME BUDGET, OR ONE EMPLOYER EATS THE RUN. The candidate list is 2 hosts x 2 spellings
    # x 8 paths plus 5 subdomains, and every one of them costs a full _TIMEOUT when the domain
    # simply does not resolve — up to ~10 minutes on a single company. Measured while wiring
    # verification into the queue: 30 employers had produced 3 answers in several minutes.
    # Giving up is safe here because the answer it falls back to is "unknown", which never kills.
    deadline = time.time() + _FP_BUDGET
    for u in urls:
        if time.time() > deadline:
            out["note"] = "fingerprint budget exhausted"
            break
        try:
            html_body = _get(u)
        except Exception:
            continue
        out = _sniff(html_body, u)
        if out["platform"]:
            break
        # THE LANDING PAGE OFTEN DOES NOT EMBED THE ATS — the job LIST page does. Docaposte's
        # /nous-rejoindre serves 40KB with no signature at all. Follow ONE hop to the most
        # job-shaped link on the page before giving up on this host.
        for nxt in _job_links(html_body, u)[:2]:
            try:
                out = _sniff(_get(nxt), nxt)
            except Exception:
                continue
            if out["platform"]:
                break
        if out["platform"]:
            break
    if use_cache:
        cache[key] = out
        _cache_save(cache)
    return out


# --------------------------------------------------------------------------- readers

def _radancy(host: str, query: str) -> list[dict]:
    """Radancy career sites (Veolia, Sanofi).

    ⚠ THE SEARCH PARAMETER IS `keywords`. `keyword` is ACCEPTED, returns 200, and is SILENTLY
    IGNORED — you get the same unfiltered first page every time, which reads exactly like "the
    posting is gone". That cost one wrong "Veolia is dead" conclusion.
    ⚠ Titles live in the anchor's href SLUG, not in a clean field.
    """
    qs = urllib.parse.urlencode({
        "ActiveFacetID": 0, "CurrentPage": 1, "RecordsPerPage": 50, "Distance": 50,
        "SearchResultsModuleName": "Search Results", "SearchFiltersModuleName": "Search Filters",
        "sortCriteria": "score", "keywords": query})
    d = _get(f"https://{host}/search-jobs/results?{qs}", as_json=True)
    out, seen = [], set()      # each card carries TWO anchors (image + title) -> dedupe by URL
    for href, inner in re.findall(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                                  d.get("results", "") or "", re.S):
        if "/emploi/" not in href and "/job/" not in href:
            continue
        txt = html.unescape(" ".join(re.sub(r"<[^>]+>", " ", inner).split()))
        # The anchor text is often a generic call to action ("View job offer"), and the real
        # title is in the href slug. Prefer the slug whenever the text carries no information.
        if not txt or len(txt) < 12 or re.fullmatch(r"(view|voir)[^a-z]*(job|offer|offre).*", txt, re.I):
            txt = href.rsplit("/", 3)[1].replace("-", " ")
        if href in seen:
            continue
        seen.add(href)
        out.append({"role": txt, "url": f"https://{host}{href}"})
    return out


def _oracle(host: str, query: str, site: str = "CX_1") -> list[dict]:
    """Oracle Recruiting Cloud (Hermès, Technip Energies). The host appears in the careers HTML."""
    url = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
           f"?onlyData=true&expand=requisitionList&finder=findReqs;siteNumber={site},"
           f"keyword={urllib.parse.quote(query)},limit=100")
    d = _get(url, as_json=True)
    items = (d.get("items") or [{}])[0]
    return [{"role": r.get("Title") or "", "url": "", "location": r.get("PrimaryLocation") or "",
             "id": r.get("Id")} for r in (items.get("requisitionList") or [])]


def postings(company: str, query: str = "alternance", careers_url: str | None = None) -> dict:
    """Everything this employer has open matching `query`, read from their own ATS.

    -> {'platform': str|None, 'readable': bool, 'rows': [{'role','url',...}], 'note': str}
    Never raises: an unreachable board returns rows=[] with a note, because a bookkeeping failure
    must not stop the search.
    """
    fp = fingerprint(company, careers_url)
    platform, host = fp["platform"], fp["host"]
    res = {"platform": platform, "readable": platform in READABLE, "rows": [], "note": ""}
    if not platform:
        res["note"] = "no ATS signature on the careers page (custom or JS-only site)"
        return res
    if platform in UNREADABLE:
        res["note"] = f"{platform} has no public JSON — use the CFA channel for this employer"
        return res
    try:
        if platform == "smartrecruiters":
            for slug in (_slug(company), _slug(company) + "group", company.replace(" ", "")):
                rows = cb._smartrecruiters(slug)
                if rows:
                    res["rows"] = rows
                    res["note"] = f"smartrecruiters slug={slug}"
                    break
        elif platform in ("greenhouse", "lever", "ashby"):
            res["rows"] = getattr(cb, f"_{platform}")(_slug(company))
        elif platform == "radancy":
            # ⚠ The fingerprint host is the CDN (cdn.radancy.eu); the SEARCH endpoint lives on the
            # careers site's own host (jobs.veolia.com). Use the page we fingerprinted.
            res["rows"] = _radancy(urllib.parse.urlparse(fp["url"]).netloc, query)
        elif platform == "oracle" and host:
            res["rows"] = _oracle(host, query)
        elif platform == "phenom" and fp["url"]:
            res["rows"] = cb._phenom(fp["url"].split("//", 1)[-1])
        elif platform == "workday":
            res["note"] = "workday tenant cannot be guessed — use company_boards.probe_workday"
    except Exception as e:  # noqa: BLE001
        res["note"] = f"{platform} unreachable: {type(e).__name__}"
    return res


def _norm(text: str) -> list[str]:
    """Accent-free, punctuation-free tokens. Both sides of every comparison go through this.

    The accents are the whole point: the first version compared a normalised query token
    "creation" against the live title "Apprenti Création d'agents LLM" and reported GONE for a
    posting Zineb had just applied to.
    """
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return [w for w in re.split(r"[^a-z0-9]+", t) if len(w) > 2]


# Words that appear in half the alternance postings in France and so identify nothing.
_STOP = {"alternance", "alternant", "alternante", "apprenti", "apprentie", "stage", "stagiaire",
         "les", "des", "une", "pour", "and", "the", "ingenieur", "ingenieure", "junior"}


_VERDICTS = Path(__file__).parent / "cache" / "ats_verdicts.json"
# TTL BY VERDICT, because they decay at different rates. A closed requisition does not reopen,
# so "gone" keeps; "live" is a claim about today and must be re-earned; "unknown" describes the
# EMPLOYER'S ATS rather than the posting, and only changes when a site is replatformed.
_VERDICT_TTL = {"gone": 7 * 86400, "live": 86400, "unknown": 3 * 86400}


def _verdict_cache() -> dict:
    try:
        return json.loads(_VERDICTS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _verdict_key(company: str, role: str) -> str:
    return f"{(company or '').strip().lower()}|{(role or '').strip().lower()}"


def _verdict_get(company: str, role: str):
    e = _verdict_cache().get(_verdict_key(company, role))
    if not e:
        return None
    if time.time() - e.get("ts", 0) > _VERDICT_TTL.get(e.get("verdict"), 86400):
        return None
    return {k: v for k, v in e.items() if k != "ts"}


def _verdict_put(company: str, role: str, got: dict) -> None:
    try:
        d = _verdict_cache()
        d[_verdict_key(company, role)] = {**got, "ts": time.time()}
        _VERDICTS.parent.mkdir(parents=True, exist_ok=True)
        _VERDICTS.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass                                   # a cache write must never break a run


def board_says_gone(url: str) -> bool:
    """Does the BOARD's own page admit the posting is closed?

    Aggregators outlive their postings — that is the standing warning in CLAUDE.md, and it has
    now cost two packs in one session. But HelloWork is not completely silent about it: when a
    posting expires it STOPS EMITTING JobPosting structured data and drops the Postuler button,
    leaving "cette offre n'est plus disponible" in the body.

    Measured 2026-09-21 on a posting Zineb confirmed closed (Groupe SII, 81417977): no
    JobPosting block, no apply button, "plus disponible" present. A live row on the same board
    (Docaset 81298068, H3 Campus 78400063) carries all three.

    ⚠ TRUE MEANS GONE; FALSE MEANS "THE BOARD IS NOT ADMITTING IT", never "alive". A board that
    renders a dead posting perfectly is exactly the failure this repo keeps paying for, so this
    can only ever ADD a death signal, never certify life.
    """
    try:
        import descriptions as _d
        page = _d._get(url)
    except Exception:                                             # noqa: BLE001
        return False
    if not page:
        return False
    import re as _re
    has_posting = bool(_re.search(r"JobPosting", page))
    says_gone = bool(_re.search(r"n['’]est plus disponible|plus disponible|offre expir[ée]|"
                                r"pourvue|cl[oô]tur[ée]e", page, _re.I))
    return says_gone and not has_posting


def verify(company: str, role: str, careers_url: str | None = None,
           use_cache: bool = True) -> dict:
    """Is this posting still open on the employer's own system?

    -> {'verdict': 'live'|'gone'|'unknown', 'platform', 'matched', 'score', 'note'}

    'unknown' is a FIRST-CLASS ANSWER and must never be read as 'gone': most large French
    employers run an ATS this repo cannot read, and calling those dead would discard exactly the
    employers who carry the alternance market.

    BIASED TOWARDS 'live' ON PURPOSE. A false 'gone' silently deletes a real opportunity, which is
    the expensive error; a false 'live' only costs one click. So the match is a token-overlap
    score over DISTINCTIVE words, not an equality test, and the threshold is deliberately low.
    """
    if use_cache:
        hit = _verdict_get(company, role)
        if hit:
            return {**hit, "cached": True}
    want = [w for w in _norm(role) if w not in _STOP]
    # Query the board on the two most distinctive words, falling back to the contract word.
    got = postings(company, query=" ".join(want[:2]) or "alternance", careers_url=careers_url)
    if not got["readable"]:
        out = {"verdict": "unknown", "platform": got["platform"], "matched": None,
               "score": 0.0, "note": got["note"]}
        _verdict_put(company, role, out)
        return out
    if not got["rows"]:
        # An empty board is evidence, but a weak one: the query may simply not be how this ATS
        # indexes the title. Re-ask with the contract word before concluding anything.
        got = postings(company, query="alternance", careers_url=careers_url)
    # ⚠ A MATCH MUST AGREE ABOUT THE CONTRACT. The score is token overlap over distinctive
    # words, and "alternance"/"stage" are deliberately stopped out of it — which means
    # "STAGE - Assistant Data Manager" scored 0.67 against "Alternance Assistant Data Scientist"
    # and reported the posting LIVE. Hermès's own board had 48 alternances and no data scientist
    # among them; the posting was closed, and a pack was about to be built for it. A row whose
    # contract EXPLICITLY differs can never be the posting we are asking about.
    def _kind(text: str) -> str | None:
        if re.search(r"\balternan\w*\b|\bapprenti\w*\b", text or "", re.I):
            return "alternance"
        if re.search(r"\bstages?\b|\bstagiaire\b|\bintern(ship)?\b|\bCDI\b|\bCDD\b|\bVIE\b",
                     text or "", re.I):
            return "other"
        return None

    want_kind = _kind(role)
    best, best_score = None, 0.0
    for r in got["rows"]:
        have_kind = _kind(r.get("role") or "")
        if want_kind and have_kind and want_kind != have_kind:
            continue
        have = set(_norm(r.get("role") or ""))
        if not have or not want:
            continue
        score = len(set(want) & have) / len(set(want))
        if score > best_score:
            best, best_score = (r.get("role") or ""), score
    verdict = "live" if best_score >= 0.45 else "gone"
    out = {"verdict": verdict, "platform": got["platform"],
           "matched": best if best_score else None, "score": round(best_score, 2),
           "note": got["note"] or f"{len(got['rows'])} postings read"}
    _verdict_put(company, role, out)
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "verify":
        print(json.dumps(verify(sys.argv[2], " ".join(sys.argv[3:])), ensure_ascii=False, indent=1))
    elif len(sys.argv) >= 2 and sys.argv[1] == "fingerprint":
        for c in sys.argv[2:]:
            print(f"{c:24} {fingerprint(c)}")
    else:
        for c in sys.argv[1:] or ["Veolia"]:
            got = postings(c)
            print(f"{c}: platform={got['platform']} readable={got['readable']} "
                  f"rows={len(got['rows'])} {got['note']}")
