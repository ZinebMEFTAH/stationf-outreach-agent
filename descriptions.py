"""Get a job's real description, and pull the facts out of it.

WHY. The pipeline found 117 alternances a day and then stopped: deciding whether any of them was
worth applying to meant a human opening each posting and reading it. That was the binding
constraint on how many applications Zineb could send — not the number of sources.

It also cost accuracy. A title is five words with no agreed vocabulary behind it, and reading the
description was the ONLY thing that separated these, all met in one week:
  · Docaposte "Alternant IA Générative & Automatisation"  -> prompt libraries and adoption workshops
  · Albioma  "Alternant-e IA générative"                  -> administering ChatGPT Enterprise
  · Technip  "Apprenticeship Data Analyst"                -> HR reporting on Power BI and VBA
  · Hermès   "Assistant Data Scientist"                   -> required analytical chemistry
  · Veolia   "Apprenti Création d'agents LLM"             -> genuinely building agents
Every one of them passes a keyword filter on its title.

CHEAPER THAN IT LOOKS. Three sources already return the full description inside the search
response the digest ALREADY MAKES, and the repo was discarding it:
    France Travail  `description`      ~2 100 chars   free
    Free-Work       `description`      ~2 900 chars   free
    Remotive        `description`      ~6 900 chars   free
Two more carry only a teaser, so they need the posting itself:
    APEC            `texteOffre`          ~280 chars  TRUNCATED
    Adzuna          `description`         ~500 chars  TRUNCATED
Two need one fetch each, both without auth:
    LinkedIn        /jobs-guest/jobs/api/jobPosting/<id>
    HelloWork       JobPosting JSON-LD on the page
And WTTJ serves an anti-bot shell to everything, so its descriptions are out of reach.

⚠ `facts["stack"]` IS EVIDENCE, NOT A VERDICT, and one measured case says why. Some employers
paste a catalogue of every technology they recruit for into each posting: a Remotive "Senior AI
Engineer" carried "React & Python, React & Golang, Golang, React & Java, Ruby, PHP & Vue…", so
the extractor honestly reported php, java, node and go for a role that uses none of them.
A DEAD END, measured rather than assumed: over 159 real descriptions, a "too many technologies
in one sentence" rule cannot separate them — the densest legitimate sentence found was a real
Data Engineer stack ("Scala, Python, SQL, Spark, Kafka, dbt, Snowflake, Databricks, BigQuery",
7 hits) and the contaminated one scored the same. So no heuristic is applied; the brief step
reads the text, and that is what the split between code and judgement is for.

⚠ USE JSON-LD FOR TEXT, NEVER FOR LIVENESS. HelloWork publishes `validThrough` as datePosted+30d
mechanically: AP-HP and Septeo, both confirmed dead, advertised future expiry dates. Only the
employer's own ATS answers "does this still exist" — see ats.verify().
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import jobsource as js

_TIMEOUT = 20
_MIN_USEFUL = 400          # below this, treat the text as a teaser rather than a description


# A board's teaser ends mid-sentence with an ellipsis; a real description does not.
_TRUNCATED_TAIL = re.compile(r"(?:\.\.\.|…|\[\s*\.\.\.\s*\])\s*$")


def _error_origin(exc: Exception) -> str:
    """Say WHICH failure this was — a 404 and a 429 mean opposite things.

    ⚠ They used to collapse into one opaque "error:HTTPError". A 404 is real signal (the
    posting is gone); a 429 or 403 means WE are throttled or blocked and the posting may be
    perfectly alive. Conflating them is the failure shape this repo keeps getting bitten by:
    a batch that comes back entirely "error" reads as "these jobs are all dead" when it
    actually means the reading stopped. LinkedIn is the biggest source here and the one most
    likely to throttle, so the distinction has to survive into what the caller sees.
    """
    code = getattr(exc, "code", None)
    if code in (404, 410):
        return "gone"
    if code == 429:
        return "throttled"
    if code in (401, 403):
        return "blocked"
    if isinstance(code, int) and code >= 500:
        return "server_error"
    return "unreachable"


def _decompress(raw: bytes, encoding: str) -> bytes:
    """Undo Content-Encoding. Some servers compress even when asked for `identity`.

    ⚠ APEC does exactly that, and without this the compressed bytes were decoded with
    errors="replace" into 5,121 characters of mojibake that `fetch` then reported as a clean
    description — same length for every offer, `truncated: False`. A confident wrong answer is
    worse than no answer, because it stops the caller looking anywhere else.
    """
    enc = (encoding or "").lower()
    try:
        if enc == "gzip" or raw[:2] == b"\x1f\x8b":
            import gzip
            return gzip.decompress(raw)
        if enc == "deflate":
            import zlib
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
        if enc == "br":
            try:
                import brotli                                   # optional dependency
            except ImportError:
                return b""                                      # unreadable is not garbage
            return brotli.decompress(raw)
    except Exception:                                           # noqa: BLE001
        return b""
    return raw


# PACE EACH HOST. Reading a few hundred postings in a tight loop is what a scraper looks like,
# and on the first full queue run LinkedIn returned 45 throttles - her single biggest source,
# silently unread. A per-host minimum gap costs seconds across a run and keeps the channel.
_HOST_GAP = {"linkedin.com": 1.5}
_HOST_GAP_DEFAULT = 0.25
_last_hit: dict[str, float] = {}


def _pace(url: str) -> None:
    host = urllib.parse.urlparse(url).netloc.lower()
    gap = next((g for h, g in _HOST_GAP.items() if host.endswith(h)), _HOST_GAP_DEFAULT)
    wait = gap - (time.time() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.time()


def _get(url: str) -> str:
    _pace(url)
    req = urllib.request.Request(url, headers={"User-Agent": js.DEFAULT_UA,
                                               "Accept": "text/html,application/json",
                                               "Accept-Encoding": "gzip, identity"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        raw = _decompress(r.read(), r.headers.get("Content-Encoding", ""))
    text = raw.decode("utf-8", "replace")
    # Belt and braces: if what came back is still mostly undecodable, say so rather than hand
    # back replacement characters that look like content to every length check downstream.
    if text and text.count("\ufffd") > len(text) * 0.02:
        return ""
    return text


def clean(raw: str) -> str:
    """HTML (or HTML-in-JSON) to readable text, with paragraph breaks preserved."""
    if not raw:
        return ""
    s = re.sub(r"(?i)<(br|/p|/li|/div|/h[1-6])[^>]*>", "\n", raw)
    s = re.sub(r"(?i)<li[^>]*>", "\n• ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t ]+", " ", s)
    return re.sub(r"\n\s*\n\s*", "\n\n", s).strip()


# --------------------------------------------------------------------------- per-source readers

def _jsonld_nodes(doc):
    """Every object inside a JSON-LD document, however it is nested.

    ⚠ TWO SHAPES THE FIRST VERSION MISSED, and both are the schema.org norm rather than an edge
    case: `"@type": ["JobPosting"]` (a LIST, because a node may declare several types) and the
    `@graph` wrapper that most CMS plugins emit. Each returned "" here, which since the
    page-text fallback was tightened means the description was lost outright.
    """
    if isinstance(doc, list):
        for x in doc:
            yield from _jsonld_nodes(x)
    elif isinstance(doc, dict):
        yield doc
        for key in ("@graph", "itemListElement", "mainEntity"):
            if key in doc:
                yield from _jsonld_nodes(doc[key])


def _jsonld_description(page: str) -> str:
    """The JobPosting description embedded for Google-for-Jobs. Text only — never liveness."""
    for blk in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            d = json.loads(blk)
        except Exception:
            continue
        for it in _jsonld_nodes(d):
            types = it.get("@type")
            types = types if isinstance(types, list) else [types]
            if "JobPosting" in types and it.get("description"):
                return clean(it["description"])
    return ""


_LI_ID = re.compile(r"(?:jobs/view/|currentJobId=|-)(\d{8,})")


# Where LinkedIn's own UI starts. The body capture runs past the description into the panel
# below it, which is ~250-340 characters of chrome on every posting — and it carries
# "Employment type: Full-time", which LinkedIn stamps on ALTERNANCE postings. Handing that to
# a reader as part of the employer's own words is worse than the wasted characters.
_LI_CHROME = re.compile(r"\n\s*Show more\s*\n|\n\s*Seniority level\s*\n|"
                        r"Referrals increase your chances|\n\s*Employment type\s*\n", re.I)


def _li_trim(t: str) -> str:
    m = _LI_CHROME.search(t or "")
    return (t[:m.start()] if m else t).strip()


def _linkedin(url: str) -> str:
    """LinkedIn's guest posting endpoint — no auth, full description.

    The numeric id is the tail of the job URL slug, not a query parameter.
    """
    m = _LI_ID.search(url or "")
    if not m:
        return ""
    page = _get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}")
    body = re.search(r'(?is)<div[^>]*class="[^"]*(?:description__text|show-more-less-html)[^"]*"[^>]*>(.*?)</div>\s*</div>', page)
    return _li_trim(clean(body.group(1) if body else page))


def _hellowork(url: str) -> str:
    return _jsonld_description(_get(url))


def _adzuna(url: str) -> str:
    # Adzuna's API ships a 500-char teaser; the page carries the JobPosting block in full.
    return _jsonld_description(_get(url))


def _workday_api(url: str) -> str:
    """Careers URL -> the public JSON endpoint behind it. Pure string work, so it is testable."""
    u = urllib.parse.urlparse(url)
    tenant = u.netloc.split(".")[0]
    parts = [p for p in u.path.strip("/").split("/") if p]
    if parts and re.fullmatch(r"[a-z]{2}(?:-[A-Za-z]{2})?", parts[0]):
        parts = parts[1:]                       # drop a locale prefix
    # ⚠ Thales's board links straight to the APPLY step, and that trailing segment is not part
    # of the job path — with it the API answers 406, which looks exactly like a bad tenant.
    while parts and parts[-1].lower() in ("apply", "applynow", "apply-now", "login"):
        parts = parts[:-1]
    if len(parts) < 2:
        return ""
    return f"https://{u.netloc}/wday/cxs/{tenant}/{parts[0]}/{'/'.join(parts[1:])}"


def _workday(url: str) -> str:
    """Workday job pages are client-rendered, but the JSON behind them is public and keyless.

    This matters more than any other reader here: Workday is where the large French employers
    are — Thales, GE HealthCare, Chanel — and they are the ones who carry the CFA relationships
    and the alternance volume. Before this, every Workday page returned ~120 characters of shell.
        careers  https://<host>/<site>/job/<loc>/<slug>
        api      https://<host>/wday/cxs/<tenant>/<site>/job/<loc>/<slug>
    The tenant is the first label of the host (gehc.wd5.myworkdayjobs.com -> "gehc"), and an
    optional locale segment ("en-US") sits before the site and must be dropped.
    """
    api = _workday_api(url)
    if not api:
        return ""
    d = json.loads(_get(api) or "{}")
    return clean((d.get("jobPostingInfo") or {}).get("jobDescription") or "")


_BY_HOST = [
    (re.compile(r"myworkdayjobs\.com", re.I), _workday),
    (re.compile(r"linkedin\.com", re.I), _linkedin),
    (re.compile(r"hellowork\.com", re.I), _hellowork),
    (re.compile(r"adzuna\.", re.I), _adzuna),
]



def _page_text(page: str) -> str:
    """Last resort: the page with scripts, styles and navigation stripped.

    Noisier than a structured block, which is why it is tried last — but a noisy description
    still separates an engineering role from a change-management one, and nothing does not.
    """
    body = re.sub(r"(?is)<(script|style|nav|header|footer|svg)[^>]*>.*?</\1>", " ", page)
    return clean(body)


_POSTING_MARK = re.compile(
    r"missions?|profil\b|comp[ée]tences|exp[ée]rience|responsabilit|qualificat|dipl[ôo]m|"
    r"vous (?:serez|aurez|participerez|travaillerez|rejoign)|"
    r"responsibilit|requirements|what you|we are looking|your role|skills", re.I)


# Where the posting stops and the APPLICATION FORM begins. Greenhouse serves both from one
# page, and half of an 11,807-character "description" was the form — right down to "what
# specific type of visa or authorization are you seeking?". Deliberately strong markers only:
# cutting a real description short is worse than carrying some furniture.
_FORM_START = re.compile(
    r"\n\s*Submit application|\n\s*Apply for this job|\n\s*Postuler à ce poste|"
    r"indique un champ obligatoire|indicates a required field|"
    r"\n\s*(?:Prénom|First Name|Last Name|Nom)\s*\*", re.I)


def _trim_form(t: str) -> str:
    m = _FORM_START.search(t or "")
    return (t[:m.start()] if m else t).strip()


def _looks_like_posting(t: str) -> bool:
    """Is this stripped page actually the job, or the furniture around it?

    Only ever applied to the RAW PAGE TEXT fallback — the weakest evidence path. JSON-LD is
    explicitly labelled JobPosting, and a payload comes from the board's own API, so neither
    needs this. Measured over the live boards, the gap is clean: every real description carried
    at least three of these markers, while all three junk cases carried ZERO —
      · APEC        999 chars of SPA chrome, IDENTICAL for every offer
      · Greenhouse 1,892 chars of the APPLICATION FORM ("Prénom *", "Nom *", "CV *")
      · iCIMS      1,451 chars of the COOKIE WALL ("Veuillez autoriser les cookies")
    each of which was being returned as a complete description with truncated=False.
    """
    return bool(_POSTING_MARK.search(t or ""))


_CACHE_PATH = Path(__file__).parent / "cache" / "description_cache.json"
_CACHE_TTL = 7 * 86400        # a posting's text barely changes; its existence is ats.verify()'s job
_CACHE_MAX = 2000
_cache_mem: dict | None = None


def _cache() -> dict:
    global _cache_mem
    if _cache_mem is None:
        try:
            _cache_mem = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _cache_mem = {}
    return _cache_mem


def _cache_get(url: str):
    e = _cache().get(url)
    if not e or time.time() - e.get("ts", 0) > _CACHE_TTL:
        return None
    return {"text": e["text"], "chars": len(e["text"]), "origin": e["origin"],
            "truncated": e.get("truncated", False)}


def _cache_put(url: str, got: dict) -> None:
    """Remember what a FETCH cost. Never cache a throttle or a transient error.

    WHY THIS EXISTS: the brief step reads hundreds of postings on every run, daily, and without
    this each run re-fetches every posting it has already read. That is slow on a 1GB VM and,
    worse, it is the most likely way to get LinkedIn — the biggest source here — to start
    refusing. A posting's TEXT is stable; whether it is still open is ats.verify()'s question,
    not this one, so a 7-day TTL costs nothing in accuracy.
    ⚠ "throttled"/"blocked"/"server_error"/"unreachable" are NOT cached: they say something
    about us or about a bad minute, not about the posting, and caching one would turn a
    five-minute outage into a week of silence.
    """
    if got["origin"] in ("payload", "teaser", "none") or got["origin"].startswith(
            ("error:throttled", "error:blocked", "error:server_error", "error:unreachable")):
        return
    c = _cache()
    c[url] = {"text": got["text"], "origin": got["origin"],
              "truncated": got["truncated"], "ts": time.time()}
    if len(c) > _CACHE_MAX:                     # keep the most recently written
        for k, _ in sorted(c.items(), key=lambda kv: kv[1].get("ts", 0))[:len(c) - _CACHE_MAX]:
            c.pop(k, None)
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass                                    # a cache write must never break a run


def fetch(lead: dict, use_cache: bool = True) -> dict:
    """The best description available for this lead.

    -> {"text", "chars", "origin", "truncated"}

    `origin` says where the text came from, so a thin result can be explained rather than
    guessed at. Never raises: an unreachable posting returns empty text with an origin of
    "error:<Type>", because one bad page must not stop a batch.
    """
    carried = (lead.get("description") or (lead.get("meta") or {}).get("description") or "").strip()
    _c = clean(carried)
    # ⚠ LENGTH ALONE DOES NOT MEAN COMPLETE. Boards hard-cap their teasers — APEC's texteOffre
    # is EXACTLY 283 chars and Adzuna's description EXACTLY 500, both cut mid-word and closed
    # with an ellipsis. 500 clears _MIN_USEFUL, so an Adzuna teaser would pass as a finished
    # description and suppress the fetch that would have got the real one.
    if len(_c) >= _MIN_USEFUL and not _TRUNCATED_TAIL.search(_c):
        return {"text": _c, "chars": len(_c), "origin": "payload", "truncated": False}

    url = lead.get("url") or ""
    if use_cache and url:
        hit = _cache_get(url)
        if hit:
            return hit

    def _done(got: dict) -> dict:
        if use_cache and url:
            _cache_put(url, got)
        return got

    for rx, reader in _BY_HOST:
        if rx.search(url):
            try:
                t = reader(url)
            except Exception as e:                         # noqa: BLE001
                return _done({"text": clean(carried), "chars": len(clean(carried)),
                              "origin": f"error:{_error_origin(e)}", "truncated": True})
            if len(t) >= _MIN_USEFUL:
                return _done({"text": t, "chars": len(t),
                              "origin": reader.__name__.strip("_"), "truncated": False})
            carried = t or carried
            break
    else:
        # ANY OTHER HOST — employer ATS pages included. Most publish JobPosting JSON-LD for
        # Google for Jobs; Veolia's Radancy page does, and without this branch it returned
        # NOTHING while being one of the strongest leads of the week. Falls back to stripped
        # page text when there is no structured block.
        if url:
            try:
                page = _get(url)
                t = _jsonld_description(page)
                if not t:
                    # Raw page text is the weakest evidence there is: an SPA shell, an
                    # application form and a cookie wall all have a length and none of them
                    # is the job. Reporting one as a description stops the caller looking
                    # anywhere else, which is the expensive failure.
                    raw = _trim_form(_page_text(page))
                    t = raw if _looks_like_posting(raw) else ""
                if len(t) >= _MIN_USEFUL:
                    return _done({"text": t, "chars": len(t), "origin": "generic",
                                  "truncated": False})
                carried = t or carried
            except Exception as e:                         # noqa: BLE001
                return _done({"text": clean(carried), "chars": len(clean(carried)),
                              "origin": f"error:{_error_origin(e)}", "truncated": True})

    t = clean(carried)
    # `truncated` means "there is more text than this", which is true when the board cut it off
    # as well as when it is simply short of _MIN_USEFUL.
    return _done({"text": t, "chars": len(t), "origin": "teaser" if t else "none",
                  "truncated": bool(t) and (len(t) < _MIN_USEFUL
                                            or bool(_TRUNCATED_TAIL.search(t)))})


# --------------------------------------------------------------------------- fact extraction

# Only technologies that appear in HER world: the point is overlap with a real CV, not a survey.
_STACK = {
    "python": r"\bpython\b", "java": r"\bjava\b(?!script)", "javascript": r"\bjavascript\b|\bjs\b",
    # NOT \bts\b: two letters, and "TS" appears in French prose for unrelated reasons.
    "typescript": r"\btypescript\b",
    "sql": r"\bsql\b", "c++": r"\bc\+\+\b", "c#": r"\bc#\b",
    "php": r"\bphp\b", "scala": r"\bscala\b", "go": r"\bgolang\b",
    "react": r"\breact\b", "node": r"\bnode\.?js\b", "flask": r"\bflask\b", "django": r"\bdjango\b",
    "fastapi": r"\bfastapi\b", "nestjs": r"\bnest\.?js\b",
    "pytorch": r"\bpytorch\b", "tensorflow": r"\btensorflow\b", "scikit-learn": r"scikit[- ]learn|\bsklearn\b",
    "pandas": r"\bpandas\b", "numpy": r"\bnumpy\b",
    "llm": r"\bllms?\b|grands? mod[èe]les? de langage", "rag": r"\brag\b|retrieval[- ]augmented",
    "genai": r"\bgen ?ai\b|ia g[ée]n[ée]rative|generative ai", "nlp": r"\bnlp\b|traitement du langage",
    # ⚠ A BARE "agent" IS A FRENCH JOB GRADE, NOT A TECHNOLOGY. French postings say
    # "Statut du poste : Agent de maîtrise", "agent d'accueil", "agent commercial" — so
    # `\bagents?\b` fired on postings with no AI in them at all. It must earn its meaning from
    # a neighbouring AI word (within one sentence), or be one of the unambiguous forms.
    "agents": (r"\bagentiques?\b|\bagentic\b|multi[- ]?agents?\b|\bai[ -]agents?\b|"
               r"\bagents?\b[^.\n]{0,50}?\b(?:llm|ia|gpt|rag|genai|conversationnels?|"
               r"autonomes?|intelligents?)\b|"
               r"\b(?:llm|gpt|genai)\b[^.\n]{0,50}?\bagents?\b"),
    "mcp": r"\bmcp\b",
    "langchain": r"\blangchain\b",
    # "prompting" is the common French usage and \bprompts?\b missed it entirely.
    "prompt": r"\bprompt\w*\b|prompt engineering",
    # Named assistants are worth recording because they are a NEGATIVE signal as often as a
    # positive one: a posting whose only AI content is "administer ChatGPT Enterprise" is an
    # adoption role, which is exactly how Docaposte and Albioma read.
    "chatgpt": r"\bchat ?gpt\b", "copilot": r"\bcopilot\b",
    "embeddings": r"\bembeddings?\b", "vector db": r"vector ?(db|database)|base vectorielle",
    "docker": r"\bdocker\b", "kubernetes": r"\bkubernetes\b|\bk8s\b", "openshift": r"\bopenshift\b",
    "terraform": r"\bterraform\b", "ansible": r"\bansible\b", "helm": r"\bhelm\b",
    "ci/cd": r"\bci ?/ ?cd\b|int[ée]gration continue", "git": r"\bgit(hub|lab)?\b",
    "linux": r"\blinux\b", "airflow": r"\bairflow\b", "spark": r"\bspark\b", "hadoop": r"\bhadoop\b",
    "kafka": r"\bkafka\b", "dbt": r"\bdbt\b", "snowflake": r"\bsnowflake\b",
    "aws": r"\baws\b|amazon web services", "azure": r"\bazure\b", "gcp": r"\bgcp\b|google cloud",
    "power bi": r"\bpower ?bi\b", "tableau": r"\btableau\b", "excel": r"\bexcel\b", "vba": r"\bvba\b",
    "postgres": r"\bpostgre\w*\b", "mongodb": r"\bmongo\w*\b", "power automate": r"\bpower automate\b",
    "copilot studio": r"copilot studio",
    # ---- added 2026-09-20 after measuring recall over 159 real descriptions. Each of these was
    # named by real postings and was invisible: Databricks 12, BigQuery 12, MLOps 15,
    # transformers 19, PySpark 6, Talend 6.
    "mlops": r"\bml ?ops\b", "databricks": r"\bdatabricks\b", "bigquery": r"\bbigquery\b",
    "pyspark": r"\bpyspark\b", "transformers": r"\btransformers\b",
    "hugging face": r"hugging ?face\b", "mlflow": r"\bmlflow\b",
    "fine-tuning": r"fine[- ]?tun\w+|\bfinetun\w+",
    "streamlit": r"\bstreamlit\b", "opencv": r"\bopencv\b", "bash": r"\bbash\b|shell script",
    "redis": r"\bredis\b", "elasticsearch": r"\belastic ?search\b",
    "jenkins": r"\bjenkins\b", "nosql": r"\bnosql\b", "talend": r"\btalend\b",
    "dataiku": r"\bdataiku\b", "sagemaker": r"\bsagemaker\b", "vertex ai": r"vertex ?ai\b",
    "llamaindex": r"llama ?index\b", "looker": r"\blooker\b", "qlik": r"\bqlik\b",
    "rest api": r"\bapis? rest\b|\brest(?:ful)? apis?\b|\bgraphql\b",
    # ⚠ VENDOR NAMES NEED GUARDS, and two of these are why. "Claude" is an ordinary French
    # first name (Jean-Claude, hôpital Claude-Bernard) and "Mistral" is a wind, a missile and
    # an Airbus programme — bare \bclaude\b / \bmistral\b would fire on employers with no
    # LLM anywhere. Both must be spelled in their product form to count.
    "openai": r"\bopen ?ai\b|\bgpt-?[45]\b",
    "anthropic": r"\banthropic\b|\bclaude\s*(?:code|opus|sonnet|haiku|[345])\b",
    "mistral ai": r"\bmistral\s*ai\b",
}
_STACK_RX = {k: re.compile(v, re.I) for k, v in _STACK.items()}

_CONTRACT = [
    # ⚠ THE PLURAL WAS MISSING: \balternan[ct]e?\b matches "alternant" but NOT "alternants",
    # so "nous recrutons deux alternants" registered as no contract at all.
    # ⚠ "APPRENTISSAGE" USUALLY MEANS MACHINE LEARNING, NOT APPRENTICESHIP — and \bapprenti\w*\b
    # swallowed it. Measured against France Travail's boolean: 6 of 7 false "alternance" verdicts
    # were "apprentissage automatique", "bases de données d'apprentissage", "capacité
    # d'apprentissage" — i.e. the error concentrated on exactly the AI/data postings she targets.
    # The noun counts as a CONTRACT only in its contract forms.
    ("alternance", r"\balternan[ct]e?s?\b|\bapprenti(?:e|s|es)?\b|"
                   r"contrat\s+d['’]apprentissage|\ben\s+apprentissage\b|contrat pro"),
    ("stage", r"\bstage\b|\bstagiaire\b|\binternship\b"),
    ("cdi", r"\bcdi\b|\bpermanent\b"),
    ("cdd", r"\bcdd\b|fixed[- ]term"),
]
_LEVEL = [
    # ORDER IS THE MEANING. This field answers "can an M1 student apply?", so an explicitly
    # named M1 WINS over a co-mentioned M2: "M1/M2" and "Master 1 ou 2" are invitations, and
    # matching m2 first read them as refusals.
    ("m1_ok", r"\bm1\b|master\s*1|bac\s*\+\s*4\b|m1\s*[/&]\s*m2"),
    ("m2_or_final", r"derni[èe]re ann[ée]e|\bm2\b|master\s*2|2[eè]?(?:me)?\s+ann[ée]e de master"),
    # "Bac+5" is the DIPLOMA, not the year. CACIB's 24-month alternance said "Bac+5" and starts
    # in M1; reading it as "final year only" would have hidden a posting she actually applied to.
    ("bac5", r"bac\s*\+\s*5\b"),
    # Before `bachelor`, or "licence pro" is swallowed by the bare "licence".
    # ⚠ \bbut\b is NOT matched — it is the ordinary French noun, and "le but de cette
    # alternance" labelled a real M2 posting as BTS-level. jobsource refuses it for the same
    # reason on titles; here, on 2,000 words of prose, it fires constantly.
    ("below", r"\bbts\b|\bdut\b|bac\s*\+\s*2\b|licence pro"),
    ("bachelor", r"bac\s*\+\s*3\b|licence"),
]
_BEGINNER = re.compile(
    # ⚠ "première expérience" IS NOT HERE, and used to be. "Vous justifiez d'une première
    # expérience réussie en data science" is a REQUIREMENT; reading it as "beginners welcome"
    # inverted the meaning of the one field that says whether she will be read at all.
    r"d[ée]butant\w*\s*(?:e?s?)\s*(?:accept|bienvenu|admis)|ouvert\w*\s+aux\s+d[ée]butant|"
    r"aucune\s+exp[ée]rience[^.\n]{0,30}(?:requise|exig|n[ée]cessaire|demand)|"
    r"sans\s+exp[ée]rience\s+(?:requise|exig|pr[ée]alable)|no\s+(?:prior\s+)?"
    r"(?:professional\s+)?experience\s+(?:required|needed)|profil\s+junior", re.I)

# --- duration, start and remote all had the SAME defect: they took the FIRST thing that looked
# right anywhere in ~2,000 words, with nothing tying it to the contract being offered.

_DUR_UNIT = re.compile(r"\b(\d{1,2})\s*(?:à|a|-|ou)?\s*(\d{1,2})?\s*(mois|ans?|years?|months?)\b", re.I)
_DUR_CONTEXT = re.compile(r"altern\w+|apprenti\w*|contrat|dur[ée]e|stage|mission|poste", re.I)
_DUR_REJECT = re.compile(r"exp[ée]rience|anciennet[ée]|existe|cr[éeè]{2}e|fond[ée]e|depuis", re.I)


def _duration(t: str):
    """The CONTRACT's duration — not the first number in the posting.

    "Vous justifiez de 5 ans d'expérience" and "notre entreprise existe depuis 30 ans" were both
    being reported as the length of the alternance. A candidate now has to sit near contract
    language and away from experience language.
    """
    # SCOPED TO THE SENTENCE, not to a character window. A ±60-char window reached backwards
    # across the full stop and let "…5 ans d'expérience. Contrat en alternance de 24 mois"
    # reject its own correct answer.
    for sent in re.split(r"(?<=[.!?;\n•·])\s+", t):
        if _DUR_REJECT.search(sent) or not _DUR_CONTEXT.search(sent):
            continue
        m = _DUR_UNIT.search(sent)
        if m:
            return m.group(0).strip()
    return None


_START_RX = re.compile(r"\b(janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[ûu]t|septembre|"
                       r"octobre|novembre|d[ée]cembre|january|february|march|april|june|july|"
                       r"august|september|october|november|december)\s*(20\d{2})\b", re.I)
_START_REJECT = re.compile(r"depuis|cr[éeè]{2}e|fond[ée]e|dipl[ôo]m[ée]|obtenu|since|founded", re.I)


def _start(t: str, today=None):
    """When the CONTRACT starts. A start date is in the future — that is what makes this cheap.

    "Créée en janvier 2015, la société…" and "diplômé depuis juin 2024" were both being reported
    as the start date. Requiring the year not to be in the past kills the whole class.
    """
    import datetime
    year_now = (today or datetime.date.today()).year
    for m in _START_RX.finditer(t):
        if int(m.group(2)) < year_now:
            continue
        if _START_REJECT.search(t[max(0, m.start() - 40):m.start()]):
            continue
        return f"{m.group(1)} {m.group(2)}"
    return None


_REMOTE_RX = re.compile(r"t[ée]l[ée]travail|remote|hybride|hybrid|sur site|on[- ]site", re.I)
_REMOTE_NEG = re.compile(r"\b(pas|aucun\w*|sans|non|n'est pas|ni)\b[^.\n]{0,30}$", re.I)


def _remote(t: str):
    """What the posting says about remote — INCLUDING when it says no.

    "Pas de télétravail possible" and "le télétravail n'est pas autorisé pour les alternants"
    both came back as `['télétravail']`, which reads as an offer of exactly the thing being
    refused. A negated mention is now reported as the refusal it is.
    """
    out = set()
    for m in _REMOTE_RX.finditer(t):
        word = m.group(0).lower()
        before = t[max(0, m.start() - 40):m.start()]
        after = t[m.end():m.end() + 30]
        negated = bool(_REMOTE_NEG.search(before)) or bool(
            re.match(r"\s*n[e']\s*(?:est|sont)?\s*pas|\s*non\b", after, re.I))
        out.add(f"pas de {word}" if negated and "site" not in word else word)
    return sorted(out) or None


def _contract(t: str):
    """Which contract THIS posting offers — not every contract word it happens to contain.

    MEASURED against France Travail's own `alternance` boolean over 195 postings, because that
    boolean is ground truth and guessing was not needed:
        >=2 alternance mentions -> 24 real alternances, ZERO false positives out of 155
         1 alternance mention   -> 5 real against 9 NOT: genuinely ambiguous
         0 mentions             -> 11 real alternances say it nowhere in the body, which is
                                   why the board's own field always outranks this one.
    So a lone mention loses to an explicitly stated CDI/CDD/stage — that is the
    "CDD de 6 mois, l'équipe compte 3 apprentis" case, which used to come back "alternance".
    """
    hits = {name: len(re.findall(pat, t, re.I)) for name, pat in _CONTRACT}
    if hits["alternance"] >= 2:
        return "alternance"
    ranked = [name for name, _ in _CONTRACT if hits[name]]
    if hits["alternance"] == 1 and len(ranked) > 1:
        return next(n for n in ranked if n != "alternance")
    return ranked[0] if ranked else None


def extract(text: str) -> dict:
    """Structured facts from a description. Everything is best-effort and may be empty.

    Absence is reported as absence — never as a negative. A posting that does not mention a
    level is `level: None`, which is not the same as "accepts M1", and the caller must not
    treat it as one.
    """
    t = text or ""
    stack = sorted(k for k, rx in _STACK_RX.items() if rx.search(t))
    contract = _contract(t)
    level = next((name for name, pat in _LEVEL if re.search(pat, t, re.I)), None)

    return {
        "stack": stack,
        "contract": contract,
        "level": level,
        "beginner_ok": bool(_BEGINNER.search(t)),
        "duration": _duration(t),
        "start": _start(t),
        "remote": _remote(t),
        "chars": len(t),
    }


def describe(lead: dict) -> dict:
    """fetch() + extract() for one lead, as one record."""
    got = fetch(lead)
    return {**got, "facts": extract(got["text"])}


if __name__ == "__main__":
    import sys
    lead = {"url": sys.argv[1]} if len(sys.argv) > 1 else {}
    d = describe(lead)
    print(f"origin={d['origin']} chars={d['chars']} truncated={d['truncated']}")
    print(json.dumps(d["facts"], ensure_ascii=False, indent=1))
    print("\n" + d["text"][:1200])
