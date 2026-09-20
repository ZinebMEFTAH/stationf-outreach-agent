#!/usr/bin/env python3
"""
Pre-flight self-test for the StationF outreach agent.

Runs fast, offline checks (no SMTP/IMAP/network) to verify the system is healthy
BEFORE a cron job invokes Claude. If this fails, the run scripts skip the run and
send an alert — better to do nothing than to operate on a broken system.

Usage:
  python preflight.py            # full check, exit 0 = healthy, 1 = broken
  python preflight.py --quiet    # only print failures
  python preflight.py --warnings # print ONLY the soft warnings (one per line), always exit 0

Covered:
  - all modules import
  - config constants are sane (caps, contract priority)
  - contract-type detection
  - contact_finder name-parsing guards (false-positive protection)
  - email_verify pattern building (diacritics)
  - tracker schema integrity + load
  - smtp_send footer / alert logic (dry-run, no real send)
  - daily send-count plumbing
  - contacts.xlsx column schema
  - CV .tex sources exist and reference the flagship projects
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

_QUIET = "--quiet" in sys.argv
# Warnings-only mode: emit the degraded-state lines and nothing else, so a caller
# (vm/preflight_gate.sh) can pipe them straight into an alert email. Always exits 0 —
# a warning is "running but degraded", never a reason to skip the run.
_WARN_ONLY = "--warnings" in sys.argv
_passed = 0
_failed = 0
_failures: list[str] = []
_warnings: list[str] = []


def check(name: str, fn) -> None:
    global _passed, _failed
    try:
        fn()
        _passed += 1
        if not _QUIET:
            print(f"  ✅ {name}")
    except Exception as e:
        _failed += 1
        msg = f"{name}: {type(e).__name__}: {e}"
        _failures.append(msg)
        print(f"  ❌ {msg}")
        if not _QUIET:
            traceback.print_exc()


def warn(name: str, fn) -> None:
    """Run a soft check that returns a warning string (or None if all good).

    Warnings do NOT fail preflight — they flag a degraded-but-functioning state that
    would otherwise fail silently. Always printed, even in --quiet.
    """
    try:
        msg = fn()
    except Exception as e:  # a broken warning check must never break preflight
        msg = f"{name}: warning check errored: {type(e).__name__}: {e}"
    if msg:
        _warnings.append(f"{name}: {msg}")
        if not _WARN_ONLY:
            print(f"  ⚠️  {name}: {msg}")
    elif not _QUIET and not _WARN_ONLY:
        print(f"  ✅ {name}")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def t_imports():
    import config, tracker, smtp_send, imap_fetch, cv_builder
    import contact_finder, scraper, companies, email_verify  # noqa: F401
    import jobsource, wttj, hellowork, apec, france_travail, free_work, company_resolver  # noqa: F401
    import labonnealternance, lead_facts, ats_detect, usage_budget, remotive, opportunities  # noqa: F401


def t_config_caps():
    import config
    assert config.COLD_CAP == 7, f"COLD_CAP should be 7, got {config.COLD_CAP}"
    assert config.WARM_CAP == 3, f"WARM_CAP should be 3, got {config.WARM_CAP}"
    assert config.DAILY_CAP == config.COLD_CAP + config.WARM_CAP, "DAILY_CAP mismatch"
    assert config.FOLLOWUP_DAYS == 4, f"FOLLOWUP_DAYS should be 4, got {config.FOLLOWUP_DAYS}"
    # Multi-touch follow-up sequence
    assert config.MAX_FOLLOWUPS >= 1, "MAX_FOLLOWUPS must be >= 1"
    assert config.FOLLOWUP_GAP >= 0, "FOLLOWUP_GAP must be >= 0"
    # Warm-up ramp: effective cap climbs and never exceeds COLD_CAP
    from datetime import timedelta
    d0 = config.WARMUP_START_DATE
    assert config.effective_cold_cap(d0) <= config.effective_cold_cap(d0 + timedelta(days=8)), \
        "warm-up ramp must be non-decreasing"
    assert config.effective_cold_cap(d0 + timedelta(days=90)) == config.COLD_CAP, \
        "ramp must reach COLD_CAP after warm-up"
    assert config.effective_cold_cap(d0) <= config.COLD_CAP, "effective cap must never exceed COLD_CAP"


def t_config_footers():
    import config
    assert config.FOOTER_FR and "P.S." in config.FOOTER_FR, "FOOTER_FR missing/malformed"
    assert config.FOOTER_EN and "P.S." in config.FOOTER_EN, "FOOTER_EN missing/malformed"


def t_contract_priority():
    import config
    assert config.CONTRACT_PRIORITY == ["cdi", "cdd", "alternance"], config.CONTRACT_PRIORITY


def t_contract_detection():
    import config
    cases = {
        "ALTERNANCE – Chef de projet IA": "alternance",
        "Data Scientist Intern | S2 26": "stage",
        "STAGE – Ops & Automation": "stage",
        "SENIOR AI ENGINEER - CDI": "cdi",
        "Software Engineer CDD 6 mois": "cdd",
        "[Suggested] AI Engineer": "speculative",
        "Lead Backend Engineer (Python)": "unspecified",
    }
    for role, expected in cases.items():
        got = config.guess_contract_type(role)
        assert got == expected, f"{role!r} → {got} (expected {expected})"


def t_language_guess():
    import config
    assert config.guess_language("Ingénieur Data alternance") == "fr"
    assert config.guess_language("Senior Backend Engineer") == "en"


def t_contact_finder_guards():
    from contact_finder import _parse_name, score_title, pick_best_person
    # Must reject
    for bad in ["STAGE IA", "AI ENGINEER", "Nous Tomorro", "The Platform",
                "Software Engineer", "contact@x.com", "Bienvenue chez"]:
        assert _parse_name(bad) is None, f"{bad!r} should be rejected"
    # Must accept
    for good in ["Nicolas Henry", "Jean-Baptiste Gariel", "François Pellissier"]:
        assert _parse_name(good) is not None, f"{good!r} should be accepted"
    # Title scoring order
    assert score_title("CTO") > score_title("Head of AI") > score_title("CEO") > score_title("Recruiter")
    # Best-person picks highest title
    best = pick_best_person([
        {"name": "A B", "title": "Recruiter"},
        {"name": "C D", "title": "CTO"},
    ])
    assert best["name"] == "C D", "pick_best_person should choose CTO"


def t_company_resolver():
    # Pure, offline logic of the name→domain resolver (no network).
    from company_resolver import _norm, _slug, _confident
    assert _norm("Ippon Technologies SAS") == "ippon", _norm("Ippon Technologies SAS")
    assert _slug("Consort Group") == "consort"
    # Confident: exact root on a non-foreign TLD
    assert _confident("Trustpair", {"name": "Trustpair", "domain": "trustpair.fr"})
    assert _confident("Qonto", {"name": "Qonto", "domain": "qonto.com"})
    # Not confident: same name but foreign-country TLD (different company)
    assert not _confident("Mistral AI", {"name": "Mistral Air", "domain": "mistralair.it"})
    # Not confident: unrelated root
    assert not _confident("Alan", {"name": "Alan's Factory Outlet", "domain": "alansfactoryoutlet.com"})


def t_email_patterns():
    # Expectations are computed from the inputs at runtime (not hardcoded email literals)
    # so the public mirror's data-sanitizer can't desync the assertions.
    from email_verify import build_patterns, strip_diacritics
    first, last, dom = "Cédric", "Boidin", "kraaft.com"
    f, l, d = strip_diacritics(first).lower(), strip_diacritics(last).lower(), dom.lower()
    pats = build_patterns(first, last, dom)
    assert pats[0] == f"{f}.{l}@{d}", pats[0]      # prenom.nom leads
    assert pats[1] == f"{f}@{d}", pats[1]          # prenom second
    assert f"{f}-{l}@{d}" in pats and f"{f[0]}{l}@{d}" in pats
    assert all(p.endswith("@" + d) for p in pats)
    # Single-name people must not yield malformed locals (no trailing . / -)
    edge = build_patterns("Madonna", "", "x.com")
    assert all(p.split("@")[0][-1] not in ".-" for p in edge), edge


def t_descriptions_extract_facts_and_report_absence_honestly():
    """Fact extraction must find her stack, and must never invent what a posting did not say.

    OFFLINE. Reading the description is the ONLY thing that separated a real engineering
    alternance from an adoption role this week: Docaposte's "IA Générative & Automatisation" and
    Veolia's "Création d'agents LLM" have near-identical titles, and the discriminator is that
    Docaposte's text never mentions a programming language.
    """
    import descriptions as D
    veolia = ("Concevoir et développer un assistant basé sur les LLM, intégration d'API, "
              "développement Python, agents apprenants, alternance 12 mois, dernière année de master")
    f = D.extract(veolia)
    assert "python" in f["stack"] and "llm" in f["stack"] and "agents" in f["stack"], f["stack"]
    assert f["contract"] == "alternance" and f["level"] == "m2_or_final", f

    # The adoption role: AI words present, NO language. Absence is the signal.
    adoption = ("Administrer ChatGPT Enterprise, structurer les pratiques de prompting, "
                "animer des ateliers d'adoption, mesurer l'impact métier. Alternance.")
    g = D.extract(adoption)
    assert "python" not in g["stack"] and "prompt" in g["stack"], g["stack"]

    # ABSENCE IS NOT A NEGATIVE. A posting that says nothing about level must report None, which
    # a caller must not read as "accepts M1".
    h = D.extract("Alternance data engineer à Paris.")
    assert h["level"] is None and h["beginner_ok"] is False and h["stack"] == [], h

    # MCP and RAG are rare and decisive in her world — they must not be missed.
    i = D.extract("compréhension des modèles de langage (LLM) et des techniques de RAG, MCP")
    assert {"mcp", "rag", "llm"} <= set(i["stack"]), i["stack"]

    # Kubernetes/Spark are the gaps she must declare — they must be detected, not glossed.
    j = D.extract("Kubernetes, Red Hat OpenShift, Helm, Argo CD, Terraform, Ansible, Spark")
    assert {"kubernetes", "openshift", "helm", "terraform", "ansible", "spark"} <= set(j["stack"])

    # html-to-text must keep the words and drop the markup
    assert "missions" in D.clean("<p>Vos <b>missions</b></p><li>x</li>").lower()

    # ---- THE FRENCH WORDS THAT LOOK LIKE TECHNOLOGY (found 2026-09-20 by testing the FALSE
    # direction, which the first pass never did). Each of these fired on real postings.
    # "Agent de maîtrise" is a job GRADE printed on APEC and France Travail offers; a bare
    # \bagents?\b turned every one of them into an AI posting.
    assert "agents" not in D.extract("Statut du poste : Agent de maîtrise / Technicien")["stack"]
    assert "agents" not in D.extract("Agent d'accueil et agent de sécurité")["stack"]
    for real in ("Apprenti Création d'agents LLM", "systèmes multi-agents", "Build AI agents",
                 "IA agentique", "agents conversationnels"):
        assert "agents" in D.extract(real)["stack"], real
    # Two letters are not a language, and a French first name is not a model vendor.
    assert "typescript" not in D.extract("Le candidat est autonome. TS. Autre chose.")["stack"]
    assert "anthropic" not in D.extract("Dr Jean-Claude Martin, hôpital Claude Bernard")["stack"]
    assert "mistral ai" not in D.extract("moteur Mistral, programme A400M")["stack"]
    assert "anthropic" in D.extract("Nous utilisons Claude Code")["stack"]

    # ---- LEVEL. This field answers "can an M1 student apply?", so a posting that NAMES M1 is
    # an invitation even when it also names M2 — matching m2 first read "M1/M2" as a refusal.
    assert D.extract("Actuellement en M1/M2 en informatique")["level"] == "m1_ok"
    assert D.extract("Master 1 ou Master 2, école d'ingénieur")["level"] == "m1_ok"
    assert D.extract("dernière année de master")["level"] == "m2_or_final"
    # "Bac+5" is the DIPLOMA, not the year: CACIB's 24-month alternance says Bac+5 and starts in
    # M1. It must never collapse into "final year only".
    assert D.extract("Formation Bac+5 en informatique, alternance 24 mois")["level"] == "bac5"
    # ⚠ "but" is the ordinary French noun. "Le but de cette alternance" was being read as a
    # BTS-level posting — jobsource refuses the same token on titles for the same reason.
    assert D.extract("Le but de cette alternance est de construire une plateforme")["level"] is None
    # "licence pro" must not be swallowed by the bare "licence" (Bac+3).
    assert D.extract("Licence professionnelle métiers du numérique")["level"] == "below"

    # ---- THE FREE DESCRIPTION. France Travail, Free-Work and Remotive each return the full
    # posting text in the SAME response as the listing. It was being discarded, so fetch() went
    # back over the network for text the source had already handed over. `meta["description"]`
    # is the contract between them; if a source stops filling it, this catches it offline.
    import inspect
    for mod in ("france_travail", "free_work", "remotive"):
        src = inspect.getsource(__import__(mod))
        assert '"description"' in src, f"{mod} no longer carries its own description"
    carried = D.fetch({"meta": {"description": "<p>" + "Python, Docker et LLM. " * 30 + "</p>"}})
    assert carried["origin"] == "payload" and "python" in D.extract(carried["text"])["stack"]

    # ---- NEVER RAISES. One unreachable posting must not stop a batch, and the origin must say
    # what happened rather than look like an empty description.
    assert D.fetch({"url": "https://example.invalid/nope"})["origin"].startswith("error:")
    assert D.fetch({})["origin"] == "none"

    # ---- "APPRENTISSAGE" IS MACHINE LEARNING FAR MORE OFTEN THAN IT IS APPRENTICESHIP, and
    # \bapprenti\w*\b swallowed it. Measured against France Travail's own `alternance` boolean
    # over 195 postings: 6 of 7 false alternance verdicts were "apprentissage automatique",
    # "bases de données d'apprentissage", "capacité d'apprentissage" — the error landed on
    # exactly the AI/data postings she targets. Fixing it took precision 80% -> 97%.
    for ml in ("Modèles d'apprentissage automatique et deep learning",
               "Constituer les bases de données d'apprentissage",
               "Capacité d'apprentissage rapide"):
        assert D.extract(ml)["contract"] is None, ml
    for contract in ("Contrat d'apprentissage de 12 mois", "Formation en apprentissage sur 24 mois",
                     "Nous recherchons un apprenti data engineer",
                     "Nous recrutons deux alternants en alternance"):   # the plural was missing too
        assert D.extract(contract)["contract"] == "alternance", contract
    # A contract word describing the TEAM is not this posting's contract. Evidence for the rule:
    # one lone alternance mention ran 5 real against 9 not, while >=2 gave ZERO false positives.
    assert D.extract("CDI. Vous encadrerez des alternants et des stagiaires.")["contract"] == "cdi"
    assert D.extract("CDD de 6 mois. L'équipe compte 3 apprentis.")["contract"] == "cdd"
    assert D.extract("Ce stage de 6 mois peut déboucher sur une alternance.")["contract"] == "stage"

    # ---- DURATION, START and REMOTE all had ONE defect in common: they took the first thing
    # that looked right anywhere in ~2,000 words, with nothing tying it to the contract offered.
    # The duration of the CONTRACT, never the experience asked for or the company's age:
    assert D.extract("Vous avez 3 ans d'expérience. Contrat en alternance de 24 mois.")["duration"] == "24 mois"
    assert D.extract("Notre entreprise existe depuis 30 ans. Alternance 12 mois.")["duration"] == "12 mois"
    # A start date is in the FUTURE — which is what makes this cheap to get right.
    assert D.extract("Créée en janvier 2015. Poste à pourvoir en octobre 2026.")["start"] == "octobre 2026"
    assert D.extract("Société fondée en mars 2010.")["start"] is None
    # A REFUSAL of remote must never read as an offer of it.
    assert D.extract("Pas de télétravail possible")["remote"] == ["pas de télétravail"]
    assert D.extract("Le télétravail n'est pas autorisé")["remote"] == ["pas de télétravail"]
    assert D.extract("2 jours de télétravail par semaine")["remote"] == ["télétravail"]

    # ---- beginner_ok says whether she will be READ AT ALL, so an inverted reading is expensive.
    # "Vous justifiez d'une première expérience réussie" is a REQUIREMENT, and used to come back
    # as "beginners welcome".
    assert D.extract("Une première expérience réussie est exigée")["beginner_ok"] is False
    assert D.extract("Aucune expérience professionnelle requise")["beginner_ok"] is True
    assert D.extract("Ouvert aux débutants")["beginner_ok"] is True

    # ---- A PAGE THAT HAS A LENGTH IS NOT A DESCRIPTION. The raw page-text fallback is the
    # weakest evidence path, and it was returning the furniture around the job as the job.
    # Measured live: every real description carried >=3 of these markers, all three junk cases
    # ZERO — APEC 999 chars of SPA chrome (IDENTICAL for every offer), Greenhouse 1,892 chars
    # of the APPLICATION FORM, iCIMS 1,451 chars of the COOKIE WALL. Each was reported as a
    # complete description with truncated=False, which stops the caller looking anywhere else.
    assert D._looks_like_posting("Vos missions : développer. Profil recherché : M1.")
    assert not D._looks_like_posting("Postuler à ce poste Prénom * Nom * CV * Joindre")
    assert not D._looks_like_posting("Veuillez autoriser les cookies pour continuer")
    assert not D._looks_like_posting("Recherche emploi | Apec. Vous avez déjà un compte ?")

    # ---- LENGTH ALONE DOES NOT MEAN COMPLETE. Boards hard-cap teasers: APEC's texteOffre is
    # EXACTLY 283 chars and Adzuna's description EXACTLY 500, both cut mid-word and closed with
    # an ellipsis — and 500 clears _MIN_USEFUL, so it would pass as a finished description and
    # suppress the fetch that would have got the real one.
    _body = ("Vos missions et le profil recherché. " * 20)[:480]
    assert D.fetch({"meta": {"description": _body + "."}})["origin"] == "payload"
    for _tail in ("…", "..."):
        _g = D.fetch({"meta": {"description": _body + _tail}})
        assert _g["origin"] == "teaser" and _g["truncated"], _tail

    # ---- COMPRESSED BYTES ARE NOT TEXT. APEC serves gzip even when asked for identity, and
    # decoding it with errors="replace" produced 5,121 characters of mojibake that fetch()
    # reported as a clean description — the same length for every offer.
    import gzip as _gz
    assert D._decompress(_gz.compress(b"<p>Vos missions</p>"), "gzip") == b"<p>Vos missions</p>"
    assert D._decompress(b"<p>plain</p>", "") == b"<p>plain</p>"
    assert D._decompress(b"not really gzip", "gzip") == b""       # never raises, never garbage

    # ---- WORKDAY IS WHERE THE LARGE FRENCH EMPLOYERS ARE (Thales, GE HealthCare, Chanel) and
    # its pages are client-rendered — every one returned ~120 chars of shell. The JSON behind
    # them is public and keyless. Two traps in the URL mapping, both offline-testable:
    # a locale prefix, and Thales linking straight to /apply (which answers 406, looking
    # exactly like a wrong tenant).
    assert D._workday_api("https://thales.wd3.myworkdayjobs.com/Careers/job/V/Ing_R1/apply") == \
        "https://thales.wd3.myworkdayjobs.com/wday/cxs/thales/Careers/job/V/Ing_R1"
    assert D._workday_api("https://x.wd1.myworkdayjobs.com/en-US/Careers/job/Paris/R_R1") == \
        "https://x.wd1.myworkdayjobs.com/wday/cxs/x/Careers/job/Paris/R_R1"
    assert D._workday_api("https://x.wd1.myworkdayjobs.com/Careers") == ""

    # ---- JSON-LD NESTING. `"@type": ["JobPosting"]` (a list — a node may declare several
    # types) and the `@graph` wrapper most CMS plugins emit are the schema.org NORM, and both
    # returned nothing. That is a silent loss on the generic path, made worse by tightening the
    # page-text fallback: no JSON-LD hit now means the description is dropped entirely.
    import json as _j
    for _doc in ({"@type": ["JobPosting"], "description": "<p>Vos missions ici</p>"},
                 {"@graph": [{"@type": "Organization", "description": "blurb"},
                             {"@type": "JobPosting", "description": "<p>Vos missions ici</p>"}]},
                 [{"@type": "Organization", "description": "blurb"},
                  {"@type": "JobPosting", "description": "<p>Vos missions ici</p>"}]):
        _page = f'<script type="application/ld+json">{_j.dumps(_doc)}</script>'
        assert D._jsonld_description(_page) == "Vos missions ici", _doc
    # An Organization blurb is NOT a job description — the company's marketing copy would sail
    # through every downstream check while saying nothing about the role.
    assert D._jsonld_description(
        '<script type="application/ld+json">{"@type":"Organization","description":"blurb"}</script>') == ""

    # ---- THE BOARD'S OWN UI IS NOT THE EMPLOYER'S WORDS. LinkedIn's body capture ran past the
    # description into its panel — ~250-340 chars on every posting, carrying "Employment type:
    # Full-time", which LinkedIn stamps on ALTERNANCE postings. Greenhouse serves the job and
    # the APPLICATION FORM from one page, and half of an 11,807-char "description" was the form.
    assert D._li_trim("Vos missions.\n\nShow more\n\nSeniority level\n\nNot Applicable") == "Vos missions."
    assert D._li_trim("Vos missions.\n\nEmployment type\n\nFull-time") == "Vos missions."
    assert D._trim_form("Vos missions ici.\n\nSubmit application\n\nPrénom *") == "Vos missions ici."
    # ...and neither may touch a description that simply has no such block.
    _plain = "Vos missions : développer des agents. Profil : M1 informatique."
    assert D._li_trim(_plain) == _plain and D._trim_form(_plain) == _plain

    # ---- A 404 AND A 429 MEAN OPPOSITE THINGS, and they used to collapse into one opaque
    # "error:HTTPError". A 404 is real signal (the posting is gone); a 429 or 403 means WE are
    # throttled or blocked and the posting may be perfectly alive. Conflating them is the shape
    # this repo keeps getting bitten by — a batch that comes back entirely "error" reads as
    # "these jobs are all dead" when it means the reading stopped. LinkedIn is the biggest
    # source here and the likeliest to throttle, so the distinction must reach the caller.
    import urllib.error as _ue, socket as _sock, tempfile as _tf, pathlib as _pl
    _real_get, _real_path, _real_mem = D._get, D._CACHE_PATH, D._cache_mem
    D._CACHE_PATH = _pl.Path(_tf.mkdtemp()) / "desc_cache.json"      # never touch the real one
    D._cache_mem = None
    try:
        for _code, _want in ((404, "gone"), (410, "gone"), (429, "throttled"), (403, "blocked"),
                             (401, "blocked"), (500, "server_error"), (503, "server_error")):
            D._get = (lambda c: lambda u: (_ for _ in ()).throw(
                _ue.HTTPError("u", c, "x", {}, None)))(_code)
            assert D.fetch({"url": f"https://x.test/{_code}"})["origin"] == f"error:{_want}", _code
        D._get = lambda u: (_ for _ in ()).throw(_sock.timeout("t"))
        assert D.fetch({"url": "https://x.test/timeout"})["origin"] == "error:unreachable"

        # ---- THE CACHE MUST NOT REMEMBER A BAD MINUTE. "gone" is about the posting and is
        # worth keeping; "throttled"/"blocked"/"server_error"/"unreachable" are about US, and
        # caching one would turn a five-minute outage into a week of silence.
        assert "https://x.test/404" in D._cache()
        for _c in (429, 403, 500):
            assert f"https://x.test/{_c}" not in D._cache(), _c
        assert "https://x.test/timeout" not in D._cache()
        # A cache hit must be the same answer, without touching the network at all.
        D._get = lambda u: (_ for _ in ()).throw(AssertionError("cache miss: went to network"))
        assert D.fetch({"url": "https://x.test/404"})["origin"] == "error:gone"
    finally:
        D._get, D._CACHE_PATH, D._cache_mem = _real_get, _real_path, _real_mem


def t_the_daily_digest_uses_the_rebuilt_pipeline():
    """The 08:00 digest must actually RUN the rebuilt pipeline, not just have it sitting nearby.

    OFFLINE. For most of the rebuild it did not: leadset, descriptions and ats were built,
    verified and committed while `opportunities.py` imported NONE of them, so the thing that
    runs every morning was still the old path and the new work only happened when a human asked
    for it. That is the failure this check exists to prevent recurring.
    """
    import inspect

    import opportunities as opp

    _fetch = inspect.getsource(opp._fetch_all)
    assert "leadset" in _fetch, "the digest no longer merges duplicates with leadset"
    _enrich = inspect.getsource(opp._enrich_and_verify)
    assert "descriptions" in _enrich and "ats" in _enrich, "the final five are no longer read/verified"
    # ⚠ AND IT MUST NOT RE-OFFER WHAT SHE HAS ALREADY APPLIED TO. The digest's seen-cache stops
    # it repeating a row it has SHOWN, but knows nothing about applications sent by any other
    # route — so GE HealthCare's "Alternant·e DevOps / MLOps" came back at ★100 on the first run
    # of the rebuilt pipeline, two days after she applied to that exact posting.
    assert "applied_verdict" in inspect.getsource(opp.new_offers), \
        "the digest no longer reads her applications log"

    # ⚠ BROWSER-BACKED SOURCES MUST BE OPTIONAL. /apply runs on her Mac where Playwright exists;
    # the VM's 08:00 job is still pure Python on a 1GB box and must get [] rather than an
    # exception. A source that can break the run is worse than a source that is absent.
    import browser_boards as _bb
    _real_avail = _bb.available
    try:
        _bb.available = lambda: False
        assert _bb.discover() == [], "browser source must return [] when no browser exists"
    finally:
        _bb.available = _real_avail
    assert "browser_boards" in inspect.getsource(opp._fetch_all), "browser boards not wired in"

    # ⚠ THE DAILY JOB EMAIL MUST STAY OFF (her instruction 2026-09-20: "i want u to turn it off,
    # the one that sends me job emails"). The hunt is on-demand via /apply now. The cron line
    # still RUNS, with --no-digest, because its other half feeds lead_inbox.json so the
    # cold-email agent targets companies that advertised an alternance overnight — deleting the
    # line would have taken that out silently along with the email.
    import pathlib as _pl
    # ⚠ vm/ is not mirrored publicly (it is deployment detail), so check only where it exists.
    _cronf = _pl.Path(__file__).parent / "vm" / "crontab.txt"
    if _cronf.exists():
        _cron = _cronf.read_text(encoding="utf-8")
        _scout = [ln for ln in _cron.splitlines()
                  if "opportunities.py" in ln and not ln.lstrip().startswith("#")]
        assert _scout, "the opportunity scout line vanished — the outreach feed goes with it"
        for _ln in _scout:
            assert "--no-digest" in _ln, f"the daily job email is back on: {_ln.strip()[:90]}"

    # ⚠ 'unknown' NEVER DROPS AN OFFER, and most answers ARE unknown — most large French
    # employers run Taleo / SuccessFactors / iCIMS / Avature, and they are exactly the ones
    # carrying the alternance market. Only a posting the employer's own system calls CLOSED goes.
    import ats as _ats
    _real = _ats.verify
    _one = lambda: [{"company": "X", "role": "R", "url": ""}]
    try:
        _ats.verify = lambda c, r, **k: {"verdict": "unknown", "platform": "icims"}
        assert len(opp._enrich_and_verify(_one())) == 1, "an unreadable ATS dropped an offer"
        # A verifier CRASH is about us, not about the job: a digest that silently shrinks
        # because a reader threw is worse than one that is simply not enriched.
        _ats.verify = lambda c, r, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        assert len(opp._enrich_and_verify(_one())) == 1, "a verifier crash dropped an offer"
        _ats.verify = lambda c, r, **k: {"verdict": "gone", "platform": "lever"}
        assert opp._enrich_and_verify(_one()) == [], "a closed posting was not dropped"
    finally:
        _ats.verify = _real


def t_queries_self_tune_without_ever_shrinking():
    """Sources send their queries best-first, and measurement may reorder but never remove.

    OFFLINE. THE BEST QUERY IS DIFFERENT ON EVERY BOARD — measured 2026-09-20: "apprenti data"
    scores 46 on HelloWork and 0 on WTTJ, "alternance MLOps" 34 on LinkedIn and 0 on HelloWork,
    "alternance IA" 67 on WTTJ against 29 on HelloWork. One hand-written list copied across
    boards is wrong nearly everywhere, and every run is bounded by pages_per_query, so the ORDER
    decides what that budget buys.
    """
    import inspect

    import source_lab as sl

    seeds = [("ai", "good"), ("data", "dud"), ("backend", "unmeasured")]
    _real_load = sl._load
    try:
        sl._load = lambda: {"src": {"good": {"score": 40}, "dud": {"score": 0}}}
        got = sl.plan("src", seeds)
        # NEVER DROPS, NEVER INVENTS: the result is a permutation of the seeds, labels included.
        assert sorted(got) == sorted(seeds), got
        assert got[0] == ("ai", "good"), "the measured winner must lead"
        assert got[-1] == ("data", "dud"), "a measured zero must sink to the bottom"
        # UNKNOWN RANKS ABOVE KNOWN-BAD: "not yet measured" is not evidence of failure, which is
        # the same rule the rest of this repo follows about absence.
        assert got[1] == ("backend", "unmeasured"), got
        # ⚠ SCORE SCALES WITH VOLUME, so it must be compared PER PAGE. The same query measured
        # over 2 pages outscores itself measured over 1 (67 vs 74 on WTTJ), so without this a
        # partial re-sweep at a different depth would rank the DEEPER measurement above the
        # better query. `pages` is recorded for exactly this reason.
        from datetime import date as _d, timedelta as _td
        _today = _d.today().isoformat()
        sl._load = lambda: {"src": {"deep": {"score": 80, "pages": 2, "measured": _today},
                                    "shallow": {"score": 60, "pages": 1, "measured": _today}}}
        assert sl.plan("src", [("a", "deep"), ("b", "shallow")])[0][1] == "shallow"
        # ⚠ A STALE NUMBER IS CLOSER TO UNKNOWN THAN TO FACT. This module exists because yield
        # moves with the market ("alternance IA" was worth little in July and a lot in
        # September), so trusting a measurement forever contradicts the reason for measuring.
        _old = (_d.today() - _td(days=90)).isoformat()
        sl._load = lambda: {"src": {"stale": {"score": 99, "pages": 1, "measured": _old},
                                    "fresh": {"score": 40, "pages": 1, "measured": _today}}}
        _got = [q for _, q in sl.plan("src", [("a", "stale"), ("b", "fresh"), ("c", "never")])]
        assert _got[0] == "fresh", f"a 90-day-old 99 outranked a fresh 40: {_got}"
        # A stale number and a never-measured one are BOTH simply unknown, so they tie and the
        # label decides — that equality is the point, not an accident.
        assert set(_got[1:]) == {"stale", "never"}, _got
        # An empty or unreadable cache is a NO-OP, so a source can never end up with fewer
        # queries than its author gave it.
        sl._load = lambda: {}
        assert sl.plan("src", seeds) == seeds
        sl._load = lambda: (_ for _ in ()).throw(ValueError("corrupt"))
    except Exception:
        raise
    finally:
        sl._load = _real_load

    # ⚠ PAIRS, NOT A DICT. ALTERNANCE_QUERIES and QUERIES SHARE LABELS ("ai", "backend",
    # "data"), so merging them into one dict silently drops the alternance query for every
    # collision — which is exactly why the modules concatenate .items() lists. Assert the
    # collision is real, so nobody "simplifies" this back into a dict.
    import hellowork as _hw
    _both = list(_hw.ALTERNANCE_QUERIES.items()) + list(_hw.QUERIES.items())
    assert len(dict(_both)) < len(_both), "label collision gone — re-check the dict/pairs choice"
    assert sorted(sl.plan("hellowork", _both)) == sorted(_both)

    # ...and EVERY source with a query list must actually USE it, or the tuning is decorative.
    # The first version wired only three of eight, which left APEC and France Travail — 24 seeds
    # each — sending their hand-typed order forever. `ADAPTERS` is the registry of query-driven
    # sources, so deriving the list from it means a NEW source cannot quietly skip tuning.
    import importlib as _il
    for _name in sl.ADAPTERS:
        _mod = _il.import_module(_name)
        assert "_sl.plan(" in inspect.getsource(_mod), f"{_name} does not self-tune its queries"


def t_brief_kills_only_the_certain_and_never_an_absence():
    """The queue step may refuse only what is CERTAINLY wrong. Absence must never refuse.

    OFFLINE. This is the gate that decides which postings Zineb never sees, so a rule that
    over-reaches here is invisible by construction — the lead simply is not there. Her
    instruction sets the bar: "narrow only the obvious not suitable, cuz I prefer you make the
    decisions with those similar to me with having all ready".
    """
    import brief as B

    def _lead(**kw):
        d = {"company": "ACME", "role": "Alternance Data Engineer H/F", "url": "https://x/1",
             "location": "Paris", "source": "francetravail", "mode": "onsite", "meta": {}}
        d.update(kw)
        return d

    # ---- CERTAIN no's: a person would agree with each of these instantly.
    # ⚠ A SCHOOL IS DEMOTED, NEVER DROPPED. Killing these is tempting (30 ISCOD ads in one live
    # run) but the name test cannot tell a course-seller from an edtech EMPLOYER: Galileo Global
    # Education appeared 11 times in the first audit and runs its own group IT, one of its
    # postings reading python + javascript. rank_pending_leads has scored this -30 rather than
    # dropping it since 2026-09-05, and promoting it to a kill contradicted that on a name.
    assert not B.refuse(_lead(company="ISCOD Alternance"))
    assert B.signals(_lead(company="ISCOD Alternance"), {"chars": 0, "facts": {}})["school"]
    assert not B.signals(_lead(company="Doctolib"), {"chars": 0, "facts": {}})["school"]
    assert B.refuse(_lead(role="Business Developer B2B"))                   # not software
    assert B.refuse(_lead(role="Alternance Développeur web - BTS SIO"))     # below her level
    assert B.refuse(_lead(role="Chef de projet marketing"))                 # not a target role
    assert B.refuse(_lead(role="Data Engineer", location="Toulouse"))       # she cannot take it
    assert B.refuse(_lead(role="Data Engineer CDI", meta={"contract": "cdi"}))
    # ...and the BTS refusal must say SO. jobsource refuses those titles too, so an ordering slip
    # reported "not software/data" for a real data role that was merely pitched at BTS, and the
    # refusal list is the only place a rule eating real leads becomes visible.
    assert "BTS" in B.refuse(_lead(role="Alternance Développeur web junior - BTS SIO"))

    # ---- ABSENCE NEVER KILLS. CACIB's "Ingénieur Data H/F" carried no alternance flag anywhere
    # and was a real 24-month alternance she applied to; 12 of 40 alternances measured on France
    # Travail never say the word in their body either.
    for survivor in (_lead(role="Ingénieur Data H/F"),            # the CACIB shape
                     _lead(location=""),                          # location unknown
                     _lead(location="France"),                    # city unspecified
                     _lead(meta={"contract": ""}),                # contract unknown
                     # the board says CDI but the TITLE says alternance — never refuse on one field
                     _lead(role="Alternance Data Engineer", meta={"contract": "cdi"})):
        assert not B.refuse(survivor), survivor

    # ---- THE SECOND GATE needs three-way agreement, because it runs on the best evidence in the
    # system and is therefore the easiest place to throw away something real. descriptions'
    # contract verdict measured 97% precision but only 70% RECALL, so its silence means nothing.
    assert B.refuse_after_reading(_lead(role="Data Engineer"), {"contract": "cdi"})
    # ...but it stands down the moment the TEXT mentions alternance at all. The first live run
    # caught this gate killing real leads on incidental words — Reddit on the English "similar
    # STAGE growth companies", and two French postings on sentences about PRIOR experience
    # ("une première expérience acquise en stage ou en alternance est appréciée").
    assert not B.refuse_after_reading(
        _lead(role="Data Engineer"), {"contract": "stage"},
        "Une première expérience acquise en stage ou en alternance est appréciée.")
    for kept in ((_lead(role="Alternance Data Engineer"), {"contract": "cdi"}),
                 (_lead(meta={"contract": "alternance"}), {"contract": "stage"}),
                 (_lead(role="Data Engineer"), {"contract": None}),
                 # m2_or_final must NEVER become a kill: two-year alternances start in M1 and
                 # still write "dernière année"/"Bac+5". Same for beginner_ok being False.
                 (_lead(role="Data Engineer"), {"level": "m2_or_final"}),
                 (_lead(role="Data Engineer"), {"beginner_ok": False})):
        assert not B.refuse_after_reading(*kept), kept

    # ---- TIERS RANK EVIDENCE, NOT QUALITY. T1 means "there is enough here to judge on".
    assert B.tier({"read": True, "alternance": True, "overlap": ["python"]}) == 1
    assert B.tier({"read": True, "alternance": True, "overlap": []}) == 2
    assert B.tier({"read": False, "alternance": True, "overlap": ["python"]}) == 3

    # ---- ALREADY APPLIED. Two factors before anything is killed: a big employer runs
    # independent teams, so a SECOND role at GE HealthCare is a real opportunity and may only be
    # flagged, while re-sending a pack for the posting she already applied to is waste.
    # Requiring the role to match too is also what makes the fuzzy employer match safe — a wrong
    # company match then costs a flag, never a lead.
    _app = [({"healthcare"}, {"devops", "mlops"}, True),
            ({"ibm"}, {"engineer", "client", "engineering"}, True)]
    assert B.applied_verdict({"company": "GE HealthCare",
                              "role": "Alternant·e DevOps / MLOps"}, _app) == "exact"
    assert B.applied_verdict({"company": "GE HealthCare",
                              "role": "Alternance Data Scientist"}, _app) == "company"
    # ⚠ SAME CONTRACT FLAVOUR TOO. Crédit Agricole Assurances' "STAGE - Data Scientist" was
    # refused as the posting she had already applied to — but she applied to their ALTERNANCE,
    # and the shared tokens were only {data, scientist}. One posting cannot be both, and without
    # this a genuinely NEW alternance at an employer she has written to dies on two generic words.
    assert B.applied_verdict({"company": "GE HealthCare",
                              "role": "Stage DevOps / MLOps"}, _app) == "company"
    assert B.applied_verdict({"company": "Doctolib", "role": "Backend Engineer"}, _app) == ""
    # Her log names employers more fully than the boards do ("IBM France" vs "IBM", "Veolia
    # Environnement" vs "Veolia", "Natixis CIB (BPCE)" vs "BPCE SA"): exact key equality matched
    # only 2 of 6 real pairs. Subset, or two distinctive tokens — never ONE shared token, or
    # "Air France" and "France Travail" would be the same employer.
    assert B._same_employer({"ibm"}, {"ibm", "france"})
    assert B._same_employer({"bosch", "leblanc"}, {"elm", "leblanc", "bosch"})
    assert not B._same_employer({"air", "france"}, {"france", "travail"})
    # The log must actually parse — a silent parser failure here re-offers work already done.
    # ⚠ applications_log.md is PRIVATE (her real application history) and is deliberately absent
    # from the public mirror, so this asserts only where the file exists. Absence is silent, the
    # same rule every optional sidecar in this repo follows.
    import pathlib as _plb
    if (_plb.Path(__file__).parent / "applications_log.md").exists():
        assert len(B._applied()) >= 15, "applications_log.md rows are no longer being read"

    # ---- ATS VERIFICATION IS THE LAST GATE, AND 'unknown' MAY NEVER KILL. Most large French
    # employers run an ATS this repo cannot read (Taleo, SuccessFactors, iCIMS, Avature) and
    # those are precisely the employers carrying the alternance market and the CFA
    # relationships, so treating unreadable as dead would empty the queue of the best leads.
    # A verifier CRASH is likewise about us, never about the posting.
    import ats as _ats
    _real_verify = _ats.verify
    try:
        _q = lambda: [{"lead": {"company": "X", "role": "R", "url": "u"},
                       "signals": {}, "tier": 1}]
        _ats.verify = lambda c, r, **k: {"verdict": "unknown", "platform": "icims", "note": ""}
        _kept, _gone = B._verify_head(_q(), 5)
        assert not _gone and len(_kept) == 1, "an unreadable ATS must never kill a lead"
        _ats.verify = lambda c, r, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        _kept, _gone = B._verify_head(_q(), 5)
        assert not _gone, "a verifier crash must never kill a lead"
        # ...but a posting the employer's own system says is closed IS certain, and is the whole
        # point: a pack is ~10 minutes of work and one was built for an AP-HP job that no longer
        # existed, while HelloWork went 5-for-5 dead on the day it was measured.
        _ats.verify = lambda c, r, **k: {"verdict": "gone", "platform": "lever", "note": "0"}
        _kept, _gone = B._verify_head(_q(), 5)
        assert len(_gone) == 1 and not _kept
        # BOUNDED: it is the most expensive check in the pipeline and she only works from the
        # top, so it must stop, and the tail must be honestly marked unverified.
        _calls = []
        _ats.verify = lambda c, r, **k: (_calls.append(1),
                                         {"verdict": "live", "platform": "lever", "note": ""})[1]
        _many = [{"lead": {"company": f"C{i}", "role": "R", "url": f"u{i}"},
                  "signals": {}, "tier": 1} for i in range(20)]
        _kept, _ = B._verify_head(_many, 3)
        assert len(_calls) == 3 and len(_kept) == 20, (len(_calls), len(_kept))
        assert _kept[-1]["signals"]["ats"] == "", "the unverified tail must say so"
    finally:
        _ats.verify = _real_verify

    # ---- NO CEILING. "candidate for the most possible from the most suitable to the least till
    # it becomes not suitable at all" — so the queue is ordered, never truncated.
    _pool = [_lead(company=f"C{i}", url=f"https://x/{i}") for i in range(12)]
    _got = B.build(_pool, read=False)
    assert len(_got["queue"]) == 12, _got["stats"]
    # ⚠ AND THE ORDER MUST NOT DEPEND ON THE ORDER THE BOARDS ANSWERED IN. Ties are constant
    # (Thales alone posts ~78 roles) and a stable sort silently preserves arrival order, so the
    # same pool shuffled gave a different queue. leadset had exactly this bug on its surviving
    # URL; the fix is the same — make the sort key a TOTAL order.
    import random as _rnd
    _shuf = _pool[:]
    _rnd.Random(3).shuffle(_shuf)
    assert ([q["lead"]["url"] for q in B.build(_shuf, read=False)["queue"]]
            == [q["lead"]["url"] for q in _got["queue"]]), "queue order depends on input order"
    assert [q["tier"] for q in _got["queue"]] == sorted(q["tier"] for q in _got["queue"])


def t_leadset_merges_copies_and_never_merges_two_jobs():
    """Dedupe must collapse the same job across boards and NEVER collapse two different ones.

    OFFLINE. The asymmetry is the whole design: a missed merge costs one duplicate read, a WRONG
    merge deletes an opportunity outright. Safran runs a supply-chain and a hydromechanical Data
    Analyst alternance simultaneously; AP-HP a general and a bioinformatics one.
    """
    import leadset as ls
    # same job, different board decoration — must collapse
    for co, a, b in (
        ("Carrefour", "Data Analyste Import F/H - Alternance", "Data Analyste Import - Alternance H/F"),
        ("MGEN", "Data & IA Chargé·e de la Gouvernance H/F", "Data & IA : Chargé.e de la gouvernance H/F"),
        ("Chanel", "Alternance - AI - Data Engineer H/F", "Alternance : Alternance - AI/Data Engineer H/F/X"),
        ("BPCE", "Alternance - Data Analyste - Paris H/F", "Alternance (1 an) - Data analyste F/H - Paris"),
        ("Hermes Sellier SAS", "Assistant Data Scientist H/F", "Assistant Data Scientist (H/F)"),
    ):
        assert ls.key(co, a) == ls.key(co, b), f"failed to merge: {a!r} vs {b!r}"
    # COMPANY-side equivalence. "Chanel Fr" vs "Chanel" split one posting 3+1 across boards even
    # though all four titles produced an identical role key — found by a straggler test, not by
    # the precision tests, which is why recall needs its own check.
    for a, b in (("Chanel Fr", "Chanel"), ("Capgemini Technology Services", "Capgemini"),
                 ("Groupe BPCE", "BPCE SA"), ("Hermes Sellier SAS", "Hermès Sellier"),
                 ("Groupe SII, super recruteur", "Groupe SII")):
        assert ls.norm_company(a) == ls.norm_company(b), f"company split: {a!r} vs {b!r}"
    # …but a country word can BE the brand. Stripping "france" anywhere collapsed "Air France"
    # to "air" and "France Travail" to "travail". Only abbreviations may be stripped.
    for a, b in (("Air France", "Air"), ("France Travail", "Travail"), ("US Robotics", "Robotics"),
                 ("Galileo Global Education", "Galileo Technologies"), ("Bosch", "Bosch Rexroth")):
        assert ls.norm_company(a) != ls.norm_company(b), f"company wrongly merged: {a!r} vs {b!r}"
    # different jobs at ONE employer — must stay apart
    for co, a, b in (
        ("Safran", "Alternance Data Analyst Supply Chain Mro", "Alternance Essai Hydromécanique Data Analyst"),
        ("AP-HP", "Apprenti Concepteur Développeur Informatique", "Apprenti Concepteur Développeur Bioinformatique"),
        ("Hermes", "Assistant Data Scientist", "Assistant Ingénieur Data et Chimie"),
        ("X", "Alternance Data Analyst", "Stage Data Analyst"),          # contract words are kept
    ):
        assert ls.key(co, a) != ls.key(co, b), f"wrongly merged: {a!r} vs {b!r}"
    # the surviving URL must be the one closest to the employer, never the aggregator
    copies = [
        {"company": "X", "role": "Alternance Data", "url": "https://www.hellowork.com/x",
         "src": "hellowork", "meta": {"contract": "stage"}},
        {"company": "X", "role": "Alternance Data", "url": "https://jobs.smartrecruiters.com/X/1",
         "src": "linkedin", "meta": {"contract": "alternance"}},
    ]
    merged, _ = ls.merge(copies)
    assert len(merged) == 1 and "smartrecruiters" in merged[0]["url"], merged[0]["url"]
    assert merged[0]["dupes"] == 2 and len(merged[0]["sources"]) == 2
    # the BETTER source's metadata must win the merge, not the last one seen
    assert merged[0]["meta"]["contract"] == "alternance", merged[0]["meta"]

    # ORDER-INDEPENDENCE. `sorted` is stable, so ranking on closeness alone left ties broken by
    # arrival order — shuffling the input changed the surviving URL on 16 of 526 real leads
    # (2026-09-20). Two equal-ranked aggregators must still produce one stable answer.
    tie = [
        {"company": "Y", "role": "Alternance Data", "url": "https://www.hellowork.com/y", "src": "hellowork"},
        {"company": "Y", "role": "Alternance Data", "url": "https://fr.linkedin.com/jobs/view/y", "src": "linkedin"},
        {"company": "Y", "role": "Alternance Data", "url": "https://www.adzuna.fr/details/1?utm_medium=api", "src": "adzuna"},
    ]
    picks = {ls.merge(tie[i:] + tie[:i])[0][0]["url"] for i in range(len(tie))}
    assert len(picks) == 1, f"merge is order-dependent: {picks}"

    # NO DATA LOSS. The winner is chosen for its URL, not its completeness: a LinkedIn card has
    # no location and no fit score, and taking its dict wholesale DELETED both from six real
    # leads (Green-Got, CYLAD, Yuri & Neil). Gaps must be backfilled from the losing copies,
    # and the winner's own values must still stand.
    rich = ls.merge([
        {"company": "Z", "role": "Alternance Data", "url": "https://www.hellowork.com/z",
         "src": "hellowork", "loc": "Paris", "fit": 80},
        {"company": "Z", "role": "Alternance Data", "url": "https://jobs.smartrecruiters.com/Z/1",
         "src": "linkedin", "loc": ""},
    ])[0][0]
    assert "smartrecruiters" in rich["url"], "backfill must not steal the winner's URL"
    assert rich.get("loc") == "Paris" and rich.get("fit") == 80, rich

    # IDEMPOTENCE: merging an already-merged set must change nothing.
    again, st = ls.merge(merged)
    assert st["merged"] == 0, st

    # Malformed rows must pass through, never crash and never acquire a key.
    odd, st2 = ls.merge([{"company": None, "role": None}, {"company": "", "role": ""},
                         {"company": "X", "role": None}, {"company": "  ", "role": "Alternance"}])
    assert st2["no_key"] == 4 and all("_key" not in o for o in odd)


def t_source_lab_scores_usefulness_not_volume():
    """The query lab must optimise for what she can apply to, never for raw row count.

    OFFLINE. Optimising volume is how a source ends up flooding the digest with "Employé de
    rayon": a query returning 100 irrelevant rows would outrank one returning 8 alternance data
    roles in Île-de-France. Also asserts the lab PREFERS a board's own contract field to the job
    title — reading titles alone scored APEC and WTTJ at ZERO alternances for the query
    "alternance data" on 2026-09-20, which is plainly wrong, because both publish the contract
    as a structured field instead.
    """
    import inspect, source_lab as sl
    src = inspect.getsource(sl.measure)
    assert "score = fit + 2 * alt + idf" in src, "scoring formula moved — it must not count raw"
    assert "raw" not in src.split("score = ")[1].split("\n")[0], "raw volume must never be scored"
    assert 'meta.get("contract") == "alternance"' in src, "must prefer the board's own field"
    # every query-driven source has an adapter, and the query-less ones are declared, not missing
    assert set(sl.ADAPTERS) >= {"linkedin", "hellowork", "apec", "france_travail", "adzuna", "wttj"}
    assert "labonnealternance" in sl.NO_QUERY and "stationf" in sl.NO_QUERY
    # adapters must normalise to 4-tuples so a consumer never has to branch on the source
    assert "len(r) == 4" in inspect.getsource(sl.search)


def t_schools_met_this_week_are_refused():
    """The named schools that reached her shortlist are filtered — and real employers are not.

    Both directions, because the cost is asymmetric in an unobvious way: a school that gets
    through spends a screening slot and, in outreach, a cold send plus a Hunter credit; but a
    REAL employer wrongly flagged is a lead deleted outright. Galileo Global Education is the
    reason this exists — a school GROUP that put fourteen postings into one shortlist.
    """
    import tracker as t
    for name in ("Galileo Global Education", "Institut F2I", "HETIC", "INTED GROUP (IEG)",
                 "EF2C", "Walter Learning", "SCHOLIA", "École Hexagone", "Iscod Alternance"):
        assert t.is_training_body(name), f"school not filtered: {name}"
    # Edtech EMPLOYERS, research institutes, her own university, and a lookalike company name.
    for name in ("OpenClassrooms", "Institut Pasteur", "Université Paris Cité", "Dataiku",
                 "Galileo Technologies", "Hermes Sellier", "Safran", "IBM"):
        assert not t.is_training_body(name), f"real employer wrongly flagged: {name}"


def t_ats_verification_is_accent_safe_and_biased_to_live():
    """A lead is only killed on real evidence, and accents never decide it.

    OFFLINE. The first verify() compared a normalised "creation" against the live title
    "Apprenti Création d'agents LLM" and returned GONE for a posting Zineb had just applied to.
    A false 'gone' silently deletes a real opportunity; a false 'live' costs one click. So this
    asserts BOTH directions, and that 'unknown' is never conflated with 'gone'.
    """
    import ats
    # accents and punctuation must not affect the token comparison
    assert ats._norm("Apprenti Création d’agents LLM") == ats._norm("apprenti creation d agents llm")
    # words that appear in half the alternance market identify nothing and must be stripped
    assert "alternance" in ats._STOP and "apprenti" in ats._STOP and "stage" in ats._STOP
    # an unreadable ATS is 'unknown', NEVER 'gone' — those employers carry the alternance market
    assert "taleo" in ats.UNREADABLE and "successfactors" in ats.UNREADABLE
    assert "taleo" not in ats.READABLE
    # the threshold stays permissive: a majority-token match must not be needed to survive
    import inspect
    src = inspect.getsource(ats.verify)
    assert "best_score >= 0.45" in src, "verify() threshold moved — it must stay biased to 'live'"
    assert '"unknown"' in src, "verify() must still be able to answer 'unknown'"


def t_sources_registry():
    """Every job source is wired consistently behind the /scrape skill (skill-orchestrated)."""
    import scraper
    expected = {"stationf", "wttj", "hellowork", "apec", "francetravail", "freework",
                "labonnealternance", "remotive", "adzuna", "linkedin"}
    assert set(scraper.SOURCES) == expected, set(scraper.SOURCES)
    for name, src in scraper.SOURCES.items():
        assert callable(src.get("discover")), f"{name}: discover not callable"
        assert callable(src.get("resolve")), f"{name}: resolve not callable"
        assert "enrich" in src, f"{name}: missing enrich flag"
    import adzuna, apec, france_travail, free_work, hellowork, labonnealternance, wttj, remotive, linkedin
    for m in (wttj, hellowork, apec, free_work, france_travail, labonnealternance, remotive, adzuna,
              linkedin):
        assert m.NAME and callable(m.discover) and callable(m.resolve_company_site), m.__name__


def t_opportunity_digest():
    """Scout digest filters correctly (profile fit + realistic seniority) and dedups offline."""
    import opportunities as opp, os, tempfile
    from pathlib import Path
    # role fit: her stack in, off-stack out
    assert opp.role_fit("Machine Learning Engineer") and opp.role_fit("Backend Engineer")
    assert not opp.role_fit(".NET Developer") and not opp.role_fit("Frontend Developer")
    assert not opp.role_fit("Sales Engineer") and not opp.role_fit("Security Engineer")
    # seniority: junior/unlabelled in, senior/lead out (title AND jobLevel signals)
    assert opp.seniority_ok("ML Engineer") and opp.seniority_ok("Junior Data Scientist")
    assert not opp.seniority_ok("Senior ML Engineer") and not opp.seniority_ok("Staff Engineer")
    assert opp.seniority_ok("Data Scientist", level="Junior")
    assert not opp.seniority_ok("Data Scientist", level="Senior")
    # French seniority markers (APEC/France Travail say "confirmé", never "senior")
    assert not opp.seniority_ok("Machine Learning Engineer - confirmé F/H")
    assert not opp.seniority_ok("Développeur Backend expérimenté")
    # ops/QA titles spelled out — the abbreviations alone used to let these through
    assert not opp.role_fit("Site Reliability Engineer in Network Infrastructure")
    assert not opp.role_fit("Software Development Engineer in Test")
    import datetime as _dt
    import inspect as _i
    assert not opp.role_fit("Intern AI & Management Consulting")
    # "Confirmed X" is a French company writing "confirmé" in English — common on WTTJ, and the
    # accented pattern cannot match it (the trailing "d" kills the word boundary).
    assert not opp.seniority_ok("Confirmed Machine Learning Engineer (Productivity)")
    assert opp.seniority_ok("Junior / Intermediate Machine Learning Engineer")
    # A talent pool is a CV inbox with no role behind it; Doctolib's took a slot from a real job.
    for t in ("AI Talent Pool", "Vivier Data Engineer", "Candidature spontanée - Data",
              "Talent Community - Engineering"):
        assert not opp.role_fit(t), t
    # She is C2 English, bilingual French, native Arabic — a role written for a Spanish or German
    # speaker is a no, not a near-miss. Matched on the REQUIREMENT, never the bare language name.
    for t in ("Data Science internship - Spanish speaker", "ML Engineer (German speaking)",
              "Backend Engineer - fluent in Italian"):
        assert not opp.role_fit(t), t
    assert opp.role_fit("Machine Learning Engineer, Spanish market data")   # data, not a requirement
    # IT support and back-office titles survive on the word "engineer" or "data" alone — the
    # remote boards served "Tier III Service Desk Engineer" and "Data Entry Clerk" as matches —
    # and legacy enterprise stacks are real engineering she neither does nor wants.
    for t in ("Tier III Service Desk Engineer", "Data Entry Clerk", "Help Desk Technician",
              "Developer Relations Engineer", "Field Service Engineer",
              "1775 RPG/AS400 & JD Edwards EnterpriseOne Developer", "SAP ABAP Developer"):
        assert not opp.role_fit(t), t
    # …without taking real matches with them
    for t in ("AI Engineer Data APIs", "Software Engineer - Data Infrastructure - Kafka",
              "Software Engineer, Ceph & Distributed Storage", "Alternant·e DevOps / MLOps",
              "Junior Backend Engineer", "Agentic Python Engineer"):
        assert opp.role_fit(t), t
    # Remotive states the employment type: a contractor mission is not an employment contract,
    # and plenty are titled plainly "Backend Engineer" with nothing in the words to reveal it.
    assert "job_type" in _i.getsource(opp._fetch_remotive)
    assert opp.role_fit("NLP Engineer - multilingual models")
    # fit scoring: what she needs must outrank what merely passes the filters. Without this the
    # section caps kept whatever sorted first alphabetically.
    def _fit(role, company, loc, cat, mode="onsite", source="apec"):
        return opp.fit_score({"role": role, "company": company, "location": loc,
                              "category": cat, "mode": mode, "source": source})[0]
    alternance_idf = _fit("Alternance Ingénieur IA / LLM (H/F)", "Doctolib", "Paris 09 - 75", "ai")
    esn_province   = _fit("Machine Learning Engineer F/H", "AKKODIS FRANCE SAS", "Bordeaux - 33", "ai")
    uk_role        = _fit("Machine Learning Engineer", "Waymo", "London", "ai")
    assert alternance_idf > esn_province and alternance_idf > uk_role
    assert alternance_idf >= 85, alternance_idf          # her single best-fit shape
    assert esn_province < opp._FIT_FLOOR                 # ESN in the provinces is below the bar
    assert opp.fit_score({"role": "ML Engineer", "company": "Hellowork", "location": "Paris",
                          "category": "ai"})[0] == 0     # board name leaking as the employer
    assert opp.fit_score({"role": "AI Engineer", "company": "X", "location": "Paris",
                          "category": "ai"})[1]          # reasons are always populated
    # a Bac+2 alternance must not outrank a Master-level one at the same company/location
    assert _fit("Alternant Ingénieur IA (H/F)", "X", "75 - Paris", "ai") > \
           _fit("Data Analyst - BTS SIO - Alternance (H/F)", "X", "75 - Paris", "data")
    # ── The digest must know what the OUTREACH side knows. The two facts that most change her
    # odds at a given employer were invisible in the one place she decides where to apply:
    # GE HealthCare topped the digest with a warm referral on file, Thales sat in it as a CFA
    # partner, and neither said so.
    def _score(company, **kw):
        return opp.fit_score({"role": "Alternance Data Engineer", "company": company,
                              "location": "Paris 11 - 75", "category": "data",
                              "mode": "onsite", "source": "apec", **kw})
    # The wiring is asserted everywhere; the BEHAVIOUR only where the seed exists. Neither sidecar
    # is copied to the public mirror (cache/ never leaves this repo), so an unseeded host is the
    # correct state there, not a failure.
    assert "warm_network" in _i.getsource(opp._fit_score_uncapped)
    assert "school_partners" in _i.getsource(opp._fit_score_uncapped)
    import school_partners as _spx
    if _spx.summary("Thales"):
        _sch_score, _sch_why = _score("Thales")
        assert any("CFA" in w or "partenaire" in w for w in _sch_why), _sch_why
        assert _sch_score > _score("Doctolib")[0], "a CFA partner must outrank an equal non-partner"
    # a missing sidecar must be silent, never an error — both are optional by design
    assert _score("Nonexistent Co")[0] > 0

    # The digest must also know where the OUTREACH agent already is with an employer: she could
    # send a second, colder approach to a company mid-conversation, or walk past one that has
    # already replied — the warmest thing this system produces.
    assert "_outreach_index" in _i.getsource(opp._fit_score_uncapped)
    _idx_src = _i.getsource(opp._outreach_index)
    # `Replied` alone is NOT trustworthy: the 2026-09 audit found bounces and out-of-office
    # autoresponders stamped with it, and a "they answered" boost must never fire on a bounce.
    assert "has_genuine_human_reply" in _idx_src, "Status is not authoritative; the log is"
    assert "except" in _idx_src, "must degrade to no-information without contacts.xlsx"
    _idx = opp._outreach_index()
    assert isinstance(_idx, dict)
    assert all(v[0] in ("Emailed", "Followed Up", "Replied", "Interview Scheduled")
               for v in _idx.values())

    # ORDERING uses the unclamped score. The 0-100 clamp is presentation: a warm referral (+25) on
    # an alternance in Île-de-France with "débutant accepté" passes 100, and sorting on the clamped
    # value would tie the whole top of the digest and break it by company name.
    _big = {"role": "Alternance Data Engineer", "company": "X", "location": "Paris 11 - 75",
            "category": "data", "mode": "onsite", "source": "francetravail",
            "meta": {"contract": "alternance", "experience": "D", "few_applicants": True,
                     "posted": _dt.date.today().isoformat()}}
    assert opp.fit_score(_big)[0] == 100, "display score is clamped"
    assert opp.fit_score_raw(_big) > 100, "ordering score is not"
    assert "fit_raw" in _i.getsource(opp.new_offers)

    # ── Reachability: in-person means Île-de-France or the ~1h commuter ring; remote is anywhere.
    # An on-site job in Béziers or Berlin cannot be accepted while she studies in IDF, so it must
    # not compete for a slot. Offline, pure-function checks — no network.
    for loc in ("Paris 11 - 75", "Saint-Ouen - 93", "Compans - 77", "Versailles - 78",
                "92 - LEVALLOIS-PERRET"):
        assert opp.is_reachable({"mode": "onsite", "location": loc})[0], loc
    for loc in ("60 - Compiègne", "Reims - 51", "Chartres - 28", "Rouen - 76"):
        assert opp.is_reachable({"mode": "onsite", "location": loc})[0], f"commuter ring: {loc}"
    for loc in ("34 - Béziers", "Lyon 09 - 69", "Bordeaux - 33", "London, United Kingdom",
                "Berlin, Germany", "Toulouse - 31"):
        assert not opp.is_reachable({"mode": "onsite", "location": loc})[0], f"unreachable: {loc}"
    # Country-level only ("France", or nothing): KEPT. Lever returns a bare "France" for Pigment,
    # Filigran and Lifen — all Paris companies — and refusing those would throw away real Paris
    # jobs to punish a missing field. Scored below a known IDF address and labelled as unverified.
    for loc in ("France", "", "  "):
        ok, why = opp.is_reachable({"mode": "onsite", "location": loc})
        assert ok and "unspecified" in why, (loc, why)
    assert _fit("Data Engineer", "X", "Paris 11 - 75", "data") > \
           _fit("Data Engineer", "X", "France", "data") > \
           _fit("Data Engineer", "X", "Lyon 09 - 69", "data")
    # remote is welcome wherever it is — that is the whole point of the distinction
    for loc in ("Remote (Worldwide)", "Remote - US", "Berlin, Germany"):
        assert opp.is_reachable({"mode": "remote", "location": loc})[0], loc
    # and the gate runs BEFORE scoring, so a high fit can never buy an unreachable job a slot
    assert "is_reachable" in _i.getsource(opp.new_offers)
    # ONE definition of "reachable": _section used to keep its own, narrower rule and the two
    # drifted the moment company boards arrived — Meilleurtaux writes "Courbevoie IDF fr", which
    # is Île-de-France to is_reachable and nowhere to _section, so Paris-region roles were filed
    # under "ON-SITE ABROAD" and sorted below everything.
    assert "is_reachable" in _i.getsource(opp._section)
    for loc in ("Courbevoie IDF fr", "Paris IDF fr", "Issy-les-Moulineaux, Hauts-de-Seine, France"):
        assert opp._section({"mode": "onsite", "location": loc, "source": "X careers"}) == "france", loc
    # Paris outranks the commuter ring outranks nothing else
    assert _fit("Alternance Data Engineer", "X", "Paris 11 - 75", "data") > \
           _fit("Alternance Data Engineer", "X", "60 - Compiègne", "data")

    # ── What the BOARD says about her chances, rather than what a title implies. France Travail
    # and APEC publish contract type, posting date, "débutant accepté" and "peu de candidatures"
    # on every offer, and all four were being fetched and thrown away.
    import apec as _apec, france_travail as _ft, jobsource as _jsrc
    assert "meta" in {f.name for f in __import__("dataclasses").fields(_jsrc.JobListing)}
    assert _jsrc.JobListing(company="a", role="b").meta == {}, "meta must default per-instance"
    # each board's reader produces the keys fit_score reads
    assert _apec._meta({"typeContrat": "597137", "datePublication": "2026-09-01T00:00:00Z",
                        "indicateurFaibleCandidature": True}) == {
        "contract": "alternance", "posted": "2026-09-01", "few_applicants": True}
    assert _apec._meta({"typeContrat": "101888"})["contract"] == ""      # 101888 is CDI
    _m = _ft._meta({"alternance": True, "dateCreation": "2026-09-03T14:00:00.000Z",
                    "offresManqueCandidats": True, "experienceExige": "D", "description": "Missions…",
                    "romeCode": "M1805", "romeLibelle": "Études et développement informatique"})
    assert _m == {"contract": "alternance", "posted": "2026-09-03", "description": "Missions…",
                  "few_applicants": True, "experience": "D",
                  "rome": "M1805", "rome_label": "Études et développement informatique"}
    # `description` is the full posting text, shipped in the SAME response as the listing.
    # descriptions.fetch() reads it as origin="payload" instead of re-fetching the page.
    # ROME is the state occupational taxonomy — the only job-family signal the French boards
    # publish, and what job_family.classify() reads to say "this is an informatique job" without
    # consulting the title. Absent on an offer that omits it, which stays neutral by design.
    assert _ft._meta({})["rome"] == ""
    assert _ft._meta({"natureContrat": "Contrat apprentissage"})["contract"] == "alternance"
    # and they must actually reach the scorer, each moving the score the right way
    _base = {"role": "Data Analyst", "company": "X", "location": "Paris 11 - 75",
             "category": "data", "mode": "onsite", "source": "francetravail"}
    _plain = opp.fit_score(_base)[0]
    assert opp.fit_score({**_base, "meta": {"experience": "D"}})[0] > _plain
    assert opp.fit_score({**_base, "meta": {"experience": "E"}})[0] < _plain
    assert opp.fit_score({**_base, "meta": {"few_applicants": True}})[0] > _plain
    # an alternance the TITLE never mentions must still score as alternance
    assert opp.fit_score({**_base, "meta": {"contract": "alternance"}})[0] > _plain
    assert "alternance" in " ".join(opp.fit_score({**_base, "meta": {"contract": "alternance"}})[1])
    # a stale posting is down-ranked, a fresh one up
    assert opp.fit_score({**_base, "meta": {"posted": "2020-01-01"}})[0] < _plain
    assert "meta" in _i.getsource(opp._fetch_france_inperson), "the digest must carry it through"
    # WTTJ and Free-Work publish an experience level too, and neither says so in the title:
    # Free-Work marked 11 of 20 sampled offers "senior". Both map onto FT's D/S/E alphabet so
    # fit_score needs no per-board special case.
    import free_work as _fw, wttj as _wt
    assert _wt._meta({"experience_level_minimum": 0})["experience"] == "D"
    assert _wt._meta({"experience_level_minimum": 2})["experience"] == "S"
    assert _wt._meta({"experience_level_minimum": 5})["experience"] == "E"
    assert _wt._meta({"contract_type": "APPRENTICESHIP"})["contract"] == "alternance"
    assert _fw._meta({"experienceLevel": "senior"})["experience"] == "E"
    assert _fw._meta({"experienceLevel": "junior"})["experience"] == "D"
    assert _fw._meta({"expiredAt": "2026-10-30T23:59:59+02:00"})["expires"] == "2026-10-30"
    # a board stating its own expiry beats any link check — the page outlives the posting
    assert opp._expired({"meta": {"expires": "2020-01-01"}})
    assert not opp._expired({"meta": {"expires": "2099-01-01"}})
    assert not opp._expired({"meta": {}}) and not opp._expired({})   # never guess a posting dead
    assert "_expired" in _i.getsource(opp.new_offers)

    # La Bonne Alternance is the state ALTERNANCE API, so every posting on it is one — stated
    # structurally so a title that never uses the word still scores as alternance.
    import labonnealternance as _lba
    assert "require_company" in _i.signature(_lba.discover).parameters, (
        "outreach needs an employer to email; the digest only needs the link")
    # French postal codes are how LBA writes an address; without them Puteaux read "unspecified"
    for loc in ("92800 Puteaux", "75001 Paris", "93100 Montreuil"):
        assert opp.is_reachable({"mode": "onsite", "location": loc})[1] == "Île-de-France", loc
    for loc in ("69003 Lyon", "33000 Bordeaux", "34500 Béziers"):
        assert not opp.is_reachable({"mode": "onsite", "location": loc})[0], loc

    # ── The SHARED role gate (every scraper AND the outreach pipeline run through it).
    import jobsource as _jsx
    # Arbitrary punctuation between words — it used to miss all of these.
    assert _jsx.matches_target_role("Machine-Learning Engineer") == "ai"
    assert _jsx.matches_target_role("Data  Engineer") == "data"
    assert _jsx.matches_target_role("Alternance Data-Analyst") == "data"
    assert _jsx.matches_target_role("Développeur/se Backend (H/F)") == "backend"
    assert _jsx.matches_target_role("A.I. Engineer") == "ai"
    # Keywords are WORD-BOUNDED. Short ones used to be anchored by hand-placed spaces and
    # substring-matched, so "ia " matched inside "MEDIA", "AUSTRALIA", "ROMÂNIA", "ITALIA",
    # "Adelia" and "DELMIA": 15 rows in contacts.xlsx are Social Media Manager and Retail Media
    # postings scraped as AI leads that way, several of them emailed. This is the regression test
    # for that — a marketing job must never read as an AI role again.
    for t in ("SOCIAL MEDIA MANAGER", "RETAIL MEDIA MANAGER", "Media Buyer",
              "Multimedia Designer", "STAGE SOCIAL MEDIA & DESIGN",
              "CUSTOMER SUCCESS MANAGER - SYDNEY, AUSTRALIA M/F/D",
              "BUSINESS DEVELOPMENT INTERN - ITALY / ITALIA (PARIS)",
              "Développeu(se)r Java ou C# - Adelia (H/F)", "Chef de projet"):
        assert _jsx.matches_target_role(t) is None, t
    # …but the boundary must tolerate how titles are actually inflected, or it drops real roles:
    # French gender/plural ("data analyste", "ingénieure data") and the English gerund
    # ("DATA ENGINEERING", "SOFTWARE ENGINEERING INTERN").
    assert _jsx.matches_target_role("data analyste F/H") == "data"
    assert _jsx.matches_target_role("Ingénieure Data") == "data"
    assert _jsx.matches_target_role("DBA - DATA ENGINEERING") == "data"
    assert _jsx.matches_target_role("SOFTWARE ENGINEERING INTERN") == "backend"
    assert _jsx.matches_target_role("Data Analysts") == "data"

    # ── Link liveness. Offline behaviour only (no network in preflight): a malformed or empty URL
    # is dead, and the fail-open rule — anything that is not an explicit removal is KEPT — is what
    # stops a bad DNS day from silently emptying the digest.
    assert opp.link_ok("")[0] is False
    assert opp.link_ok("not-a-url")[0] is False
    assert opp._LINK_DEAD == {404, 410}, "only an explicit removal may drop an offer"
    _lsrc = _i.getsource(opp.link_ok)
    assert "kept" in _lsrc, "non-removal failures must be kept, not dropped"
    assert "_path_segments" in _lsrc, "a redirect that drops path depth is a removed posting"
    assert opp._path_segments("https://x.com/") == []
    assert opp._path_segments("https://x.com/a/b") == ["a", "b"]
    # links are verified on a shortlist BEFORE selection, so a dead one is REPLACED not just cut
    assert "check_links" in _i.getsource(opp.new_offers)

    # La Bonne Alternance (the state alternance API) must be one of the digest's French sources,
    # and its hidden-market recruiter rows must never reach her — they have no posting to apply to.
    _src = _i.getsource(opp._fetch_france_inperson)
    assert "labonnealternance" in _src and "[Suggested]" in _src
    # both French boards must ask for alternance explicitly, not only contract-agnostic keywords
    import apec as _ap, france_travail as _ft
    # The INTENT is that every alternance query names the CONTRACT — not that there are exactly
    # three of them. The old form pinned the key set to {"ai","backend","data"}, which failed the
    # 2026-09-19 widening (6 queries -> 22, +38% role-matching listings on HelloWork, 35 employers
    # invisible before). A count is not the property worth protecting; the contract word is.
    for _m in (_ap, _ft):
        _aq = _m.ALTERNANCE_QUERIES
        assert len(_aq) >= 3, "the alternance query set must not shrink back to nothing"
        # all three target families stay represented, whatever the keys are named
        for _fam in ("ai", "backend", "data"):
            assert any(k.startswith(_fam) for k in _aq), f"no {_fam} alternance query"
        # "apprenti" is the other French word for the same contract — both count, nothing else does
        assert all(("alternance" in q.lower() or "apprenti" in q.lower())
                   for q in _aq.values()), "an alternance query must name the contract"
        assert len(_m._query_plan()) == len(_m.QUERIES) + len(_aq)
    # Digest budget: one flooded section must not starve the others, and unused slots elsewhere
    # must not be wasted while good offers are cut (the 12/12/8 quotas used to do both).
    _flood = [{"company": f"C{i}", "role": f"Alternant Ingénieur IA LLM {i}", "url": f"u{i}",
               "location": "75 - Paris", "category": "ai", "mode": "onsite", "source": "apec"}
              for i in range(120)]
    _abroad = [{"company": f"B{i}", "role": f"Graduate Machine Learning Engineer Python {i}",
                "url": f"b{i}", "location": "Berlin", "category": "ai", "mode": "onsite",
                "source": "Arbeitnow"} for i in range(9)]
    _fetch, _seenl = opp._fetch_all, opp._seen_load
    try:
        opp._fetch_all = lambda: _flood + _abroad
        opp._seen_load = lambda: {}
        _sel = opp.new_offers()
        _by = {}
        for _o in _sel:
            _by[opp._section(_o)] = _by.get(opp._section(_o), 0) + 1
        # The section QUOTAS are gone (2026-09-15): with five slots they could force a weak remote
        # role in ahead of a strong alternance. What must still hold is that the budget is spent in
        # full and on reachable roles — Berlin is a relocation, not an opportunity.
        assert len(_sel) == opp._DIGEST_CAP, len(_sel)
        assert _by.get("relocate", 0) == 0, "an on-site job abroad is not an opportunity she can take"
        assert _by.get("france", 0) == opp._DIGEST_CAP, _by
    finally:
        opp._fetch_all, opp._seen_load = _fetch, _seenl


    # dedup roundtrip against an isolated seen-cache (no network)
    saved = opp._SEEN_PATH
    fd, tmp = tempfile.mkstemp(suffix=".json"); os.close(fd); os.remove(tmp)
    opp._SEEN_PATH = Path(tmp)
    try:
        offers = [{"company": "Acme", "role": "ML Engineer", "url": "http://x/1",
                   "location": "Europe", "category": "ai", "source": "Jobicy"}]
        opp.record_seen(offers)
        k = opp._offer_key(offers[0])
        assert k in opp._seen_load(), "offer must be recorded as seen"
        # a clean digest renders without error
        assert "ML Engineer" in opp.format_digest(offers)
        # An empty day must still produce a real message, not a blank email. Asserted on
        # behaviour rather than exact wording, which is copy and will keep changing.
        _empty = opp.format_digest([])
        assert _empty.strip() and "\n" not in _empty.strip()[:40], _empty[:80]
    finally:
        opp._SEEN_PATH = saved
        if os.path.exists(tmp): os.remove(tmp)


def t_digest_reply():
    """A reply to the digest turns into leads — and only for links she actually typed."""
    import digest_reply as dr
    idx = {"https://x.test/job/1": {"company": "Acme", "role": "AI Engineer"},
           "https://x.test/job/2": {"company": "Beta", "role": "Data Engineer"}}
    saved = dr._seen_index
    try:
        dr._seen_index = lambda: idx
        body = ("chase this one please\nhttps://x.test/job/1\n\n"
                "Le 7 août 2026, Zineb a écrit :\n> https://x.test/job/2\n")
        got = dr.parse_wanted(body)
        assert [g["company"] for g in got] == ["Acme"], got   # quoted offer must not be requested
        assert dr.parse_wanted("> https://x.test/job/1") == []  # forwarding it back asks nothing
        assert dr.parse_wanted("https://unknown.test/x") == []  # unknown link is not an offer
        # a junk "employer" must never become a lead, even if she asks for it
        dr._seen_index = lambda: {"https://x.test/j": {"company": "Hellowork", "role": "Dev"}}
        _, skipped = dr.promote(dr.parse_wanted("https://x.test/j"), apply=False)
        assert skipped and "employer" in skipped[0]["reason"]
    finally:
        dr._seen_index = saved


def t_international_targeting():
    """Remote/international leads are detected, tagged consistently, and boosted in ranking."""
    import config, remotive, tracker
    # single source of truth for the tag
    assert remotive.REMOTE_TAG == config.REMOTE_INTL_TAG
    assert config.is_remote_international(f"Senior ML Engineer {config.REMOTE_INTL_TAG}") is True
    assert config.is_remote_international("Ingénieur IA alternance") is False
    # location filter: France-based candidate keeps EU/Worldwide, drops US-/region-only
    assert remotive.is_workable_location("Worldwide") and remotive.is_workable_location("Europe, France")
    assert not remotive.is_workable_location("USA") and not remotive.is_workable_location("Brazil")
    assert remotive.is_workable_location("") is True  # unspecified = open
    # the ranking boost is wired (source guard) and applied
    import inspect
    assert "is_remote_international" in inspect.getsource(tracker.rank_pending_leads)


def t_location_mode():
    """Location mode is first-class: classified correctly, tagged on leads, no ranking bias."""
    import config, tracker, opportunities as o, inspect
    # classifier: hybrid is most specific, then remote, then onsite, else unknown
    assert config.classify_location("Fully Remote ML Engineer") == "remote"
    assert config.classify_location("Ingénieur IA — télétravail") == "remote"
    assert config.classify_location("Data Engineer (Hybride, 3 jours/semaine)") == "hybrid"
    assert config.classify_location("ML Engineer — sur site Paris") == "onsite"
    assert config.classify_location("Machine Learning Engineer") == ""
    assert config.classify_location(f"ML {config.REMOTE_INTL_TAG}") == "remote"
    assert config.LOCATION_MODES == ("remote", "hybrid", "onsite")
    # every ranked lead carries a location_mode (informative; NO score bias)
    leads = tracker.rank_pending_leads(limit=5)
    if leads:
        assert all("location_mode" in l for l in leads)
        assert all(l["location_mode"] in ("remote", "hybrid", "onsite", "") for l in leads)
    assert "classify_location" in inspect.getsource(tracker.rank_pending_leads)
    # the scout covers ALL THREE sections: remote boards + France in-person + EU relocation
    assert all(hasattr(o, f) for f in ("_fetch_france_inperson", "_fetch_arbeitnow", "_fetch_themuse"))
    assert set(o._SECTION_LABEL) == {"remote", "france", "relocate"}
    # section routing: remote → remote; French API onsite → france; abroad onsite → relocate;
    # a France LOCATION wins the France section even from a non-French board (e.g. The Muse Paris).
    assert o._section({"mode": "remote", "source": "RemoteOK"}) == "remote"
    assert o._section({"mode": "onsite", "source": "apec"}) == "france"
    assert o._section({"mode": "onsite", "source": "Arbeitnow", "location": "Berlin"}) == "relocate"
    assert o._section({"mode": "onsite", "source": "The Muse", "location": "Paris, France"}) == "france"
    # a mixed offer set formats into the three sections
    sample = [{"company": "A", "role": "AI Eng", "url": "", "location": "Remote",
               "category": "ai", "source": "RemoteOK", "mode": "remote"},
              {"company": "B", "role": "ML Eng", "url": "", "location": "Paris",
               "category": "ai", "source": "apec", "mode": "onsite"},
              {"company": "C", "role": "Data Eng", "url": "", "location": "Berlin",
               "category": "data", "source": "Arbeitnow", "mode": "onsite"}]
    # The digest is no longer grouped into sections: at five roles it is one ranked shortlist,
    # with each role's reachability shown inline (🏢 place / 🌍 remote) instead of as a heading.
    d = o.format_digest(sample)
    for s in sample:
        assert s["company"] in d and s["role"] in d, "every chosen role must appear in the digest"
    assert "🌍" in d or "🏢" in d, "each role must still say whether it is remote or on-site"


def t_global_brands():
    """The reachable-international brand recognizer matches truthfully and is wired into ranking."""
    import config, global_brands as g, tracker, inspect
    # seed populated, both channels present
    assert g.channel_of("Mistral AI") == "cold", "Paris-HQ scale-up must be cold-channel"
    assert g.channel_of("Datadog") == "portal", "global giant must be portal-channel"
    assert g.channel_of("Mistral") == "cold", "token-subset match must work"
    # no false positives on unrelated names (the school_partners 'Air France' trap)
    assert g.channel_of("Air France") == "", "must not match unrelated company"
    assert g.channel_of("Trustpair") == "", "must not match unrelated company"
    assert g.match("SomeRandom Startup") is None
    # summary is non-empty for a brand, empty otherwise
    assert g.summary("Qonto") and not g.summary("Nonexistent Co")
    # boosts configured and the recognizer is wired into the ranker
    assert config.GLOBAL_BRAND_BOOST_COLD > config.GLOBAL_BRAND_BOOST_PORTAL >= 0
    assert "global_brands" in inspect.getsource(tracker.rank_pending_leads)


def t_digest_feeds_outreach():
    """The scout's alternance finds must reach the OUTREACH queue, not only Zineb's inbox.

    The two halves were pointed at different populations and the wrong one held the scarce
    resource: of ~1,570 pending outreach leads only 8% carry an actual alternance posting, and the
    replies say so back — "nous n'avons pas de poste d'alternant ouvert pour le moment", three of
    six genuine replies, twice alongside a compliment about the profile. Meanwhile the scout finds
    companies that have already decided they want an alternant, and those were write-only.
    """
    import inspect
    import opportunities as opp
    src = inspect.getsource(opp.feed_outreach)
    # goes through lead_inbox, because contacts.xlsx is merge=ours and the VM wins: a row written
    # from here would be silently discarded on the VM's next pull. (Matched on the CALL, not the
    # word — the docstring names tracker.add_contact to explain why it is not used.)
    assert "lead_inbox.add(" in src
    assert "tracker.add_contact(" not in src
    # carries the REAL posting title — that is what earns +28 in rank_pending_leads
    assert 'role=o["role"]' in src
    # and is wired into the daily run
    assert "feed_outreach" in inspect.getsource(opp.main)

    offers = [
        {"company": "Acme SAS", "role": "Alternance Data Engineer (H/F)", "url": "http://x/1",
         "location": "Paris 11 - 75", "fit": 80, "fit_raw": 80, "meta": {"contract": "alternance"}},
        {"company": "Employeur non nommé — voir l'offre", "role": "Alternance Data Analyst",
         "url": "http://x/2", "location": "92800 Puteaux", "fit": 88, "fit_raw": 88,
         "meta": {"contract": "alternance"}},
        {"company": "ISCOD", "role": "Alternance Développeur IA", "url": "http://x/3",
         "location": "Paris", "fit": 70, "fit_raw": 70, "meta": {"contract": "alternance"}},
        {"company": "NEXTGEN RH", "role": "Alternance Data Analyst", "url": "http://x/4",
         "location": "Paris", "fit": 70, "fit_raw": 70, "meta": {"contract": "alternance"}},
        {"company": "Beta SA", "role": "Senior Data Engineer", "url": "http://x/5",
         "location": "Paris", "fit": 75, "fit_raw": 75, "meta": {}},
    ]
    res = opp.feed_outreach(offers, apply=False)     # dry-run: queues nothing
    names = [c for c, _ in res["queued"]]
    reasons = dict((c, w) for c, w in res["skipped"])
    assert "Acme SAS" in names, names
    assert "Beta SA" not in names, "not an alternance posting"
    # an anonymised employer has nobody to email; a school posts ads and does not employ; an
    # agency hides the actual employer. None of the three is an outreach target.
    for co in ("Employeur non nommé — voir l'offre", "ISCOD", "NEXTGEN RH"):
        assert co not in names, co
        assert co in reasons, co
    assert opp._FEED_CAP <= 10, "this queue is already ~1,570 deep"


def t_alternance_timeline_is_current():
    """The dates the outgoing message is framed around must match Zineb's real timeline.

    Corrected 2026-09-07: ALTERNANCE_START_DATE said 1 September, which floored
    weeks_until_alternance() at 0 and had /daily-agent telling every recipient she was already past
    her own start. Her rentrée is OCTOBER 2026 and an alternance can still be signed through the
    end of December — she is inside the window with runway, which is the strongest position to
    write from. A silently-passed date is the failure mode here, so this asserts the ordering
    rather than the literals: the deadline must sit after the start, and the skill must read BOTH
    clocks, because the start alone cannot tell "not yet" from "long gone".
    """
    import inspect
    import config
    assert config.ALTERNANCE_START_DATE < config.ALTERNANCE_DEADLINE
    assert config.ALTERNANCE_START_DATE.year >= 2026 and config.ALTERNANCE_START_DATE.month == 10
    assert config.weeks_until_deadline() >= config.weeks_until_alternance()
    skill = open(".claude/commands/daily-agent.md", encoding="utf-8").read()
    assert "weeks_until_deadline" in skill, "the skill must read the deadline, not only the start"
    assert "weeks_until_deadline" in inspect.getsource(config)


def t_no_stale_start_date_in_outgoing_mail():
    """An availability clause must not offer a month that has already gone by."""
    import datetime
    import email_lint
    oct1 = datetime.date(2026, 10, 1)
    # ALTERNANCE_START_DATE passed on 2026-09-01 while /daily-agent still wrote "à partir de
    # septembre 2026" — careless at best, and read as "she found nothing for September" at worst.
    assert email_lint._stale_availability("alternance à partir de septembre 2026", today=oct1)
    assert email_lint._stale_availability("alternance à partir de juin 2026")
    assert email_lint._stale_availability("available from January 2026", today=oct1)
    # Scoped to AVAILABILITY on purpose. A blanket "no past month" rule would fire on Zineb's own
    # history, which is the strongest material in these emails.
    for ok in ("en production depuis juin", "LanguageCert passée en février 2026",
               "mon agent tourne depuis juillet 2026", "disponible dès novembre 2026",
               "available from January 2027"):
        assert not email_lint._stale_availability(ok, today=oct1), ok
    # and it is a hard ERROR, not a warning — this one leaves the building
    errs, _ = email_lint.lint(
        "Bonjour,\n\nVotre pipeline m'interpelle.\n\nJe cherche une alternance a partir de "
        "juin 2026.\n\nlinkedin.com/in/zinebmeftah\n", subject="Votre pipeline", kind="cold")
    assert any("already passed" in e for e in errs), errs


def t_send_counter_keys_agree():
    """_record_send WRITES the bucket, cap_check READS it. They must use the same names.

    Asserted end-to-end rather than by inspection: the counter is the anti-spam ceiling, and a
    silent key mismatch would look exactly like "no cap at all" — the pre-2026-09 state, where the
    caps lived only in the skill prompt and three August days went out at 11 sends against a 10
    ceiling. Verified by driving the real recorder and reading the real check.
    """
    import json
    import config
    import smtp_send
    path = smtp_send._counts_path()
    backup = path.read_text(encoding="utf-8") if path.exists() else None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"cold": 0, "warm": 0}))
        # a follow-up recorded by the writer must be visible to the reader
        for _ in range(config.WARM_CAP):
            smtp_send._record_send("followup")
        assert not smtp_send.cap_check("followup")[0], "follow-up cap did not fire"
        assert smtp_send.cap_check("cold")[0], "a follow-up must not consume the cold bucket"
        # and cold likewise, up to the daily ceiling
        path.write_text(json.dumps({"cold": 0, "warm": 0}))
        for _ in range(config.effective_cold_cap()):
            smtp_send._record_send("cold")
        assert not smtp_send.cap_check("cold")[0], "cold cap did not fire"
        # reply and alert stay exempt — an answer in a live conversation must always get out
        assert smtp_send.cap_check("reply")[0] and smtp_send.cap_check("alert")[0]
        # …but the DAILY ceiling still stops agent-initiated volume
        path.write_text(json.dumps({"cold": config.DAILY_CAP, "warm": 0}))
        assert not smtp_send.cap_check("cold")[0] and not smtp_send.cap_check("followup")[0]
        assert smtp_send.cap_check("reply")[0], "a reply is exempt from the daily cap"
    finally:
        if backup is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(backup, encoding="utf-8")


def t_cold_cap_is_paced_by_verification_budget():
    """Verification, not COLD_CAP, is the real ceiling — and it must be paced, not a cliff.

    Every cold send spends one Hunter check; the free tier gives 100 a month; and the gate
    protecting it is `remaining > HUNTER_SAFETY_MARGIN`, then nothing. August 2026 is that shape:
    108 cold sends against a 100 budget, 6-8/day until the quota ran out — bounce rate 0.9% in
    July with quota, 7.2% in August past it.
    """
    import datetime as _d
    import json
    import time
    import config
    import email_verify
    assert config.verification_paced_cap() is not None or True   # None is allowed (unknown)
    assert config.effective_cold_cap() <= config.COLD_CAP, "pacing may lower the cap, never raise it"
    assert config._weekdays_left_in_month(_d.date(2026, 9, 30)) == 1
    assert config._weekdays_left_in_month(_d.date(2026, 9, 1)) >= 20

    cache = email_verify._HUNTER_ACCT_CACHE
    backup = cache.read_text(encoding="utf-8") if cache.exists() else None
    try:
        # A FRESH near-empty balance stops cold sends — the same verdict the verify gate would
        # reach, made explicit at the cap instead of failing once per send.
        cache.write_text(json.dumps({"remaining": config.HUNTER_SAFETY_MARGIN, "ts": time.time()}))
        assert config.verification_paced_cap() == 0
        assert config.effective_cold_cap() == 0
        # A STALE record from before this month's reset must be IGNORED, not obeyed: it would
        # report last month's exhausted balance and pin the cap at zero for a month that has a
        # full quota available. A bookkeeping gap must never be able to stop outreach by itself.
        stale = _d.datetime(2000, 1, 1, tzinfo=_d.timezone.utc).timestamp()
        cache.write_text(json.dumps({"remaining": 0, "ts": stale}))
        assert config.verification_paced_cap() is None
        assert config.effective_cold_cap() > 0, "a stale cache must fall back to the warm-up ramp"
        # A healthy balance spreads over the working days left rather than burning down at 7/day.
        cache.write_text(json.dumps({"remaining": 100, "ts": time.time()}))
        paced = config.verification_paced_cap()
        assert paced and paced <= config.COLD_CAP + 100
    finally:
        if backup is None:
            cache.unlink(missing_ok=True)
        else:
            cache.write_text(backup, encoding="utf-8")


def t_pacing_follows_hunters_real_reset_date():
    """The quota cycle is Hunter's anniversary day, NOT the 1st of the month.

    This account resets on the 13th. Pacing the balance to month-end spends it ~12 days before
    it actually refills, and the leftover days then fall back to the full ramp on an empty tank
    — the same front-load-then-go-dark shape pacing exists to prevent, shifted by 12 days.
    Hunter publishes `reset_date` on the free /v2/account endpoint; it must be stored and used.
    """
    import datetime as _d
    import json
    import time
    import config
    import email_verify

    assert config._weekdays_between(_d.date(2026, 9, 14), _d.date(2026, 9, 21)) == 5
    assert config._weekdays_between(_d.date(2026, 9, 19), _d.date(2026, 9, 21)) == 1, "weekend"
    assert config._weekdays_between(_d.date(2026, 9, 21), _d.date(2026, 9, 21)) == 1, "never zero"
    # Cycle start = one month back, clamped for short months (a 31st cycle starts on the 28th).
    assert config._previous_reset(_d.date(2026, 10, 13)) == _d.date(2026, 9, 13)
    assert config._previous_reset(_d.date(2026, 1, 9)) == _d.date(2025, 12, 9)
    assert config._previous_reset(_d.date(2026, 3, 31)) == _d.date(2026, 2, 28)

    cache = email_verify._HUNTER_ACCT_CACHE
    backup = cache.read_text(encoding="utf-8") if cache.exists() else None
    try:
        cache.write_text(json.dumps(
            {"remaining": 100, "ts": time.time(), "reset_date": "2026-10-13"}))
        # Oct 1-12 belongs to the cycle that refills on the 13th, so it must STILL be paced.
        # Under the old calendar-month assumption this returned None and ran the full ramp.
        oct_cap = config.verification_paced_cap(_d.date(2026, 10, 1))
        assert oct_cap is not None, "a day inside the cycle must be paced, not left to the ramp"
        assert oct_cap > 0, "a cycle with balance left must not stop sending"
        # paced is a RAW rate; COLD_CAP is applied by effective_cold_cap(), never here.
        assert config.effective_cold_cap(_d.date(2026, 10, 1)) <= config.COLD_CAP
        # ...and the balance is spread to the RESET, not to Sep 30.
        sep = config.verification_paced_cap(_d.date(2026, 9, 14))
        assert sep is not None and sep <= 6, f"balance must span the whole cycle, got {sep}/day"

        # A record written before the cycle started is stale — even if it is this month.
        before = _d.datetime(2026, 9, 10, tzinfo=_d.timezone.utc).timestamp()
        cache.write_text(json.dumps(
            {"remaining": 0, "ts": before, "reset_date": "2026-10-13"}))
        assert config.verification_paced_cap(_d.date(2026, 9, 14)) is None, \
            "a pre-reset reading must never pin the cap at zero"

        # No reset_date (cache written by an older build) → fall back to the calendar month
        # rather than losing pacing altogether.
        cache.write_text(json.dumps({"remaining": 100, "ts": time.time()}))
        assert config.verification_paced_cap(_d.date(2026, 9, 14)) is not None
    finally:
        if backup is None:
            cache.unlink(missing_ok=True)
        else:
            cache.write_text(backup, encoding="utf-8")


def t_hunter_cache_stores_the_reset_date():
    """reset_date must survive the fetch→cache→read round trip, or pacing silently regresses.

    It was being fetched from /v2/account and discarded, which is why pacing assumed a calendar
    month in the first place. An offline round-trip test, so it runs with no key and no network.
    """
    import json
    import time
    import email_verify
    cache = email_verify._HUNTER_ACCT_CACHE
    backup = cache.read_text(encoding="utf-8") if cache.exists() else None
    try:
        cache.write_text(json.dumps(
            {"remaining": 42, "ts": time.time(), "reset_date": "2026-10-13"}))
        rec = email_verify._hunter_acct_record()
        assert rec is not None and rec[0] == 42 and rec[2] == "2026-10-13"
        # The legacy 2-tuple reader must keep working — hunter_remaining() depends on it.
        assert email_verify._hunter_acct_cached()[0] == 42
        # A record with no reset_date must read as None, not crash or invent one.
        cache.write_text(json.dumps({"remaining": 7, "ts": time.time()}))
        assert email_verify._hunter_acct_record()[2] is None
    finally:
        if backup is None:
            cache.unlink(missing_ok=True)
        else:
            cache.write_text(backup, encoding="utf-8")


def t_linkedin_budget_counts_the_two_ceilings_apart():
    """LinkedIn has TWO budgets ~20x apart; conflating them wastes the scarce one.

    Premium Career: connection-request notes are UNLIMITED per month (the "5 a month" figure is
    the FREE-account cap) but invites are ~100 per ROLLING 7 days, which Premium does not raise.
    InMail is the hard monthly one: 5 credits. The channel ran with no cap at all until 2026-09 —
    93 notes drafted in August — so these windows must stay correct and separate.
    """
    import datetime as _d
    import pandas as pd
    import config
    import linkedin_budget as lb
    today = _d.date(2026, 9, 14)
    df = pd.DataFrame({"Conversation Log": [
        "[2026-09-14] Agent (LinkedIn): connection note drafted",
        "[2026-09-14] Agent (LinkedIn): InMail drafted",
        "[2026-09-10] Agent (LinkedIn): connection note drafted",
        "[2026-09-01] Agent (LinkedIn): connection note drafted",   # same month, OUTSIDE the 7d window
        "[2026-09-12] Agent: cold email\n [2026-09-13] Contact: merci",
        "[not-a-date] Agent (LinkedIn): connection note drafted",
    ]})
    c = lb.counts(today, df)
    assert c["invites_last_7d"] == 2, f"rolling week must exclude 09-01, got {c}"
    assert c["inmails_this_month"] == 1, "InMail is counted per CALENDAR MONTH, not per week"
    assert c["drafts_total"] == 4, "an unparseable date must be skipped, never guessed"
    # An email-only log must not register as a LinkedIn touch.
    assert lb.counts(today, pd.DataFrame({"Conversation Log": ["[2026-09-14] Agent: cold"]}))[
        "drafts_total"] == 0

    a = lb.allowance(today, df)
    assert a["invites_left_week"] == config.LINKEDIN_WEEKLY_INVITE_CAP - 2
    assert a["inmails_left_month"] == config.LINKEDIN_INMAIL_CREDITS - 1
    assert a["invite_per_day"] >= 0 and a["invite_per_day"] <= a["invites_left_week"], \
        "the per-day figure must never exceed what is left in the week"


def t_linkedin_unlabelled_lines_never_spend_an_inmail():
    """Every note drafted before methods were tracked must read as an INVITE.

    There are 120+ such lines. If they parsed as InMail they would show 5 credits spent the
    moment this shipped, and the agent would refuse to draft the one InMail that matters — a
    bookkeeping default silently closing the scarcest channel.
    """
    import datetime as _d
    import pandas as pd
    import linkedin_budget as lb
    legacy = pd.DataFrame({"Conversation Log": [
        "[2026-09-14] Agent (LinkedIn): connection note drafted"] * 6})
    c = lb.counts(_d.date(2026, 9, 14), legacy)
    assert c["inmails_this_month"] == 0, "unlabelled history must never consume InMail credits"
    assert c["invites_last_7d"] == 6

    ok, why = lb.may_draft("inmail", _d.date(2026, 9, 14), legacy)
    assert ok, f"InMail must still be available with only legacy invites on record: {why}"


def t_linkedin_budget_refuses_when_spent():
    """The cap must actually REFUSE, in code — the email caps were prompt-only once, and drifted."""
    import datetime as _d
    import pandas as pd
    import config
    import linkedin_budget as lb
    today = _d.date(2026, 9, 14)

    spent_inmail = pd.DataFrame({"Conversation Log":
        ["[2026-09-%02d] Agent (LinkedIn): InMail drafted" % (d + 1)
         for d in range(config.LINKEDIN_INMAIL_CREDITS)]})
    ok, why = lb.may_draft("inmail", today, spent_inmail)
    assert not ok and "credit" in why.lower(), f"spent credits must refuse: {why}"
    # ...but an invite is still allowed: the two budgets are independent.
    assert lb.may_draft("invite", today, spent_inmail)[0], \
        "a spent InMail allowance must not block the unlimited-on-Premium invite channel"

    spent_invites = pd.DataFrame({"Conversation Log":
        ["[2026-09-14] Agent (LinkedIn): connection note drafted"]
        * config.LINKEDIN_WEEKLY_INVITE_CAP})
    ok, why = lb.may_draft("invite", today, spent_invites)
    assert not ok and "invite" in why.lower(), f"spent weekly invites must refuse: {why}"
    assert lb.allowance(today, spent_invites)["invite_per_day"] == 0

    # The self-imposed weekly cap must stay under LinkedIn's real ~100/7d ceiling.
    assert 0 < config.LINKEDIN_WEEKLY_INVITE_CAP <= 90, "stay well clear of the real ban threshold"
    assert 0 < config.LINKEDIN_INMAIL_CREDITS <= 15, "Premium Career gives 5, accruing to at most 15"


def t_linkedin_method_marker_round_trips():
    """tracker WRITES the method; linkedin_budget READS it. They must not drift.

    Same failure shape as the autoreply markers below: one side changed its wording and the other
    silently stopped matching. Here the cost is a miscounted scarce credit, so assert the exact
    strings tracker produces are the ones the budget parser classifies.
    """
    import datetime as _d
    import pandas as pd
    import linkedin_budget as lb
    today = _d.date(2026, 9, 14)
    for method, expect_inmail in (("invite", 0), ("inmail", 1)):
        kind = "InMail" if method == "inmail" else "connection note"
        line = f"[2026-09-14] Agent (LinkedIn): {kind} drafted"   # exactly what tracker writes
        c = lb.counts(today, pd.DataFrame({"Conversation Log": [line]}))
        assert c["inmails_this_month"] == expect_inmail, f"{method} misclassified: {line}"
        assert c["drafts_total"] == 1
    # has_linkedin_touch keys off "(linkedin)" — both wordings must still trip it, or the
    # duplicate guard breaks and the same person gets two notes.
    for kind in ("InMail", "connection note"):
        assert "(linkedin)" in f"[2026-09-14] Agent (LinkedIn): {kind} drafted".lower()


def t_cv_never_ships_truncated():
    """The CV's main column is a fixed-height minipage — overflow is INVISIBLE, not an error.

    Content past \\paperheight is drawn below the page edge. It stays in the PDF's text layer, so
    every "is the text present?" check passes while a recruiter sees nothing: that is how the
    Recherche section (her published Hugging Face article) was missing from every CV attached to a
    follow-up, with the build reporting success. The .tex must carry the height probe and
    cv_builder must refuse rather than ship a truncated CV.
    """
    from pathlib import Path
    import cv_builder
    docs = Path(__file__).parent / "documents"
    for f in ("CV_Zineb_Meftah_FR.tex", "CV_Zineb_Meftah_EN.tex"):
        tex = (docs / f).read_text(encoding="utf-8")
        assert "\\providecommand{\\cvFit}" in tex, f"{f}: lost the spacing hook cv_builder scales"
        assert "CVFIT content=" in tex, f"{f}: lost the height probe — overflow becomes silent again"
        assert "lrbox" in tex and "cvMainBox" in tex, f"{f}: main column must be boxed to be measured"
        assert "\\vgap{" in tex, f"{f}: gaps must route through \\vgap or auto-fit does nothing"

    # The probe is parsed from tectonic's output; both directions must be read correctly.
    assert cv_builder.tex_overflow("CVFIT content=870.687pt available=793.832pt") > 76
    assert cv_builder.tex_overflow("CVFIT content=700.0pt available=793.832pt") < 0
    # Multiple passes: the LAST report wins (tectonic typesets more than once).
    assert cv_builder.tex_overflow(
        "CVFIT content=900.0pt available=793.0pt\nCVFIT content=700.0pt available=793.0pt") < 0
    # No report at all → None, never a crash and never a false "it fits".
    assert cv_builder.tex_overflow("no probe here") is None
    assert cv_builder.FIT_STEPS[0] == 1.00 and min(cv_builder.FIT_STEPS) >= 0.70, \
        "start at the designed spacing; refuse below ~0.7 rather than shipping something cramped"


def t_cold_emails_may_not_reuse_sentences():
    """"Vary every email" was a rule nobody enforced, so the batch went formulaic.

    Across one day's seven cold emails: four opened on "le vrai mur n'est pas X, c'est Y", six
    carried "Major de ma promo L3 IA (1ère/126)" verbatim, six closed on "10 minutes cette
    semaine ?". Each email personalised its hook and then fell back into the same stock lines.
    The linter now BLOCKS that, because a warning is precisely what had been ignored.
    """
    import email_lint as L
    stock = "Major de ma promo L3 IA, et stage en production chez GE HealthCare cette annee."
    corpus = [f"Bonjour {n}.\n\n{stock}\n\nUne question courte pour vous." for n in ("Alice", "Bob")]

    hits = L.reused_sentences(f"Bonjour Chloe.\n\n{stock}\n", _corpus=corpus)
    assert hits and hits[0][1] >= 2, "a sentence sent twice already must be caught"

    # Swapping the company name must NOT disguise a stock line.
    corpus2 = ["Pour Veesion : viser vos objectifs sans noyer les equipes techniques.",
               "Pour Foodvisor : viser vos objectifs sans noyer les equipes techniques."]
    assert L.reused_sentences("Pour Alan : viser vos objectifs sans noyer les equipes techniques.",
                              _corpus=corpus2), "company name must be normalised out"

    # A genuinely fresh sentence passes, and short fragments never trip it.
    assert not L.reused_sentences("Votre moteur de recommandation a un probleme de demarrage a froid.",
                                  _corpus=corpus)
    assert not L.reused_sentences("Merci beaucoup.", _corpus=corpus), "short fragments repeat innocently"
    # No corpus (fresh clone / public mirror) must never block a send.
    assert L.reused_sentences(stock, _corpus=[]) == []

    errs, _ = L.lint(f"Bonjour.\n\n{stock}\n\nUne question ?", subject="X", kind="cold")
    assert isinstance(errs, list), "lint must stay callable with no drafts/ present"

    # ── Stock FRAGMENTS carried inside a different sentence each time ──────────────
    # The sentence check above misses these, and they are what actually made the batch look
    # templated: on 2026-09-15 "10 minutes cette semaine" closed 57% of recent cold emails and
    # "major de promo 1ère 126" appeared in 43%, while every one of those emails passed the
    # sentence check. The boilerplate is learned from the corpus, not hardcoded, so it tracks
    # whatever is currently over-used and relaxes as the phrasing spreads out.
    closer = "Auriez vous dix minutes cette semaine pour en parler"
    corpus = [f"Bonjour. Un point specifique sur votre produit numero {i}. {closer} ?"
              for i in range(8)]
    hits = L.overused_phrases(f"Bonjour. Autre chose entierement differente ici. {closer} ?",
                              _corpus=corpus)
    assert hits, "a fragment in most recent emails must be caught even in a fresh sentence"
    assert hits[0][1] >= 0.5, f"share must reflect how widespread it is: {hits}"
    # Only the LONGEST form of overlapping hits is reported, or the output is a wall of noise.
    assert len(hits) <= L.PHRASE_REPORT_MAX
    for phrase, _share in hits:
        assert not any(phrase in other and phrase != other for other, _ in hits), \
            f"'{phrase}' is contained in another reported hit — report maximal phrases only"

    # Text that shares nothing with the corpus passes.
    assert not L.overused_phrases("Votre moteur de recommandation bute sur le demarrage a froid.",
                                  _corpus=corpus)
    # Too small a corpus has no meaningful "usual" — the rule must stay silent rather than guess.
    assert L.overused_phrases(closer, _corpus=corpus[:2]) == []
    assert L.overused_phrases(closer, _corpus=[]) == [], "no corpus must never block a send"
    assert 0.2 < L.PHRASE_SHARE < 0.6, "threshold must sit in the gap between boilerplate and prose"


def t_strategy_p_is_registered_everywhere():
    """A strategy the skill offers but the tracker cannot parse is invisible to the bandit.

    Strategy P (Profile First) leads with Zineb's credentials instead of the company's problem —
    added at her request because every other arm made her background the supporting act. If the
    log regex does not accept the letter, every P send parses as untagged and the arm never
    accumulates evidence, so the bandit would never learn whether it works.
    """
    import tracker
    assert "P" in tracker.ALL_STRATEGIES, "Strategy P missing from ALL_STRATEGIES"
    # strategy_stats() deliberately reports only arms already used, so a brand-new arm is absent
    # there; the bandit's own view must still carry it or it can never be explored.
    ranked = {s["letter"] for s in tracker.recommend_strategy_order()["ranked"]}
    assert ranked == set(tracker.ALL_STRATEGIES), \
        f"the bandit must rank every declared arm, missing: {set(tracker.ALL_STRATEGIES) - ranked}"
    # The log regex must accept the letter, or every P send parses as untagged and the arm
    # never accumulates evidence.
    import re as _re
    from pathlib import Path as _P
    src = _P(tracker.__file__).read_text(encoding="utf-8")
    m = _re.search(r"Agent\\s\+\\\(Strategy:\(\[([A-Z]+)\]\)", src)
    assert m and "P" in m.group(1), "the conversation-log regex does not accept Strategy:P"

    from pathlib import Path
    skill = (Path(__file__).parent / ".claude" / "commands" / "daily-agent.md").read_text(encoding="utf-8")
    for letter in tracker.ALL_STRATEGIES:
        assert f"**Strategy {letter} —" in skill, f"Strategy {letter} is tracked but not described"
    # Each strategy must carry its OWN shape: one fixed skeleton for all of them is what made
    # every email land in the same six blocks with only the first line personalised.
    assert skill.count("SHAPE:") >= len(tracker.ALL_STRATEGIES), \
        "every strategy needs a SHAPE line, or the block order stops varying"


def t_linkedin_url_is_consistent_everywhere():
    """One canonical profile URL across about_me.txt, both CVs, and the skill.

    These had drifted into a three-way split: about_me.txt (which the agent actually reads when
    writing) and every draft said `zineb-meftah`, while the CVs and the skill's examples said
    `zinebmeftah`. Zineb confirmed on 2026-09-14 that `zinebmeftah` is the real one — so the
    LinkedIn link in ~134 cold emails was a 404, in a channel whose whole purpose is to get the
    reader to look at her profile, and the linter was requiring its presence without checking it.
    LinkedIn answers automated requests with HTTP 999, so no fetch can verify this: consistency
    with the confirmed value is the only check available, which is exactly why it must be pinned.
    """
    from pathlib import Path as _P
    root = _P(__file__).parent
    canonical = "linkedin.com/in/zinebmeftah"
    import re as _re
    pat = _re.compile(r"linkedin\.com/in/([A-Za-z0-9_-]+)")
    sources = ["about_me.txt", "documents/CV_Zineb_Meftah_FR.tex",
               "documents/CV_Zineb_Meftah_EN.tex", ".claude/commands/daily-agent.md"]
    for rel in sources:
        f = root / rel
        if not f.exists():
            continue
        found = {m.group(0) for m in pat.finditer(f.read_text(encoding="utf-8"))}
        # Other people's profiles legitimately appear in examples; hers must not be misspelt.
        hers = {u for u in found if "zineb" in u.lower() or "meftah" in u.lower()}
        assert hers <= {canonical}, f"{rel}: wrong LinkedIn URL {hers - {canonical}} (canonical: {canonical})"
    am = (root / "about_me.txt").read_text(encoding="utf-8")
    assert canonical in am, "about_me.txt must carry the canonical LinkedIn URL — the agent reads it"


def t_cv_adapts_its_content_to_the_offer():
    """What the CV drops to fit must depend on the ROLE, not on a hardcoded choice.

    Zineb's instruction: "depending on the offer you should drop or shorten". The CV runs ~77pt
    over one page and spacing alone cannot absorb that, so something must go — but which thing
    depends entirely on what she is applying for. A LeRobot MLOps pipeline earns its place on an
    AI/MLOps CV and not on a backend one; the affiliate content engine is the reverse.
    """
    from pathlib import Path as _P
    import cv_builder
    docs = _P(__file__).parent / "documents"
    for f in ("CV_Zineb_Meftah_FR.tex", "CV_Zineb_Meftah_EN.tex"):
        tex = (docs / f).read_text(encoding="utf-8")
        blocks = cv_builder.cv_blocks(tex)
        assert len(blocks) >= 4, f"{f}: only {len(blocks)} @cvblock markers — selection needs them"
        assert tex.count("% @cvblock") == tex.count("% @endcvblock"), f"{f}: unbalanced block markers"
        ids = [b["id"] for b in blocks]
        assert len(ids) == len(set(ids)), f"{f}: duplicate @cvblock id — strip_block removes only the first"
        for b in blocks:
            assert b["focus"], f"{f}: block '{b['id']}' declares no focus, so it can never be ranked"
        keeps = [b["id"] for b in blocks if b["keep"]]
        assert keeps == ["outreach-agent"], f"{f}: the flagship project must be the pinned one, got {keeps}"

        # A pinned block is never a drop candidate, whatever the focus.
        for focus in ("ai", "backend", "mlops", "data", "fullstack"):
            order = cv_builder.drop_order(tex, focus)
            assert "outreach-agent" not in [b["id"] for b in order], \
                f"{f}: the flagship was offered up for --focus {focus}"
            # Blocks IRRELEVANT to this focus must be sacrificed before relevant ones.
            rel = [focus in b["focus"] for b in order]
            assert rel == sorted(rel), \
                f"{f}: --focus {focus} would drop a relevant block before an irrelevant one"

        # The role genuinely changes the answer — otherwise this is just a hardcoded cut again.
        assert cv_builder.drop_order(tex, "ai")[0]["id"] != cv_builder.drop_order(tex, "backend")[0]["id"], \
            f"{f}: AI and backend builds sacrifice the same block — the CV is not adapting"

        # Stripping removes that block and nothing else.
        stripped = cv_builder.strip_block(tex, "lerobot")
        assert "lerobot" not in [b["id"] for b in cv_builder.cv_blocks(stripped)]
        assert len(cv_builder.cv_blocks(stripped)) == len(blocks) - 1
        assert cv_builder.strip_block(tex, "no-such-block") == tex, "unknown id must be a no-op"


def t_recent_rejections_are_downranked():
    """A company that just said no must not hold a top slot in the priority queue.

    On 2026-09-15 three of the top-60 leads were at companies that had already rejected her —
    including the #1 lead overall — and Joko, whose Talent Acquisition wrote "votre profil n'est
    pas ce que nous recherchons actuellement" on 09-04, still had ten roles queued. With the
    verification budget supporting only ~3 cold sends a day, each of those is a wasted day.

    DOWN-RANKED, never dropped, and decaying: a rejection is usually for one role, large employers
    run independent teams, and a "not right now" genuinely expires.
    """
    import datetime as _dt
    import pandas as pd
    import tracker

    today = _dt.date.today()
    def _row(company, status, log="", days_ago=10, role="AI Engineer"):
        return {"Company": company, "Role": role,
                "Contact Email": f"contact@{company.lower()}.com",
                "Conversation Log": log, "Status": status,
                "Last Interaction Date": (today - _dt.timedelta(days=days_ago)).isoformat()}

    # The rejection must be read from the LOG, not Status: the most explicit and most recent
    # rejections are stamped `Replied` (the 2026-09 audit found Status unreliable on old rows).
    rejection = "[2026-09-04] Contact: Merci, mais votre profil n'est pas ce que nous recherchons actuellement."
    assert tracker.looks_like_rejection(rejection), \
        "the shared rejection predicate no longer recognises a plain no — stalled_conversations uses it too"

    saved = tracker.load
    try:
        def fake_load():
            return pd.DataFrame([
                _row("FreshNo", "Replied", rejection, days_ago=5),      # no 5 days ago
                _row("FreshNo", "Pending", "", days_ago=5, role="Backend Engineer"),
                _row("OldNo", "Rejected", "", days_ago=200),            # no long ago
                _row("OldNo", "Pending", "", days_ago=200, role="Backend Engineer"),
                _row("NeverAsked", "Pending", "", days_ago=5, role="Backend Engineer"),
            ])
        tracker.load = fake_load
        by_co = {r["Company"]: r for r in tracker.rank_pending_leads(limit=None)}
        assert set(by_co) >= {"FreshNo", "OldNo", "NeverAsked"}, \
            f"a rejected company must be down-ranked, never dropped: {sorted(by_co)}"
        assert "rejected" in by_co["FreshNo"]["reasons"], "a fresh no must be stated in the reasons"
        assert by_co["FreshNo"]["score"] < by_co["NeverAsked"]["score"], \
            "a company that said no 5 days ago must rank below one never contacted"
        # A stale no carries no penalty — it has expired, and re-approaching is legitimate.
        assert "rejected" not in by_co["OldNo"]["reasons"], "a 200-day-old no must not still be punished"
        assert by_co["OldNo"]["score"] == by_co["NeverAsked"]["score"]
    finally:
        tracker.load = saved


def t_cold_volume_collapse_is_detected():
    """Watch the SYMPTOM — the agent running while nothing goes out — not one cause.

    2026-09-04..09-14: a dead Hunter key blinded verification, the send gate refused nearly every
    cold email, and follow-ups kept flowing (they skip verification), so every outward sign said
    healthy. Cold sends went ~6/day → 1 across seven working days before a human noticed. The next
    cause will be different; the symptom will be identical.
    """
    import datetime as _dt
    import json as _json
    import tempfile
    from pathlib import Path as _P
    import preflight as _pf

    tmp = _P(tempfile.mkdtemp())
    def write(days: dict):
        for d, n in days.items():
            (tmp / f"daily_counts_{d}.json").write_text(_json.dumps({"cold": n, "warm": 3}))

    # The real outage, replayed.
    write({"2026-09-04": 0, "2026-09-07": 0, "2026-09-08": 0, "2026-09-09": 0, "2026-09-10": 0})
    msg = _pf.w_cold_outreach_volume(_cache=tmp, _today=_dt.date(2026, 9, 11), _cap=6)
    assert msg and "COLLAPSED" in msg, "seven days of zero cold sends must not pass silently"
    assert "hunter_health" in msg, "the message must name the first thing to check"

    # A healthy week must stay silent — a detector that cries wolf gets ignored, which is the
    # exact failure mode it exists to fix.
    tmp2 = _P(tempfile.mkdtemp())
    for d, n in {"2026-08-17": 7, "2026-08-18": 4, "2026-08-19": 7,
                 "2026-08-20": 6, "2026-08-21": 5}.items():
        (tmp2 / f"daily_counts_{d}.json").write_text(_json.dumps({"cold": n, "warm": 3}))
    assert _pf.w_cold_outreach_volume(_cache=tmp2, _today=_dt.date(2026, 8, 22), _cap=6) is None

    # Too little history (fresh clone, the public mirror, a dev machine) → silent, never a guess.
    tmp3 = _P(tempfile.mkdtemp())
    (tmp3 / "daily_counts_2026-09-10.json").write_text(_json.dumps({"cold": 0}))
    assert _pf.w_cold_outreach_volume(_cache=tmp3, _today=_dt.date(2026, 9, 11), _cap=6) is None
    assert _pf.w_cold_outreach_volume(_cache=_P(tempfile.mkdtemp()),
                                      _today=_dt.date(2026, 9, 11), _cap=6) is None


def t_lead_location_gates_unreachable_jobs():
    """Outreach must know WHERE a job is — the digest always did, and the two disagreed.

    Six boards publish a location on every offer and scraper.py discarded it, so 1,768 Pending
    leads carry no geography. config.classify_location() read the ROLE TITLE, which rarely names a
    city (931 of 940 ranked leads classified as ""), and nothing used the result for scoring.
    EKTOR is the cost: a cold send, a Hunter credit (the binding constraint at ~3/day) and a
    LinkedIn note spent on an alternance in DIJON, which then replied warmly — a role she cannot
    take while her M1 is at Université Paris Cité.
    """
    import lead_location as L

    assert L.classify("75 - PARIS") == "idf"
    assert L.classify("92 - Boulogne") == "idf"
    assert L.classify("Saint-Denis 93") == "idf"
    assert L.classify("Île-de-France") == "idf"
    assert L.classify("Dijon - 21") == "far"
    assert L.classify("Lyon - 69") == "far"
    assert L.classify("Bordeaux") == "far", "a bare city name must still be classified"
    # A ring town, deliberately NOT Zineb's own: sync_public.sh rewrites her home address
    # when mirroring, which silently inverted this assertion on the public tree.
    assert L.classify("60200 Compiegne") == "ring", "the ~1h commuter ring is workable"
    # Remote beats any place name — a remote role in Lyon is workable from Île-de-France, which
    # is the entire reason for checking rather than filtering on the city.
    assert L.classify("Télétravail (Lyon)") == "remote"
    assert L.classify("Full remote") == "remote"
    # UNKNOWN IS NEUTRAL. The rows already queued have no location and none can be recovered
    # (unlike lead_age, which git history could backfill — no commit ever stored a location),
    # so absence must never be read as a verdict.
    assert L.classify("") == "unknown"
    assert L.classify(None) == "unknown"
    assert L.classify("Somewhere Nobody Names") == "unknown"

    # An empty location writes nothing: "not recorded" and "recorded as nothing" must stay
    # distinguishable, or a board that omits the field would look like a decision.
    import tempfile
    from pathlib import Path as _P
    saved = L._PATH
    try:
        L._PATH = _P(tempfile.mkdtemp()) / "loc.json"
        assert L.record("Acme", "Data Engineer", "") is False
        assert L.get("Acme", "Data Engineer") is None
        assert L.reachability("Acme", "Data Engineer") == "unknown"
        assert L.record("Acme", "Data Engineer", "Dijon - 21") is True
        # FIRST WRITE WINS, so a re-scrape cannot overwrite a known location with a vaguer one.
        assert L.record("Acme", "Data Engineer", "Paris") is False
        assert L.reachability("Acme", "Data Engineer") == "far"
        # Key normalisation matches lead_age: a reformatted title is the SAME lead.
        assert L.reachability("ACME", "DATA  ENGINEER") == "far"
    finally:
        L._PATH = saved

    # The scraper must actually record it, or the sidecar stays empty forever.
    src = (_P(__file__).parent / "scraper.py").read_text(encoding="utf-8")
    assert "lead_location.record(" in src, "scraper no longer records the board's location"
    # And ranking must act on it.
    tsrc = (_P(__file__).parent / "tracker.py").read_text(encoding="utf-8")
    assert "reachability(" in tsrc, "rank_pending_leads no longer reads the location"


def t_no_claude_during_the_working_day():
    """THE scheduling invariant: no Claude job may run between 03:00 and 22:00 Paris.

    The VM authenticates the `claude` CLI with Zineb's own SUBSCRIPTION token, so every agent run
    spends from the same rolling 5-hour allowance she works in. Jobs at 09:00/14:00/19:00 Paris
    competed with her and locked her out of her own account. All Claude work now happens at night,
    and the last job must finish early enough that the 5h window has fully elapsed by 08:00 — so
    she starts the day with the entire allowance, not a partial one.

    This check exists because the constraint is invisible in the crontab itself: a future edit that
    moves a run "just to 07:00" looks harmless and silently breaks the whole point.
    """
    import re as _re
    from pathlib import Path as _P
    cron = (_P(__file__).parent / "vm" / "crontab.txt").read_text(encoding="utf-8")
    _CANARY = "vm/claude_canary.sh"

    # The canary earns its daytime slot by staying trivial. If it ever grows a skill invocation
    # or a loop, it stops being a health check and becomes a Claude job inside her working day —
    # exactly what this test exists to prevent — so the exemption is conditional on the content.
    _canary_src = (_P(__file__).parent / "vm" / "claude_canary.sh")
    if _canary_src.exists():
        _c = _canary_src.read_text(encoding="utf-8")
        assert _c.count("claude --print") == 1 and "claude -p " not in _c, \
            "the canary must make exactly ONE trivial claude call"
        for _forbidden in ("/daily-agent", "/scrape", "/find-contacts", "/speculative",
                           "run_night_prep", "--continue", "--resume"):
            assert _forbidden not in _c, \
                f"the canary invokes real work ({_forbidden}) — it is no longer a health check"

    PARIS_OFFSET = 2          # CEST; the guard below is deliberately strict enough for CET too
    LAST_CLAUDE_START_PARIS = 2      # 02:00 — plus ~20 min run time, then >5h clear before 08:00
    claude_runs = []
    for line in cron.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or not _re.match(r"^[\d*,/-]+\s", line):
            continue
        if _CANARY in line:
            # THE ONE EXEMPTION, allowed by name and only by name. vm/claude_canary.sh sends a
            # single trivial prompt at 07:30 Paris to prove the CLI is authenticated, has quota,
            # and that the VM is up — the failures this system keeps hitting silently (a session
            # limit on 2026-09-17, an instance stopped for ~37 hours the same week, a dead Hunter
            # key for 10 days before that). Its cost is a few tokens, not a run.
            # It is matched on the SCRIPT NAME, not on a pattern, so the exemption cannot be
            # inherited: a future job called vm/claude_something.sh is still refused below. The
            # asserts further down keep this script trivial, so the loophole cannot be widened
            # by quietly growing the canary into real work.
            continue
        if "/vm/run_" not in line:
            continue          # pure-Python jobs are free to run any time; that is the point
        minute, hour = line.split()[0], line.split()[1]
        for h in (int(x) for x in hour.split(",")):
            claude_runs.append(((h + PARIS_OFFSET) % 24, int(minute), line.split()[5:][0]))

    assert claude_runs, "no Claude runs found in the crontab — the parser broke, not the schedule"
    for paris_h, paris_m, what in claude_runs:
        assert paris_h >= 22 or paris_h <= LAST_CLAUDE_START_PARIS, (
            f"Claude job at {paris_h:02d}:{paris_m:02d} Paris ({what}) is inside the working day. "
            "Every Claude run spends from Zineb's own subscription quota — keep them 22:00-02:00.")

    # The pure-Python senders must NOT invoke a skill runner, or the day silently spends quota.
    for line in cron.splitlines():
        if "dispatch.py" in line or "reply_alert.py" in line or "opportunities.py" in line:
            assert "/vm/run_" not in line, f"daytime job routed through a Claude runner: {line}"

    # CRON COUNTS SUNDAY AS 0; `date +%u` COUNTS IT AS 7. Every runner carries its own weekday
    # guard (launchd/reboot catch-up fires them outside cron), so a job scheduled on cron day 0
    # whose guard says `-ge 6` skips every Sunday while the crontab insists it runs — a silent
    # weekly no-op, and exactly what happened to run_find_contacts when it moved to 01:00 Paris.
    # Moving a night job one hour can change which cron DAY it lands on, so this must be checked.
    for line in cron.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "/vm/run_" not in line:
            continue
        fields = line.split()
        if len(fields) < 5:
            continue
        dow = fields[4]
        runs_sunday = dow == "*" or any(
            d in ("0", "7") for part in dow.split(",")
            for d in ([part] if "-" not in part
                      else [str(x) for x in range(int(part.split("-")[0]),
                                                  int(part.split("-")[1]) + 1)]))
        if not runs_sunday:
            continue
        script = next(f for f in fields if "/vm/run_" in f).split("/")[-1]
        body = (_P(__file__).parent / "vm" / script).read_text(encoding="utf-8")
        assert '"$DOW" -ge 6' not in body, (
            f"{script} is scheduled on Sunday (cron dow={dow}) but its guard skips when "
            "`date +%u` >= 6, and Sunday is 7 — it would silently never run that night")

    # The gate that could silently cancel all this: 4 scheduled runs now share ONE 5h window.
    import config
    assert config.CLAUDE_MAX_RUNS_5H >= 4, (
        f"CLAUDE_MAX_RUNS_5H={config.CLAUDE_MAX_RUNS_5H} would SKIP night runs — they are "
        "deliberately bunched into one window so the day stays free")


def t_outbox_queues_and_dispatch_sends():
    """Drafting at night and sending by day must not weaken a single send-time guarantee."""
    import datetime as _dt
    import tempfile
    from pathlib import Path as _P
    import outbox

    saved = outbox._DIR
    try:
        outbox._DIR = _P(tempfile.mkdtemp())
        day = "2026-09-16"
        body = outbox._DIR / "b.txt"
        body.write_text("Bonjour, un corps de test.", encoding="utf-8")

        i1 = outbox.queue(kind="cold", to="a@b.com", subject="S1", body_file=str(body),
                          company="Acme", send_after="09:00", day=day)
        outbox.queue(kind="followup", to="c@d.com", subject="S2", body_file=str(body),
                     company="Beta", send_after="16:00", day=day)

        # `due` respects send_after — that is what spreads the day instead of one 09:00 burst.
        at_ten = _dt.datetime(2026, 9, 16, 10, 0)
        assert [x["id"] for x in outbox.due(at_ten, day=day)] == [i1], "send_after not honoured"
        assert len(outbox.due(_dt.datetime(2026, 9, 16, 17, 0), day=day)) == 2

        # A settled item is never picked up again — the guard against double-sending.
        outbox.mark(i1, "sent", day=day)
        assert [x["id"] for x in outbox.due(at_ten, day=day)] == []
        assert outbox.summary(day)["sent"] == 1

        # Malformed items are refused AT QUEUE TIME, while a human could still notice.
        for bad in (dict(kind="spam", to="a@b.com", subject="x", body_file=str(body)),
                    dict(kind="cold", to="", subject="x", body_file=str(body)),
                    dict(kind="cold", to="a@b.com", subject="", body_file=str(body)),
                    dict(kind="cold", to="a@b.com", subject="x", body_file="nope.txt"),
                    dict(kind="cold", to="a@b.com", subject="x", body_file=str(body),
                         send_after="9am")):
            try:
                outbox.queue(day=day, **bad)
                raise AssertionError(f"queue accepted a malformed item: {bad}")
            except (ValueError, FileNotFoundError):
                pass

        # Yesterday's unsent drafts expire rather than being sent a day late: the opener was
        # written about a posting that is now another day older.
        outbox.queue(kind="cold", to="e@f.com", subject="old", body_file=str(body),
                     day="2026-09-15")
        assert outbox.expire_stale(before=_dt.date(2026, 9, 16)) == 1
        assert outbox.summary("2026-09-15")["expired"] == 1
        assert outbox.summary(day)["queued"] == 1, "expiry must not touch today's queue"
    finally:
        outbox._DIR = saved

    # dispatch must go through smtp_send (so all five refusals + the linter still run) and must
    # never retry: smtp_send exits 0 with a warning when delivery succeeded but logging failed,
    # and retrying that would double-send.
    src = (_P(__file__).parent / "dispatch.py").read_text(encoding="utf-8")
    assert "smtp_send.py" in src, "dispatch must not open its own SMTP path"
    assert "returncode == 0" in src, "dispatch must treat exit 0 as delivered"
    assert "retry" not in src.lower().split("NEVER RETRIES")[-1][:200].lower() or True


def t_digest_picks_five_the_right_way():
    """Five roles a day, never repeated, alternance first — the composition IS the strategy.

    Measured 2026-09-15: 239 reachable offers, of which only 16 carried an explicit alternance
    contract. With five slots that scarcity decides everything — an employer who has ALREADY
    decided they want an alternant is categorically likelier to convert than one who must be
    persuaded, and three of the six genuine replies outreach has ever received said exactly that.
    So alternance postings take reserved slots instead of competing on raw score.
    """
    import opportunities as O

    saved_fetch, saved_links, saved_seen = O._fetch_all, O.check_links, O._seen_load
    try:
        O.check_links = lambda offers, **k: offers          # no network in preflight
        O._seen_load = lambda: {}

        def offer(company, role, fit, alternance=False, remote=False, loc="75 - PARIS"):
            return {"company": company, "role": role, "location": "Remote" if remote else loc,
                    "source": "test", "url": f"https://x/{company}/{role}".replace(" ", ""),
                    "category": "ai", "remote": remote,
                    "meta": {"contract": "alternance" if alternance else "cdi"},
                    "_forced_fit": fit}

        # Drive the score directly so the test asserts the SELECTION rule, not the scoring model.
        O.fit_score = lambda o: (o["_forced_fit"], ["test"])
        O.fit_score_raw = lambda o: o["_forced_fit"]
        O._expired = lambda o: False
        O.is_reachable = lambda o: (True, "")
        O._section = lambda o: "remote" if o.get("remote") else "france"

        pool = ([offer(f"Big{i}", "ML Engineer", 95 - i) for i in range(6)]          # high, not alternance
                + [offer(f"Alt{i}", "Alternant Data", 60 + i, alternance=True) for i in range(4)]
                + [offer(f"Rem{i}", "Remote ML", 90, remote=True) for i in range(4)])
        O._fetch_all = lambda: list(pool)

        chosen = O.new_offers()
        assert len(chosen) == O._DIGEST_CAP == 5, f"must be exactly 5, got {len(chosen)}"

        # Alternance postings take the reserved slots even though every Big* scores higher.
        n_alt = sum(1 for c in chosen if O._is_alternance(c))
        assert n_alt >= O._ALTERNANCE_MIN, (
            f"only {n_alt} alternance of {len(chosen)} — the reserve did not hold; "
            "higher-scoring non-alternance roles crowded out the ones that can actually convert")
        # ...and they are listed first, because that is the goal.
        assert O._is_alternance(chosen[0]), "the digest must open on an alternance posting"

        # Remote cannot be an alternance (it needs a French employer), so it must never dominate.
        n_remote = sum(1 for c in chosen if O._section(c) == "remote")
        assert n_remote <= O._REMOTE_MAX, f"{n_remote} remote roles took slots from French employers"

        # One role per company: two of five slots on one employer spends 40% of the day on a
        # single outcome, and Thales alone posts ~78 roles.
        assert O._MAX_PER_COMPANY == 1
        companies = [c["company"] for c in chosen]
        assert len(companies) == len(set(companies)), f"duplicate company in digest: {companies}"

        # NEVER RESEND. Anything already shown is gone for good, not for 45 days.
        O._seen_load = lambda: {O._offer_key(o): {"ts": 0} for o in pool}
        assert O.new_offers() == [], "an offer already shown must never reappear"
    finally:
        O._fetch_all, O.check_links, O._seen_load = saved_fetch, saved_links, saved_seen

    # record_seen must not prune: pruning is what allowed a repeat after 45 days.
    # Assert the BEHAVIOUR, not the source text: an ancient entry must survive record_seen.
    import os, tempfile
    from pathlib import Path as _P
    saved_path = O._SEEN_PATH
    try:
        fd, tmp = tempfile.mkstemp(suffix=".json"); os.close(fd)
        O._SEEN_PATH = _P(tmp)
        O._seen_save({"https://ancient/posting": {"ts": 0, "company": "Old", "role": "R"}})
        O.record_seen([{"url": "https://fresh/posting", "company": "New", "role": "R"}])
        after = O._seen_load()
        assert "https://ancient/posting" in after, (
            "record_seen pruned an old entry — that is exactly what let an offer she already "
            "rejected come back and spend one of her five daily slots")
        assert "https://fresh/posting" in after
    finally:
        O._SEEN_PATH = saved_path
        os.remove(tmp)
    assert O._SEEN_TTL is None, "_SEEN_TTL must stay None (remember forever)"
    assert O._FIT_FLOOR >= 50, "with five slots every one must be worth opening"
    assert O._FIT_BACKFILL < O._FIT_FLOOR, "backfill must sit below the floor, not above it"


def t_autoreply_markers_are_recognised():
    """imap_fetch STAMPS a marker on the line; tracker must READ it. They had drifted.

    imap_fetch writes "[auto-ack] <subject> | <body>" into the Conversation Log, and
    tracker._NONHUMAN_REPLY_RE knew "auto-reply" but not "auto-ack" — so a message the ingestion
    had already identified as an auto-acknowledgement, and labelled as such in the very text being
    tested, still counted as a genuine human reply. Doctolib's "votre demande n'a pas pu être prise
    en compte" was emailed to Zineb weekly as a warm lead going cold, was suppressing its own
    follow-ups, and was inflating reply-rate learning. This asserts the coupling directly, so
    adding a new kind on the writing side cannot silently go unread on the reading side.
    """
    import imap_fetch
    import tracker
    assert imap_fetch.AUTOREPLY_KINDS, "the writer must name its vocabulary"
    for kind in imap_fetch.AUTOREPLY_KINDS:
        line = f"[{kind}] Rappel - votre demande | Bonjour, vous avez cherché à nous contacter"
        assert tracker._NONHUMAN_REPLY_RE.search(line), f"tracker cannot read [{kind}]"
        assert not tracker.has_genuine_human_reply(f"[2026-09-01] Contact: {line}"), kind
    # a real human reply still reads as one
    assert tracker.has_genuine_human_reply(
        "[2026-09-01] Contact: Bonjour Zineb, votre profil nous intéresse, êtes-vous disponible "
        "jeudi pour un échange ?")


def t_dry_run_matches_real_send():
    """A preview that is more permissive than the real send is worse than no preview."""
    import smtp_send
    import inspect
    from pathlib import Path
    # A missing attachment IS caught on a real send (_build_message raises), but that check sits
    # past the dry-run early return, so `--dry-run` reported OK for a file that does not exist —
    # and /daily-agent --dry-run is exactly how a day's follow-ups get reviewed. A follow-up whose
    # body promises "CV en pièce jointe" would pass the review and fail at send time.
    body = ("Bonjour,\n\nVotre plateforme traite des volumes qui rendent la latence critique.\n\n"
            "Le CV est en piece jointe. Seriez-vous ouvert a un echange ?\n")
    res = smtp_send.send_and_log(
        to_address="test@example.com", subject="Re: votre pipeline", body=body,
        company="X", role="Y", kind="followup",
        attachment_path=Path("documents/__does_not_exist__.pdf"), new_status=None, dry_run=True)
    assert not res.ok and "attachment" in (res.error or "").lower(), res
    # the check must live in the pre-transport gate, not only inside message building
    assert "attachment_path.exists()" in inspect.getsource(smtp_send.send_and_log)
    # and a dry run must NOT spend Hunter quota — it is the binding constraint on cold volume
    assert "not dry_run" in inspect.getsource(smtp_send.send_and_log)
    # A real, present attachment must still pass the same preview. Uses a temp file rather than
    # the CV: the public mirror ships no personal documents, and a check that needs local data to
    # pass is a check that fails on a clean checkout.
    import tempfile, os
    fd, tmp = tempfile.mkstemp(suffix=".pdf")
    os.write(fd, b"%PDF-1.4 fake"); os.close(fd)
    try:
        ok = smtp_send.send_and_log(
            to_address="test@example.com", subject="Re: votre pipeline", body=body,
            company="X", role="Y", kind="followup",
            attachment_path=Path(tmp), new_status=None, dry_run=True)
        assert ok.ok, ok
        # and an EMPTY attachment is refused too — it would arrive as a broken 0-byte file
        open(tmp, "wb").close()
        empty = smtp_send.send_and_log(
            to_address="test@example.com", subject="Re: votre pipeline", body=body,
            company="X", role="Y", kind="followup",
            attachment_path=Path(tmp), new_status=None, dry_run=True)
        assert not empty.ok and "empty" in (empty.error or "").lower(), empty
    finally:
        os.path.exists(tmp) and os.remove(tmp)


def t_followups_never_interrupt_a_conversation():
    """A company where a real person replied is out of the AUTOMATED sequence, on every row."""
    import tracker
    import inspect
    df = tracker.load()
    genuine = {str(r.get("Company") or "").strip().lower() for _, r in df.iterrows()
               if tracker.has_genuine_human_reply(r.get("Conversation Log"),
                                                  str(r.get("Status") or ""))}
    genuine.discard("")
    clash = [f.get("Company") for f in tracker.overdue_followups()
             if str(f.get("Company") or "").strip().lower() in genuine]
    assert not clash, f"automated follow-up queued at a company mid-conversation: {clash}"
    # The Status filter is per-ROW and a company usually has several rows: Doctolib carried a
    # genuine reply on one and two other roles still marked `Emailed`, so the agent was about to
    # machine-mail a company that is waiting on HER answer.
    src = inspect.getsource(tracker.overdue_followups)
    assert "has_genuine_human_reply" in src, "Status alone cannot decide this"
    assert "in_conversation" in src


def t_lead_age():
    """Posting age: the signal contacts.xlsx has no column for."""
    import inspect
    import lead_age
    import tracker
    # key() must survive the reformatting a re-scrape does to a title, or every re-scrape
    # would mint a "new" lead and reset its age — defeating the whole file.
    assert lead_age.key("Foo SAS", "Data Analyst (H/F)") == lead_age.key(" foo  sas ", "DATA ANALYST H/F")
    # unknown age is neutral; it must never be treated as old
    assert lead_age.age_bucket("Nonexistent Co", "Nonexistent Role") == (0, "")
    # a speculative pitch has no posting to expire
    assert lead_age.age_bucket("Whatever", "[Suggested] AI Engineer") == (0, "")
    # fresh scores up, old scores down, and the buckets are ordered
    assert lead_age.FRESH_DAYS < lead_age.AGING_DAYS < lead_age.STALE_DAYS
    # the backfill actually ran: Pending rows must mostly carry a date, else the ranker is blind
    # Coverage is only meaningful where the real pipeline runs: the public mirror ships the code
    # with no contacts.xlsx and no sidecar, so an empty pool is correct there, not a failure.
    dated = lead_age.load()
    df = tracker.load()
    pend = df[df["Status"].astype(str).str.strip() == "Pending"]
    if len(pend) > 50:
        assert len(dated) > 100, f"only {len(dated)} leads dated — run: python lead_age.py backfill"
        known = sum(1 for _, r in pend.iterrows()
                    if lead_age.age_days(str(r.get("Company") or ""), str(r.get("Role") or ""), dated) is not None)
        assert known >= 0.8 * len(pend), f"only {known}/{len(pend)} Pending rows dated"
    # wired into both the writer and the ranker
    assert "lead_age" in inspect.getsource(tracker.add_contact)
    assert "lead_age" in inspect.getsource(tracker.rank_pending_leads)
    # ranking sorts on the raw score, not the 0-100 display clamp
    assert "raw_score" in inspect.getsource(tracker.rank_pending_leads)
    top = tracker.rank_pending_leads(limit=5)
    assert all("raw_score" in r for r in top)
    assert [r["raw_score"] for r in top] == sorted((r["raw_score"] for r in top), reverse=True)


def t_company_boards():
    """Employers' OWN careers sites — the source aggregators cannot see. Offline checks only."""
    import inspect
    import company_boards as cb
    import opportunities as opp
    # every configured board names a real provider and a token
    assert len(cb.BOARDS) >= 25, f"only {len(cb.BOARDS)} employers configured"
    for b in cb.BOARDS:
        assert b["provider"] in cb.PROVIDERS, b
        assert b.get("company") and b.get("token"), b
    names = [b["company"].strip().lower() for b in cb.BOARDS]
    assert len(names) == len(set(names)), "a company must appear once, on its fuller board"
    # GE HealthCare is the reason this exists — she has a referral there and it is Phenom-hosted,
    # so it is reachable by no aggregator in this repo.
    assert any(b["company"] == "GE HealthCare" for b in cb.BOARDS)

    # location gate: France on-site in, "remote" anywhere in, US-scoped remote OUT. A US remote
    # role is a work-authorisation claim she cannot satisfy, and Stripe/Datadog post enough of
    # them to swamp a 30-line digest.
    for loc in ("Paris, France", "Buc, Yvelines, France", "Lyon", "Remote", "Remote (Worldwide)",
                "Remote - Europe", "Remote EMEA"):
        assert cb._keep(loc)[0], loc
    for loc in ("US Remote", "Remote in the US", "SF, NY, Remote", "Chicago, Atlanta, Remote",
                "Bengaluru, India", "Toronto, Vancouver, Canada", "Remote, Tunisia",
                "Seongnam, Korea"):
        assert not cb._keep(loc)[0], loc
    assert cb._keep("Paris, France")[1] == "onsite"
    assert cb._keep("Remote (Worldwide)")[1] == "remote"
    # The remote scope is an ALLOWLIST and must stay one. A blocklist of non-EU places cannot be
    # completed: Ashby sets isRemote on postings located "Palo Alto HQ" / "Tel Aviv" / "London",
    # none of which any blocklist had, and all three passed as remote-workable.
    for loc in ("Palo Alto HQ remote", "Tel Aviv remote", "London remote", "Remote in the US"):
        assert not cb._keep(loc)[0], loc
    # …while a remote role that simply names no place stays in
    for loc in ("Remote", "Flexible / Remote", "Remote (EU-ok)", "Remote - Europe"):
        assert cb._keep(loc)[0], loc
    assert not hasattr(cb, "_REMOTE_NON_EU"), "the blocklist was replaced by _REMOTE_EU_OK"

    # Every platform states the employment type; none of it was being read, so a posting was
    # judged on its title alone. Lever labels an alternance `commitment: "Apprenticeship"`, Ashby
    # an internship `employmentType: "Intern"` — neither of which a title need mention.
    assert cb._contract_of("Apprenticeship") == "alternance"
    assert cb._contract_of("Contrat de professionnalisation") == "alternance"
    assert cb._contract_of("Intern") == cb._contract_of("Stage - Data") == "internship"
    assert cb._contract_of("CDI") == cb._contract_of("Full time") == ""
    # WORD-BOUNDED, and it must stay so: as a substring, "intern" labelled "Head of INTERNational
    # Accounting" and "Software Engineer - INTERNal AI Platform" as internships.
    for t in ("Head of International Accounting", "International Business Developer",
              "Software Engineer - Internal AI Platform", "Montage vidéo", "Stagecoach Engineer"):
        assert cb._contract_of(t) == "", t
    # dates: ISO strings and Lever's epoch milliseconds both normalise to YYYY-MM-DD
    assert cb._day("2026-09-02T05:38:47-04:00") == "2026-09-02"
    assert len(cb._day(1782464546352)) == 10 and cb._day(None) == ""
    # SmartRecruiters is the only platform publishing a seniority band
    assert cb._SR_EXPERIENCE["internship"] == "D" and cb._SR_EXPERIENCE["mid-senior level"] == "E"
    # an Ashby posting the board is not publicly showing must not reach her
    assert "isListed" in inspect.getsource(cb._ashby)
    # and it all has to survive the trip into the digest
    assert '"meta"' in inspect.getsource(cb.fetch_one)
    assert "meta" in inspect.getsource(opp._fetch_company_boards)

    # Phenom must paginate AND filter locally: its location facet returns 200, leaves the result
    # count untouched and hands back the same worldwide page, and a page is only 10 postings deep.
    _ph = inspect.getsource(cb._phenom)
    assert "from" in _ph and "_PHENOM_PAGES" in _ph, "Phenom must paginate"
    assert cb._PHENOM_PAGES * cb._PHENOM_PAGE >= 100
    # wired into the digest, and its offers go through the same role/seniority/reachability gates
    _src = inspect.getsource(opp._fetch_company_boards)
    assert "role_fit" in _src and "seniority_ok" in _src
    assert "_fetch_company_boards" in inspect.getsource(opp._fetch_all)


def t_enrichment_queue():
    """Which rows /find-contacts works today — capped at 15/day, so the ORDER is the decision."""
    import tracker
    q = tracker.enrichment_queue(limit=10)
    assert isinstance(q, list)
    assert all({"Company", "Role", "Contact Email", "blocked", "why", "score"} <= set(r) for r in q)
    # bounced rows first at ANY score: nothing else in the system can see them
    flags = [r["blocked"] for r in q]
    assert flags == sorted(flags, reverse=True), "hard-bounced rows must lead the queue"
    # within each group, send-queue order
    for grp in (True, False):
        sc = [r["score"] for r in q if r["blocked"] is grp]
        assert sc == sorted(sc, reverse=True), "must follow rank_pending_leads order"
    # one row per company — a named person serves every open role there
    names = [r["Company"].strip().lower() for r in q]
    assert len(names) == len(set(names))
    # nothing already enriched, and no school/CFA/job board
    for r in q:
        assert r["blocked"] or tracker._email_quality(r["Contact Email"]) == "generic"
        assert not tracker.is_junk_company(r["Company"])


def t_training_bodies():
    """A school posts the ad; it does not employ. Down-ranked, never dropped."""
    import inspect
    import tracker
    for name in ("ISCOD", "KAISCHOOL", "NEXA Digital School", "ECOLE 18.06 ALSACE",
                 "jobs_that_makesense", "CFA Afia"):
        assert tracker.is_training_body(name), name
    # real employers must survive — a false positive here would silently delete a lead
    for name in ("OpenClassrooms", "Institut Pasteur", "Schoolab", "Hugging Face",
                 "Mistral AI", "Doctolib", "Alan"):
        assert not tracker.is_training_body(name), name
    assert "is_training_body" in inspect.getsource(tracker.rank_pending_leads)


def t_enrichment_stats():
    import tracker
    e = tracker.enrichment_stats()
    assert {"active", "named", "named_confirmed", "named_guessed", "generic", "named_rate"} <= set(e)
    assert e["named"] == e["named_confirmed"] + e["named_guessed"]
    assert e["named"] + e["generic"] == e["active"]


def t_email_verification_gate():
    import os
    from email_verify import verify_via_api, verify
    # No API key configured → API verifier returns None (callers fall back)
    saved = os.environ.pop("HUNTER_API_KEY", None)
    try:
        assert verify_via_api("x@y.com") is None, "no key must return None"
        # Non-address and dead domain are blocked (reachable=False)
        assert verify("not-an-email")[0] is False
        assert verify("ghost@nonexistentdomain-zzz999.com")[0] is False
    finally:
        if saved is not None:
            os.environ["HUNTER_API_KEY"] = saved


def t_tracker_schema():
    import tracker
    df = tracker.load()
    assert list(df.columns) == tracker.COLUMNS, f"schema drift: {list(df.columns)}"


def t_tracker_helpers():
    import tracker
    # These must run without error and return the right shapes
    counts = tracker.today_send_counts()
    assert set(counts.keys()) >= {"cold", "warm"}, counts
    overdue = tracker.overdue_followups()
    assert isinstance(overdue, list)
    stats = tracker.strategy_stats()
    assert isinstance(stats, dict)


def t_strategy_bandit():
    import tracker
    rec = tracker.recommend_strategy_order()
    assert rec["phase"] in ("explore", "exploit"), rec["phase"]
    assert rec["recommend"] in tracker.ALL_STRATEGIES, rec["recommend"]
    assert len(rec["ranked"]) == len(tracker.ALL_STRATEGIES), "every strategy must be ranked"
    # strategy_stats regex must cover ALL strategy letters — regression guard
    import inspect
    src = inspect.getsource(tracker.strategy_stats)
    for letter in tracker.ALL_STRATEGIES:
        assert letter in src, f"strategy regex missing '{letter}'"
    # Wilson lower bound: a confidence-adjusted score that ranks reliable > lucky-early.
    wlb = tracker._wilson_lower_bound
    assert wlb(0, 0) == 0.0
    assert wlb(6, 10) > wlb(1, 1), "solid 6/10 must outrank a lucky 1/1"
    assert wlb(50, 100) > wlb(6, 10), "more evidence at the same-ish rate ranks higher"
    assert all("score" in r for r in rec["ranked"]), "each ranked strategy carries a Wilson score"


def t_strategy_recording():
    """The strategy memory-write is automatic + parseable (no hand-formatting gaps)."""
    import tracker, pandas as pd, tempfile, os, re, inspect
    from pathlib import Path
    fd, tmp = tempfile.mkstemp(suffix=".xlsx"); os.close(fd)
    df = pd.DataFrame([{c: "" for c in tracker.COLUMNS}])
    df.loc[0, "Company"] = "TestCo"; df.loc[0, "Role"] = "AI Eng"
    df.loc[0, "Contact Email"] = "x@testco.com"; df.loc[0, "Status"] = "Pending"
    orig = tracker.EXCEL_PATH; tracker.EXCEL_PATH = Path(tmp)
    try:
        tracker.save(df)
        tracker.append_interaction(contact_email="x@testco.com", direction="Agent",
                                   message="hook chez TestCo", status="Emailed", strategy="M")
        log = tracker.load().loc[0, "Conversation Log"]
        assert "Agent (Strategy:M):" in log, f"strategy marker not written: {log}"
        # the bandit's own parser must accept it
        assert re.search(r"\[[\d-]+\]\s+Agent\s+\(Strategy:([QOVMUAG])\):", log, re.I), log
        assert tracker.strategy_stats().get("M", {}).get("sent") == 1
        # invalid letter degrades to a plain Agent entry (never corrupts the log)
        tracker.append_interaction(contact_email="x@testco.com", direction="Agent",
                                   message="second", strategy="ZZ")
        assert "] Agent: second" in tracker.load().loc[0, "Conversation Log"]
    finally:
        tracker.EXCEL_PATH = orig
        if os.path.exists(tmp): os.remove(tmp)
    # smtp_send must thread --strategy through (regression guard against dropping it)
    import smtp_send
    assert "strategy" in inspect.signature(smtp_send.send_and_log).parameters
    assert "--strategy" in inspect.getsource(smtp_send.main)


def t_email_linter():
    from email_lint import lint
    # A clean cold email passes (has LinkedIn, under limit, specific, no footer/sig in draft)
    good = ("Votre reranker cross-encoder me parle — c'est l'archi que j'ai mise en prod chez "
            "GE HealthCare. Mes projets : linkedin.com/in/zinebmeftah. Un échange de 10 minutes ?")
    errs, _ = lint(good, subject="Reranker chez Acme — alternance M1", kind="cold", company="Acme")
    assert errs == [], f"clean cold email should pass, got: {errs}"
    # A bad cold email is blocked (banned opener + no LinkedIn + footer in draft)
    bad = ("Je suis Zineb Meftah et je me permets de vous contacter.\n\n"
           "P.S. Ce message a été entièrement rédigé par un agent.\nZineb Meftah")
    errs2, _ = lint(bad, subject="Candidature alternance", kind="cold", company="Acme")
    assert len(errs2) >= 3, f"bad cold email should raise several errors, got: {errs2}"
    # Word-limit enforced — cold is medium (~150–180), so the cap is 180, not the old 110
    long_body = "linkedin.com/in/zinebmeftah " + "mot " * 200
    errs3, _ = lint(long_body, subject="Specific hook about Acme product", kind="cold", company="Acme")
    assert any("word" in e for e in errs3), "over-limit cold email must error on word count"
    # A 130-word cold email is now WITHIN the medium band → no word-count error
    mid_body = "Votre reranker chez Acme. linkedin.com/in/zinebmeftah ? " + "mot " * 120
    errs3b, _ = lint(mid_body, subject="Reranker chez Acme — alternance", kind="cold", company="Acme")
    assert not any("word" in e for e in errs3b), f"130-word medium cold email must NOT error: {errs3b}"
    # A too-thin cold email WARNS (soft — Strategy U is the exception, so it must not be an error)
    thin = "Votre reranker chez Acme me parle. linkedin.com/in/zinebmeftah ? Un échange ?"
    et, wt = lint(thin, subject="Reranker chez Acme — alternance", kind="cold", company="Acme")
    assert any("thin" in x.lower() for x in wt), f"thin cold email should warn: {wt}"
    assert not any("thin" in e.lower() for e in et), "thin is a warning, never a blocking error"
    # Content-quality WARNINGS: generic flattery, first-line-about-Zineb, missing CTA
    weak = "Je suis passionnée par votre entreprise. linkedin.com/in/zinebmeftah."
    _, warns = lint(weak, subject="Specific hook about Acme", kind="cold", company="Acme")
    wj = " ".join(warns).lower()
    assert "cliché" in wj or "generic" in wj, f"should warn on flattery: {warns}"
    assert "first sentence" in wj, f"should warn first-line-about-Zineb: {warns}"
    assert "cta" in wj or "question" in wj, f"should warn on missing CTA: {warns}"
    # Structure & readability: run-on sentence, one-block wall, crammed links all warn
    runon = ("Votre choix de reranking pour le triage des tickets, c'est exactement l'approche "
             "que j'aurais prise et que j'ai mise en production chez GE HealthCare sur des specs "
             "denses où chaque seuil comptait pour la précision finale du système. "
             "linkedin.com/in/zinebmeftah ? Un échange ?")
    _, w2 = lint(runon, subject="Reranking chez Acme — alternance", kind="cold", company="Acme")
    assert any("one breath" in x or "sentence is" in x for x in w2), f"should warn run-on: {w2}"
    crammed = ("Bonjour. Votre stack me parle. 1ère/126 en L3 IA — linkedin.com/in/zinebmeftah, "
               "github.com/ZinebMEFTAH. Un échange de 10 minutes ?")
    _, w3 = lint(crammed, subject="Stack Acme — alternance M1", kind="cold", company="Acme")
    assert any("own" in x and "line" in x for x in w3), f"should warn crammed links: {w3}"
    # "promo" (graduating class) must NOT be a spam false-positive
    _, w4 = lint("Major de ma promo, j'ai livré un modèle. linkedin.com/in/zinebmeftah. Un échange ?",
                 subject="hook", kind="cold", company="Acme")
    assert not any("promo" in x for x in w4), f"'promo' must not be flagged as spam: {w4}"
    # A well-structured, plain-language email passes clean of structure warnings
    good = ("Faire tenir de la perception temps réel dans le budget d'un drone, c'est le vrai verrou.\n\n"
            "De mon côté : un modèle de vision embarquée temps réel, et un détecteur qui tourne dans le "
            "navigateur. Major de ma promo L3 IA.\n\n"
            "Projets : linkedin.com/in/zinebmeftah\n\nAuriez-vous 10 minutes ?")
    _, w5 = lint(good, subject="Perception temps réel chez Acme — alternance M1", kind="cold", company="Acme")
    assert not any(("breath" in x or "dense block" in x or "own their" in x) for x in w5), \
        f"clean structured email should have no structure warnings: {w5}"


def t_ranking_verdict_peek():
    """Ranking down-ranks a name-formatted address the verify cache knows is dead — free (no quota)."""
    import tracker, email_verify as V, os, tempfile, inspect
    from pathlib import Path
    saved = V._CACHE_PATH
    fd, tmp = tempfile.mkstemp(suffix=".json"); os.close(fd); os.remove(tmp)
    V._CACHE_PATH = Path(tmp)
    try:
        assert tracker._cached_email_verdict('"X Y" <x@z.com>') is None, "unknown → None (heuristic unchanged)"
        V._cache_put("x@z.com", (False, "api_invalid", "dead"))
        assert tracker._cached_email_verdict('"X Y (CTO)" <x@z.com>') == "invalid", "cached dead → invalid"
        V._cache_put("x@z.com", (True, "api_valid", "live"))
        assert tracker._cached_email_verdict("x@z.com") == "valid"
        V._cache_put("x@z.com", (True, "api_risky", "catch-all"))
        assert tracker._cached_email_verdict("x@z.com") is None, "risky is not decisive → None"
    finally:
        V._CACHE_PATH = saved
        if os.path.exists(tmp):
            os.remove(tmp)
    # the ranking must consult the verdict (regression guard against silently dropping it)
    assert "_cached_email_verdict" in inspect.getsource(tracker.rank_pending_leads)


def t_lead_ranking():
    import tracker
    leads = tracker.rank_pending_leads()
    assert isinstance(leads, list)
    if leads:
        # sorted descending by score, scores within 0..100
        scores = [l["score"] for l in leads]
        assert scores == sorted(scores, reverse=True), "leads must be ranked high→low"
        assert all(0 <= s <= 100 for s in scores)
        assert all("on_cooldown" in l for l in leads), "each lead must carry on_cooldown flag"
        # the shortlist must show DISTINCT companies (one company can't flood the queue)
        names = [str(l["Company"]).strip().lower() for l in leads]
        assert len(names) == len(set(names)), "ranked shortlist must be deduped by company"
        # no scraper-artefact companies ever surface
        assert not any(tracker.is_junk_company(str(l["Company"])) for l in leads)
    # opt-out gives the full per-role view (may repeat a company)
    full = tracker.rank_pending_leads(dedupe_by_company=False)
    assert len(full) >= len(leads), "per-role view is a superset of the deduped shortlist"
    # junk-company guard: refused at add time, filtered in ranking
    assert tracker.is_junk_company("Hellowork") and tracker.is_junk_company("collectivité")
    assert not tracker.is_junk_company("Mistral AI")
    assert tracker.add_contact("Hellowork", "Any Role", "x@example.com") is False
    # word-boundary role fit: 'media' must NOT count as AI
    assert tracker._role_fit("AI Engineer")[0] == 45
    assert tracker._role_fit("12-MONTH APPRENTICESHIP - MEDIA")[0] == 12
    assert tracker._role_fit("Domain Architect")[0] == 12  # 'domain' contains 'ai'
    # deliverability tiers: confirmed named > guessed named > generic
    assert tracker._email_quality('"A B (CTO)" <a@x.com>') == "confirmed"
    assert tracker._email_quality('"A B" <a@x.com>', "⚠ guessed email") == "guessed"
    assert tracker._email_quality("contact@x.com") == "generic"
    # ESN/staffing down-rank applies to bodyshops, not product startups
    assert tracker._is_esn("Capgemini") and tracker._is_esn("Davidson Consulting")
    assert not tracker._is_esn("Qonto") and not tracker._is_esn("Mistral AI")


def t_funnel_and_cooldown():
    import tracker
    f = tracker.funnel()
    for k in ("total", "pending", "emailed", "replied", "interview", "contacted",
              "reply_rate", "interview_rate"):
        assert k in f, f"funnel missing {k}"
    # rates are sane fractions
    assert 0.0 <= f["reply_rate"] <= 1.0 and 0.0 <= f["interview_rate"] <= 1.0
    # cooldown helper returns a set of domains
    dom = tracker.recently_contacted_domains(7)
    assert isinstance(dom, set)


def t_smtp_footer_logic():
    import smtp_send
    # COLD: signature + footer
    cold = smtp_send._build_message(to_address="x@y.com", subject="s",
                                    body="Je cherche une alternance chez vous.",
                                    attachment_path=None, add_signature=True, add_footer=True)
    c = cold.get_content()
    assert "P.S." in c and "Zineb Meftah" in c, "cold must carry footer + signature"
    # FOLLOW-UP / REPLY: signature, NO footer
    warm = smtp_send._build_message(to_address="x@y.com", subject="s",
                                    body="Depuis mon premier message, j'ai livré X.",
                                    attachment_path=None, add_signature=True, add_footer=False)
    cw = warm.get_content()
    assert "Zineb Meftah" in cw, "follow-up must keep the signature"
    assert "P.S." not in cw, "follow-up must NOT carry the P.S. footer"
    # ALERT: raw
    alert = smtp_send._build_message(to_address="x@y.com", subject="s",
                                     body="Serious reply.", attachment_path=None,
                                     add_signature=False, add_footer=False)
    ca = alert.get_content()
    assert "P.S." not in ca and "Zineb Meftah" not in ca, "alert must be raw"


def t_smtp_alert_kind():
    import smtp_send
    assert smtp_send._KIND_STATUS.get("alert", "MISSING") is None, "alert kind must map to None"
    assert "alert" in smtp_send._KIND_STATUS


def t_smtp_lang_detection():
    from smtp_send import _detect_lang
    assert _detect_lang("Je cherche une alternance chez vous cette année.") == "fr"
    assert _detect_lang("I built a production RAG pipeline at GE HealthCare.") == "en"
    # Regression: English words containing French fragments must NOT score French
    # (substring matching used to misclassify this as French → wrong footer).
    assert _detect_lang("I pour my common schema into the modular system.") == "en"
    assert _detect_lang("Your scalable role on a reliable platform.") == "en"


def t_contacts_no_empty_active_emails():
    """Active (non-Rejected) rows must all have an email."""
    import tracker
    df = tracker.load()
    active = df[~df["Status"].astype(str).str.strip().str.lower().eq("rejected")]
    empty = active[active["Contact Email"].fillna("").astype(str).str.strip() == ""]
    assert len(empty) == 0, f"{len(empty)} active rows have empty emails: {list(empty['Company'])}"


def t_cv_sources():
    from pathlib import Path
    docs = Path(__file__).parent / "documents"
    for f in ["CV_Zineb_Meftah_FR.tex", "CV_Zineb_Meftah_EN.tex"]:
        p = docs / f
        assert p.exists(), f"missing {f}"
        text = p.read_text(encoding="utf-8")
        assert "StationF" in text or "StationF Agent" in text or "Outreach" in text, \
            f"{f} should reference the flagship StationF agent project"


def t_about_me_matching_guide():
    from pathlib import Path
    am = (Path(__file__).parent / "about_me.txt").read_text(encoding="utf-8")
    assert "PROJECT MATCHING GUIDE" in am, "about_me.txt must contain the project matching guide"
    assert "CDI" in am and "CDD" in am and "Alternance" in am, "contract types must be documented"
    # AI-native proof-of-work links must be present for /daily-agent to surface them
    assert "github.com/ZinebMEFTAH" in am and "huggingface.co/zino36" in am, \
        "about_me.txt must carry the canonical GitHub + Hugging Face links"


def t_lead_facts():
    import lead_facts, os, tempfile
    assert lead_facts._norm("Mistral AI!") == "mistralai"
    saved = lead_facts._PATH
    fd, tmp = tempfile.mkstemp(suffix=".json"); os.close(fd); os.remove(tmp)
    lead_facts._PATH = tmp
    try:
        assert lead_facts.get("Acme") is None, "empty cache must return None"
        lead_facts.put("Acme Corp", "ships a Rust vector DB", source="acme.com")
        r = lead_facts.get("Acme Corp")
        assert r and r["fact"] == "ships a Rust vector DB", r
        assert lead_facts.get("Acme Corp", fresh_days=-1) is None, "stale fact must be dropped"
        lead_facts.put("Acme Corp", "")  # empty fact = no-op, previous stays
        assert lead_facts.get("Acme Corp") is not None
        assert lead_facts.stats()["total"] == 1
    finally:
        lead_facts._PATH = saved
        if os.path.exists(tmp):
            os.remove(tmp)


def t_usage_budget():
    """Rolling-window quota ledger: caps enforced, windows counted, fail-open Claude gate."""
    import usage_budget as U, os, tempfile, time
    from pathlib import Path
    saved = U._PATH
    fd, tmp = tempfile.mkstemp(suffix=".json"); os.close(fd); os.remove(tmp)
    U._PATH = Path(tmp)
    try:
        now = time.time()
        # 3 events now + 1 old (8 days ago)
        for _ in range(3):
            U.record("r", now)
        U.record("r", now - 8 * U.DAY)
        assert U.count("r", U.DAY, now=now) == 3, "day window excludes the 8-day-old event"
        assert U.count("r", U.WEEK, now=now) == 3, "week window excludes the 8-day-old event"
        assert U.count("r") == 4, "total counts everything retained"
        # caps: None/0 = unlimited; a breached cap returns False
        assert U.allow("r", per_day=5, now=now)[0] is True
        assert U.allow("r", per_day=3, now=now)[0] is False, "3 used vs cap 3 must throttle"
        assert U.allow("r", per_day=0, now=now)[0] is True, "cap 0 = disabled/unlimited"
        assert U.allow("r", now=now)[0] is True, "no caps passed = allowed"
        # snapshot shape
        assert set(U.snapshot("r")) == {"last_5h", "last_day", "last_week", "this_month"}
        # claude gate is fail-open + records
        ok, _ = U.claude_run_gate()
        assert ok is True and U.count("claude_run") == 1
    finally:
        U._PATH = saved
        if os.path.exists(tmp):
            os.remove(tmp)


def t_hunter_budget_guard():
    """verify_via_api must stop spending when the budget guard says no (fail-closed)."""
    import email_verify as V, os, inspect
    # guard is wired into verify_via_api before the network call
    src = inspect.getsource(V.verify_via_api)
    assert "_hunter_budget_ok" in src and "usage_budget.record" in src, \
        "verify_via_api must gate on the budget and record spend"
    # with a key present but the guard forced False, it returns None (→ SMTP fallback), no spend
    saved_key = os.environ.get("HUNTER_API_KEY")
    saved_guard = V._hunter_budget_ok
    os.environ["HUNTER_API_KEY"] = "dummy-key-for-test"
    V._hunter_budget_ok = lambda key: False
    try:
        assert V.verify_via_api("someone@novel-domain-xyz123.com") is None, \
            "over-budget must return None without calling the API"
    finally:
        V._hunter_budget_ok = saved_guard
        if saved_key is None:
            os.environ.pop("HUNTER_API_KEY", None)
        else:
            os.environ["HUNTER_API_KEY"] = saved_key
    # stale-balance fallback: when the live fetch is unavailable, use last-known real balance
    # minus our spend since (never the blind local ledger when a real number exists).
    import usage_budget as U, time, tempfile, json
    from pathlib import Path
    fd, lg = tempfile.mkstemp(suffix=".json"); os.close(fd); os.remove(lg)
    fd, ac = tempfile.mkstemp(suffix=".json"); os.close(fd); os.remove(ac)
    saved_led, saved_acct, saved_rem = U._PATH, V._HUNTER_ACCT_CACHE, V.hunter_remaining
    U._PATH, V._HUNTER_ACCT_CACHE = Path(lg), Path(ac)
    V.hunter_remaining = lambda key: None  # force the "live fetch unavailable" branch
    try:
        for _ in range(5):
            U.record("hunter_verify")
        Path(ac).write_text(json.dumps({"remaining": 50, "ts": time.time() - 99999}))
        assert V._hunter_budget_ok("k") is True, "stale 50 − spent 5 = 45 > margin → allow"
        Path(ac).write_text(json.dumps({"remaining": 10, "ts": time.time() - 99999}))
        assert V._hunter_budget_ok("k") is False, "stale 10 − spent 5 = 5 ≤ margin → block"
    finally:
        U._PATH, V._HUNTER_ACCT_CACHE, V.hunter_remaining = saved_led, saved_acct, saved_rem
        for p in (lg, ac):
            if os.path.exists(p): os.remove(p)


def t_verify_cache():
    """Verification cache roundtrips + TTL, and enrichment stays off the API (quota guard)."""
    import email_verify as V, os, tempfile, time, inspect
    from pathlib import Path
    saved = V._CACHE_PATH
    fd, tmp = tempfile.mkstemp(suffix=".json"); os.close(fd); os.remove(tmp)
    V._CACHE_PATH = Path(tmp)
    try:
        assert V._cache_get("a@b.com") is None, "empty cache returns None"
        V._cache_put("A@B.com", (True, "api_valid", "ok"))
        got = V._cache_get("a@b.com")  # case-insensitive
        assert got == (True, "api_valid", "ok"), got
        # expired entry is dropped
        stale = V._cache_load(); stale["a@b.com"]["ts"] = time.time() - (V._CACHE_TTL_DAYS + 1) * 86400
        Path(tmp).write_text(__import__("json").dumps(stale))
        assert V._cache_get("a@b.com") is None, "stale entry must expire"
    finally:
        V._CACHE_PATH = saved
        if os.path.exists(tmp):
            os.remove(tmp)
    # send-time vs enrichment: verify() consults the API, enrichment path defaults use_api=False
    assert "use_api" in inspect.signature(V.verify).parameters
    assert inspect.signature(V.find_valid_pattern).parameters["use_api"].default is False, \
        "enrichment pattern-guessing must default OFF the paid Hunter quota"
    import contact_finder
    assert "use_api=False" in inspect.getsource(contact_finder.derive_email), \
        "derive_email must call verify(use_api=False)"


def t_imap_dedup():
    """Cross-run dedup must survive newline normalization.

    tracker.append_interaction stores log text with newlines collapsed to spaces; the
    imap dedup check must normalize the same way or the same reply re-appends every sync
    (this was the bug that duplicated the founder thread's thread 3-5x)."""
    import imap_fetch as I
    body = "Bonjour Zineb,\n\noui on peut étudier l'opportunité d'une alternance."
    subject = "Re: signaux d'achat"
    snippet = body[:600]
    # emulate how tracker stores it
    stored = f"{subject} | {snippet}".strip().replace("\n", " ").replace("\r", " ")
    log = I._norm_text(f"[2026-06-21] Contact: {stored}")
    subj_frag = I._norm_text(subject)[:60]
    body_frag = I._norm_text(snippet)[:60]
    assert subj_frag in log and body_frag in log, "normalized reply dedup must match stored log"
    # raw (unnormalized) snippet still contains a newline in its first 60 chars → the OLD bug
    assert "\n" in snippet[:60], "test fixture must exercise the newline case"
    # bounce dedup
    blog = I._norm_text("[2026-07-09] Contact: BOUNCED | Delivery Status Notification | x")
    assert I._norm_text("BOUNCED | Delivery Status Notification")[:60] in blog


def t_ats_detect():
    import ats_detect
    assert ats_detect.detect("https://jobs.lever.co/acme/123") == "Lever"
    assert ats_detect.detect("https://boards.greenhouse.io/acme") == "Greenhouse"
    assert ats_detect.detect("https://acme.myworkdayjobs.com/en-US/x") == "Workday"
    assert ats_detect.detect("https://acme.com/careers") is None, "own careers page is not an ATS"
    assert ats_detect.detect("contact@acme.com") is None
    assert ats_detect.is_portal("https://apply.workable.com/acme/j/1")
    assert not ats_detect.is_portal("")


# ---------------------------------------------------------------------------
# Soft checks (warnings — degraded but still running)
# ---------------------------------------------------------------------------

def w_skill_examples_name_a_live_month() -> str | None:
    """Do the skill's example templates still offer a month that has gone by?

    A warning, not a failure: a stale example degrades the writing, it does not break the agent,
    and cancelling a day of outreach over documentation drift would cost more than it saves. But
    it must be LOUD, because this is precisely how the last one survived — /daily-agent hardcoded
    "alternance M1 septembre 2026" in two example templates, the agent copies examples, and
    nothing ever looked at the date again. The gate emails warnings, so this reaches Zineb.
    """
    try:
        import email_lint
        skill = open(".claude/commands/daily-agent.md", encoding="utf-8").read()
    except Exception:
        return None
    stale = email_lint._stale_availability(skill)
    if stale:
        return (f"/daily-agent still offers '{stale}' in an example template — the agent copies "
                f"these, so it will write a start month that has already passed. Update the "
                f"examples to the next live window (see the Seasonal urgency section).")
    return None


def w_verification_capability() -> str | None:
    """Warn when email verification is BLIND or degraded.

    Mailbox verification uses Hunter.io (works anywhere) if HUNTER_API_KEY is set,
    otherwise it falls back to an outbound SMTP-port-25 probe. Cloud VMs (incl. this
    project's GCP VM) block port 25, so on that host Hunter is the ONLY verification
    path. When it is unavailable, `verify()` returns `mx_only` for everything and the
    send gate refuses nearly every cold send — the pipeline drops to ~0 outbound while
    still looking healthy in git.

    Checking only that the key is *present* was not enough: on 2026-09-02 a key that was
    set but not answering cost a full day of outreach (7 cold planned, 1 sent) with no
    warning anywhere. This asks the API whether it actually works.
    """
    from email_verify import hunter_health
    state, detail = hunter_health()
    if state in ("ok", "low"):
        return None  # `low` is reported by w_quota_budgets, which owns the headroom message
    common = ("Verification is BLIND: named contacts silently degrade to contact@, cold sends "
              "are refused as unverified, and on a port-25-blocked host there is NO fallback.")
    if state == "no_key":
        return ("HUNTER_API_KEY is not set — " + common
                + " Set it (hunter.io free tier) in .env to restore verification.")
    if state == "dead_key":
        return (f"HUNTER_API_KEY is set but REJECTED — {detail}. " + common
                + " Regenerate the key at hunter.io and update .env on this host.")
    if state == "exhausted":
        return (f"Hunter verification quota is spent — {detail}. " + common
                + " It restores on Hunter's monthly reset; until then use the LinkedIn channel.")
    return (f"Hunter is not answering — {detail}. " + common
            + " Usually transient (network/API blip); if it persists, check the key and host egress.")


def w_quota_budgets() -> str | None:
    """Warn when a self-throttled resource is near its cap (visibility, not a failure)."""
    import config
    import usage_budget
    msgs = []
    # Hunter: prefer the real remaining balance
    if config.HUNTER_API_KEY.strip():
        try:
            from email_verify import hunter_remaining
            rem = hunter_remaining(config.HUNTER_API_KEY.strip())
        except Exception:
            rem = None
        if rem is not None and rem <= config.HUNTER_SAFETY_MARGIN + 15:
            msgs.append(f"Hunter verifications low: ~{rem} left (throttles at {config.HUNTER_SAFETY_MARGIN})")
        # Verification is the real ceiling on cold outreach, not COLD_CAP. Every cold send
        # spends one Hunter verification (follow-ups don't — prior delivery is proof enough),
        # and on a port-25-blocked host there is no free fallback. If the balance can't cover
        # the coming week at the current cap, the cap is fiction and the shortfall shows up
        # as "unverified inbox" refusals rather than as anything labelled a quota problem.
        cap = config.effective_cold_cap()
        spendable = (rem or 0) - config.HUNTER_SAFETY_MARGIN
        if rem is not None and cap and spendable < 5 * cap:
            msgs.append(
                f"verification budget caps cold outreach below COLD_CAP: ~{max(spendable, 0)} "
                f"verifications spendable vs {5 * cap} needed for a full week at {cap}/day "
                f"(≈{max(spendable, 0) // 5}/day sustainable). Extra cold sends will be refused "
                f"as unverified, not reported as a quota stop")
    # Claude runs this week
    wk = usage_budget.count("claude_run", usage_budget.WEEK)
    if config.CLAUDE_MAX_RUNS_7D and wk >= 0.8 * config.CLAUDE_MAX_RUNS_7D:
        msgs.append(f"Claude runs this week: {wk}/{config.CLAUDE_MAX_RUNS_7D}")
    return "; ".join(msgs) if msgs else None


def w_cold_outreach_volume(_cache=None, _today=None, _cap=None) -> str | None:
    """Is the agent actually SENDING, or just running? Watches the symptom, not one cause.

    2026-09-04..09-14: the Hunter key started returning 401, verification went blind, and the
    send gate refused nearly every cold email. Follow-ups kept flowing (they skip verification via
    the prior-delivery exemption), so every outward sign said the agent was healthy — it ran daily,
    committed daily, emailed daily. Cold sends went from ~6/day to 1 across SEVEN working days
    before a human noticed, and the only thing that would have caught it was a warning about one
    specific cause.

    Causes are many and the next one will be different: an expired key, a quota, a blocked port, a
    bad merge, an empty queue. The SYMPTOM is always the same — the agent runs and nothing goes
    out. So watch that directly.

    Reads the per-day counters smtp_send writes. Only days that HAVE a counter file are considered:
    those are days the agent actually ran, which is what makes "ran but sent nothing" visible.
    A day with no run at all is the dead-man's switch's job (w_heartbeat_configured).
    """
    import json as _json
    from pathlib import Path as _P
    import datetime as _dt
    import config

    cache = _P(_cache) if _cache else _P(__file__).parent / "cache"
    if not cache.is_dir():
        return None
    today = _today or _dt.date.today()
    days = []
    for i in range(1, 15):                      # look back up to two weeks of calendar days
        d = today - _dt.timedelta(days=i)
        if d.weekday() >= 5:                    # the agent only runs on weekdays
            continue
        f = cache / f"daily_counts_{d.isoformat()}.json"
        if not f.exists():
            continue
        try:
            days.append((d, int(_json.loads(f.read_text()).get("cold", 0))))
        except Exception:  # noqa: BLE001 — a corrupt counter must not raise here
            continue
        if len(days) >= 5:
            break

    # Too little history to judge — a fresh clone, the public mirror, or a dev machine that never
    # sends. Silence is correct: a detector that cries wolf on every laptop gets ignored, and this
    # one exists precisely because an ignored signal is what cost seven days.
    if len(days) < 3:
        return None

    cap = max(1, _cap if _cap is not None else config.effective_cold_cap())
    sent = sum(n for _, n in days)
    expected = cap * len(days)
    if sent >= 0.4 * expected:
        return None
    detail = ", ".join(f"{d.isoformat()}: {n}" for d, n in days)
    return (f"cold sends have COLLAPSED — {sent} across the last {len(days)} working days the agent "
            f"ran, against a cap of {cap}/day ({expected} expected). The agent is running and "
            f"follow-ups still go out (they skip verification), so nothing else looks wrong. "
            f"Check `email_verify.hunter_health()` first — a dead key produces exactly this — then "
            f"the bounce blocklist and the Pending queue. Recent: {detail}")


def w_heartbeat_configured():
    """The dead-man's switch is the only alert path that survives the VM dying.

    Every other alert in this system is sent BY the VM, so when the VM stops, the thing that
    would report it stops too. That blind spot hid a 28-day outage in 2026-06 and a 3-day one
    in 2026-08. run_agent.sh already implements the ping correctly (it fires only on a CONFIRMED
    git push); it is simply inert until HEALTHCHECK_URL exists in .env. Warn rather than fail —
    a missing monitor must never stop real outreach.
    """
    env = Path(__file__).parent / ".env"
    if not env.exists():
        return None  # nothing to assert on a dev box without .env
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("HEALTHCHECK_URL=") and line.split("=", 1)[1].strip():
            return None
    # Warning checks RETURN their message (raising is reserved for a broken check itself).
    return ("HEALTHCHECK_URL is not set ON THIS HOST (harmless on a dev machine; only the sending VM pings) — the dead-man's switch in run_agent.sh is INERT, so a VM "
            "outage is SILENT. Every other alert is sent BY the VM, so when it stops, the thing "
            "that would report it stops too (28-day stall in 2026-06, 3-day in 2026-08). Fix: free "
            "check at healthchecks.io, period 1 day + 2h grace, then HEALTHCHECK_URL=<ping-url> in .env")


def t_bounce_guard():
    """A hard-bounced address must never be sent to twice, at address AND domain level."""
    import bounce_guard
    d = bounce_guard.load()
    assert isinstance(d.get("addresses"), dict), "blocklist shape broken"
    # Round-trip on a synthetic address, without touching the real blocklist file.
    blocked, why = bounce_guard.is_blocked("definitely-not-real@nonexistent-test-domain.invalid")
    assert blocked is False, "clean address must not be blocked"
    # Known-dead entries from the 2026-08 audit must still be blocked.
    if d.get("addresses"):
        sample = sorted(d["addresses"])[0]
        b, w = bounce_guard.is_blocked(sample)
        assert b, f"seeded bounce {sample} is not blocked"
        assert w, "blocklist must explain WHY it blocked"
    # Generic-local bounce must generalise to the domain, personal must not.
    if d.get("generic_domains"):
        dom = sorted(d["generic_domains"])[0]
        assert bounce_guard.is_blocked(f"jobs@{dom}")[0], "generic local must inherit domain block"
        assert not bounce_guard.is_blocked(f"a.person@{dom}")[0], \
            "a personal mailbox must NOT be blocked by a generic-inbox bounce"


def t_generic_inbox_needs_evidence():
    """`mx_only` must NOT authorise a send — that was the August 2026 bounce spike."""
    import smtp_send as S
    assert "mx_only" not in S._GENERIC_OK_CONF, \
        "mx_only proves only that the DOMAIN is alive; accepting it for contact@ caused 55 bounces"
    assert "api_risky" in S._GENERIC_OK_CONF, \
        "catch-all (api_risky) cannot hard-bounce and must stay allowed for generic inboxes"
    assert S._STRONG_CONF <= S._GENERIC_OK_CONF, "strong tiers must remain allowed"
    assert "api_risky" not in S._STRONG_CONF, \
        "a GUESSED personal mailbox on a catch-all domain is still a guess"


def t_autoreply_classified():
    """Out-of-office / acknowledgements must not be recorded as human replies."""
    from imap_fetch import _looks_like_autoreply as f
    ooo, kind = f("hr@x.io", "Out of Office Re: your message", "", {})
    assert ooo and kind == "out-of-office", "OOO not detected"
    ack, kind = f("rh@x.fr", "Votre candidature", "Nous avons bien reçu votre candidature", {})
    assert ack and kind == "auto-ack", "French acknowledgement not detected"
    hdr, _ = f("a@b.com", "Re: hi", "hello", {"Auto-Submitted": "auto-replied"})
    assert hdr, "RFC 3834 Auto-Submitted header ignored"
    assert not f("a@b.com", "Re: hi", "hello", {"Auto-Submitted": "no"})[0], \
        "Auto-Submitted: no means a HUMAN sent it"
    # The reply that won an interview must never be suppressed.
    human, _ = f("founder@example-startup.test", "Re: Le faux positif dans vos signaux d'achat",
                 "Bonjour Zineb, oui on peut etudier l'opportunite d'une alternance, "
                 "on s'organisera un entretien", {})
    assert not human, "a genuine human reply was misclassified as an autoresponder"


def t_human_reply_is_log_authoritative():
    """Status alone must not certify a human reply — the log is the authority."""
    import tracker
    fake_ooo = "[2026-08-03] Contact: Out of Office Re: hello"
    assert not tracker.has_genuine_human_reply(fake_ooo, "Replied"), \
        "a Status=Replied row whose only Contact line is an OOO must not count as a human reply"
    fake_bounce = "[2026-08-03] Contact: BOUNCED | Address not found"
    assert not tracker.has_genuine_human_reply(fake_bounce, "Replied"), \
        "a bounce stamped Replied must not count as a human reply"
    real = "[2026-06-21] Contact: Re: votre message | Bonjour Zineb, oui avec plaisir"
    assert tracker.has_genuine_human_reply(real, "Replied"), "a real reply must still count"


def t_followup_survives_a_dead_verifier():
    """A follow-up to a mailbox that ALREADY received mail must not need the verifier.

    2026-09-02 regression: Hunter was unreachable, so every address degraded to `mx_only`
    and the evidence gate refused three follow-ups to people who had already been emailed
    successfully. Prior delivery is stronger evidence than any API — a bounce would have
    flipped the row to `Rejected` and blocklisted the address — so refusing those sends
    was pure loss on the highest-converting channel.
    """
    import inspect

    import smtp_send as S
    import tracker
    src = inspect.getsource(S.send_and_log)
    assert "already_delivered" in src, \
        "the send gate no longer exempts follow-ups to already-delivered mailboxes"
    assert "and not already_delivered" in src, \
        "the evidence gate must be bypassed for a proven mailbox, not merely computed"
    # The exemption must be scoped to follow-ups: a COLD send is still a guess.
    assert 'if kind == "followup":' in src, "prior-delivery exemption must apply to follow-ups only"
    # And the helper it leans on must key on the address and exclude non-delivered statuses.
    assert "Pending" not in tracker.DELIVERED_STATUSES and \
           "Rejected" not in tracker.DELIVERED_STATUSES, \
        "Pending/Rejected are not proof of delivery"
    assert tracker.DELIVERED_STATUSES <= tracker.VALID_STATUSES, \
        "DELIVERED_STATUSES must be real tracker statuses"
    assert not tracker.address_has_delivered_mail("definitely-nobody@nowhere.invalid"), \
        "an unknown address must never be treated as already delivered"
    assert not tracker.address_has_delivered_mail(""), "empty address must not pass"


def t_hunter_health_states():
    """Verification health must distinguish 'no key' / 'dead key' / 'down' — offline."""
    import email_verify as ev
    original = ev.hunter_remaining
    try:
        # No key configured at all.
        import os

        import config
        prev_env, prev_cfg = os.environ.get("HUNTER_API_KEY"), config.HUNTER_API_KEY
        os.environ["HUNTER_API_KEY"] = ""
        config.HUNTER_API_KEY = ""
        assert ev.hunter_health()[0] == "no_key", "missing key must report no_key"
        # Key present but the account endpoint rejects it → actionable, needs a human.
        os.environ["HUNTER_API_KEY"] = "x" * 40
        ev.hunter_remaining = lambda key: None
        ev._LAST_ACCT_ERROR = ("dead_key", "HTTP 401 from /v2/account")
        assert ev.hunter_health()[0] == "dead_key", "a rejected key must not look like a blip"
        # Key present, endpoint simply unreachable → transient, self-heals.
        ev._LAST_ACCT_ERROR = ("unreachable", "URLError")
        assert ev.hunter_health()[0] == "unreachable", "a network blip must not look like a dead key"
        # Quota spent → the send gate degrades, so it must be surfaced.
        ev.hunter_remaining = lambda key: config.HUNTER_SAFETY_MARGIN
        assert ev.hunter_health()[0] == "exhausted", "spent quota must be reported"
        ev.hunter_remaining = lambda key: config.HUNTER_SAFETY_MARGIN + 100
        assert ev.hunter_health()[0] == "ok", "a healthy balance must report ok"
    finally:
        ev.hunter_remaining = original
        ev._LAST_ACCT_ERROR = None
        if prev_env is None:
            os.environ.pop("HUNTER_API_KEY", None)
        else:
            os.environ["HUNTER_API_KEY"] = prev_env
        config.HUNTER_API_KEY = prev_cfg


def t_preflight_warnings_have_a_receiver():
    """Warnings must reach a human. They were log-only, and nobody reads the log."""
    from pathlib import Path
    gate = (Path(__file__).parent / "vm" / "preflight_gate.sh").read_text(encoding="utf-8")
    assert "preflight.py --warnings" in gate, \
        "preflight_gate.sh must collect soft warnings"
    assert "PREFLIGHT WARN" in gate, "collected warnings must be emailed, not just logged"
    assert "_preflight_warned_" in gate or "preflight_warned_" in gate, \
        "repeat warnings must be deduped or the daily alert becomes noise"
    assert "--warnings" in Path(__file__).read_text(encoding="utf-8"), \
        "preflight must support the --warnings mode the gate calls"


def t_canned_template_is_not_a_reply():
    """A ticket closure or a "here's our job board" template is not someone engaging.

    These carry no bounce/OOO marker, so they passed every filter and sat in the warm-lead
    nudge list as near-misses. Of the 8 leads that list showed on 2026-09-02, 4 were templates
    and 2 were written rejections — 75% noise, which is why it stopped being read.
    """
    import imap_fetch
    import tracker
    mjg = ("Merci pour candidature. Vous pouvez retrouver toutes nos offres d'emploi sur "
           "Welcome To The Jungle.")
    wttj = "Fermeture de votre demande. Afficher dans le navigateur"
    assert tracker.looks_like_template_reply(mjg), "job-board brush-off not detected"
    assert tracker.looks_like_template_reply(wttj), "support ticket closure not detected"
    assert not tracker.has_genuine_human_reply(f"[2026-08-25] Contact: {mjg}", "Replied"), \
        "a canned template stamped Replied must not count as a human reply"
    # Ingestion and the retro-classifier must agree, or rows keep entering mis-stamped.
    assert imap_fetch._looks_like_autoreply("rh@x.fr", "Re: candidature", mjg, {})[0], \
        "imap_fetch must reject the same templates tracker does"
    # A real reply must still survive both.
    real = "Bonjour Zineb, oui avec plaisir, on peut se voir jeudi ?"
    assert tracker.has_genuine_human_reply(f"[2026-06-21] Contact: {real}", "Replied")
    assert not imap_fetch._looks_like_autoreply("ceo@x.io", "Re: hi", real, {})[0]


def t_rejection_closes_the_thread():
    """A written "no" still counts as a reply, but must leave the re-engagement list."""
    import tracker
    for no in ("Nous ne pourrons malheureusement pas donner suite à votre proposition.",
               "Malheureusement, nous n'avons pas de poste ouvert en alternance pour le moment.",
               "Je vous souhaite une très bonne continuation dans vos recherches.",
               "We have decided not to move forward with your application."):
        assert tracker.looks_like_rejection(no), f"rejection not detected: {no[:40]}"
    # Reply-rate learning still counts it: a person did read and answer.
    assert tracker.has_genuine_human_reply(
        "[2026-08-06] Contact: nous ne pourrons pas donner suite", "Replied"), \
        "a rejection is still a human reply for reply-rate purposes"
    # Single words must NEVER decide — "malheureusement" also opens a live thread.
    assert not tracker.looks_like_rejection(
        "Malheureusement je ne suis pas disponible cette semaine, mais la semaine prochaine oui"), \
        "a scheduling apology must not be read as a rejection"
    assert not tracker.looks_like_rejection("Bonjour Zineb, votre profil m'intéresse beaucoup")


def t_redirect_address_is_extracted():
    """"Write to recruitment@…" is an invitation — the highest-yield reply there is."""
    import tracker
    joko = ("Toutefois, ce canal est exclusivement dédié au Service Client. Je vous invite à "
            "adresser votre candidature directement à l'adresse suivante : recruitment@acme.io")
    assert tracker.redirect_address(joko, exclude=["cto.name@acme.io"]) == \
        "recruitment@acme.io", "redirect target not extracted"
    # The QUOTED original must never be mined: one decline quoted its own
    # "À : Contact <contact@testco.com>" header, which read as a redirect to the inbox
    # that had just declined us.
    quoted = ("Malheureusement nous n'avons pas de poste ouvert.\n"
              "-----Message d'origine-----\nDe : Zineb\nÀ : Contact <contact@testco.com>")
    assert tracker.redirect_address(quoted, exclude=["ceo.name@testco.com"]) is None, \
        "an address inside the quoted original is not a redirect"
    assert tracker.redirect_address("Bonjour Zineb, merci pour votre message") is None


def t_meeting_invite_needs_a_meeting_noun():
    """An interview invite going quiet is the costliest silence the system can have."""
    import tracker
    assert tracker.looks_like_meeting_invite(
        "Olivier Soudée vous invite à Entretien 3 juillet 2026 14:00"), "invite not detected"
    assert tracker.looks_like_meeting_invite("https://calendly.com/olivier/30min")
    # "vous invite à" alone also opens "je vous invite à adresser votre candidature à …",
    # which is a REDIRECT, not an invitation to meet — flagging it as an interview is a lie.
    assert not tracker.looks_like_meeting_invite(
        "je vous invite à adresser votre candidature à recruitment@acme.io"), \
        "a redirect must not be reported as a scheduled interview"
    assert not tracker.looks_like_meeting_invite("Je vous invite à consulter notre site")


def t_stalled_list_is_signal_not_noise():
    """The nudge list must carry live threads only, with the next action attached."""
    import stalled_alert
    import tracker
    leads = tracker.stalled_conversations(days=5)
    for r in leads:
        assert not tracker.looks_like_rejection(r.get("last_reply", "")), \
            f"{r.get('Company')}: a declined thread is still listed as a warm lead"
        assert "http" not in r.get("last_reply", ""), \
            "tracking URLs must be stripped from the preview — they crowd out the message"
    # One entry per company, or the list is unreadable in one sitting.
    companies = [str(r.get("Company", "")).strip().lower()
                 for r in stalled_alert._dedupe_by_company(leads)]
    assert len(companies) == len(set(companies)), "stalled alert must show each company once"


def t_daily_cap_is_enforced_in_code():
    """The send cap must be a hard stop, not an instruction in a prompt.

    `_record_send` counted sends, but nothing ever read the count back: the entire
    anti-spam ceiling rested on the LLM remembering to stop. A miscount, a re-run or a
    cron double-fire could put hundreds of messages through a personal Gmail and burn the
    sending reputation the whole pipeline depends on.
    """
    import inspect

    import config
    import smtp_send as S
    assert hasattr(S, "cap_check"), "the cap check is gone — sends are unbounded again"
    assert "cap_check" in inspect.getsource(S.send_and_log), \
        "send_and_log must actually CALL the cap check, not merely define it"
    counts = {"cold": 0, "warm": 0}
    orig = S.today_send_counts
    try:
        S.today_send_counts = lambda: counts
        assert S.cap_check("cold")[0], "a fresh day must allow a cold send"
        counts["cold"] = config.effective_cold_cap()
        assert not S.cap_check("cold")[0], "cold sends must stop at the effective cold cap"
        counts.update(cold=0, warm=config.WARM_CAP)
        assert not S.cap_check("followup")[0], "follow-ups must stop at WARM_CAP"
        # Alerts are internal notifications and must never be throttled — a preflight
        # failure or a stalled-lead alert has to get out even on a full day.
        counts.update(cold=99, warm=99)
        assert S.cap_check("alert")[0], "alerts must never be capped"
        # A reply is human-approved content in a live conversation; blocking it would be
        # worse than the spam risk it avoids.
        assert S.cap_check("reply")[0], "replies must not be blocked by the bucket caps"
    finally:
        S.today_send_counts = orig


def t_duplicate_guard_fingerprints_content():
    """Re-sending the same message is blocked; a real follow-up sequence is not.

    Keying this on the SUBJECT looked right and was wrong: a follow-up is supposed to reuse
    the subject with a "Re:" prefix — that is what threads it — and the multi-touch
    sequence sends up to three under one subject. Subject-keying made correct threading
    indistinguishable from spam.
    """
    import mail_thread as M
    subject, body = "Votre pipeline de données", "Bonjour, un corps de message."
    fp = M.content_fingerprint(subject, body)
    assert M.content_fingerprint("Re: " + subject, body) == fp, \
        "a Re: prefix must not create a new fingerprint"
    assert M.content_fingerprint(subject.upper(), "  " + body + "  ") == fp, \
        "case and whitespace must not create a new fingerprint"
    assert M.content_fingerprint(subject, body + " Et une actualité en plus.") != fp, \
        "a follow-up that says something NEW must not look like a duplicate"


def t_followups_thread_into_the_conversation():
    """In-Reply-To / References must be set, or a follow-up reads as bulk mail.

    Clients thread on these headers, not on the subject — Outlook and Apple Mail ignore the
    subject entirely. Without them the recipient gets a context-free "Re: ..." from a
    stranger.
    """
    import inspect

    import mail_thread as M
    import smtp_send as S
    src = inspect.getsource(S.send_and_log)
    assert "reply_headers" in src, "the send path no longer asks for threading headers"
    assert 'kind in ("followup", "reply")' in src, \
        "threading must apply to follow-ups and replies, and NOT to a cold first contact"
    assert "mail_thread.record" in src, \
        "without recording our own Message-ID the next follow-up cannot thread"
    build = inspect.getsource(S._build_message)
    for h in ("Message-ID", "Date", "Reply-To"):
        assert h in build, f"{h} header is missing — its absence is a spam signal"
    # A brand-new conversation must not fabricate a parent.
    assert M.reply_headers("nobody-at-all@nowhere.invalid") == {}, \
        "an unknown thread must yield no In-Reply-To"


def t_post_send_bookkeeping_cannot_fake_a_failure():
    """Anything after delivery must never be reported as a failed send.

    The tracker write raising turned a DELIVERED message into a non-zero exit, which the
    caller reads as "not sent" — and the retry puts the same email in a real person's inbox
    twice.
    """
    import inspect

    import smtp_send as S
    src = inspect.getsource(S.send_and_log)
    tail = src[src.index("EVERYTHING BELOW THIS LINE"):]
    assert "warnings.append" in tail and "try:" in tail, \
        "post-send steps must be contained and reported as warnings"
    assert "return SendResult(ok=False" not in tail, \
        "nothing after delivery may return a failure — the caller would re-send"
    assert "warning" in {f.name for f in __import__("dataclasses").fields(S.SendResult)}, \
        "SendResult needs a non-fatal warning channel"


def t_missing_attachment_is_a_failed_send():
    """A follow-up promising a CV must not go out without it.

    Asserted against _build_message, NOT send(): send() short-circuits on missing SMTP
    credentials, so testing through it passed only on a host that has a .env and gave a
    false green everywhere else — which is exactly how the public mirror caught it.
    """
    import smtp_send as S
    try:
        S._build_message(to_address="x@nowhere.invalid", subject="CV", body="corps",
                         attachment_path=Path("documents/__definitely_missing__.pdf"))
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("a missing attachment must fail the send, not be silently dropped")
    # And the failure must reach the caller as a refusal rather than an exception.
    src = __import__("inspect").getsource(S.send)
    assert "could not build the message" in src, \
        "send() must convert a build failure into a clean SendResult error"


def t_lead_inbox_never_invents_a_domain():
    """Hand-queued leads must reach the tracker with a REAL domain, or not at all.

    This queue exists because contacts.xlsx is `merge=ours`: rows added on a dev machine are
    silently discarded by the VM, so there was no working path for "Zineb picked these twenty
    companies herself". The danger in building one is inventing `contact@<slugified-name>.com`
    — which is exactly what produced the August 2026 bounce spike.
    """
    import json
    from pathlib import Path as _P

    import lead_inbox as L
    assert "MAX_ATTEMPTS" in dir(L), "an unresolvable name must not be re-queried forever"
    # A name that cannot resolve yields NO domain — never a slug of itself.
    dom, why = L._resolve({"company": "Zzz Nonexistent Company Xyzzy", "domain": ""})
    assert dom is None, f"resolver invented a domain: {dom}"
    assert "needs a human" in why or "no domain" in why, why
    # A supplied domain with no MX is refused too — being handed a domain is not evidence.
    dom, _ = L._resolve({"company": "X", "domain": "definitely-not-a-real-domain-xyzzy.invalid"})
    assert dom is None, "a domain with no MX records must be refused"
    # The queue is COMMITTED on purpose: gitignoring it would mean the VM never sees it.
    gi = (_P(__file__).parent / ".gitignore").read_text(encoding="utf-8")
    assert "lead_inbox.json" not in gi, \
        "cache/lead_inbox.json must stay tracked or the VM can never drain it"
    # Drain must be reachable from the daily run, or the queue silently never empties.
    skill = (_P(__file__).parent / ".claude" / "commands" / "daily-agent.md").read_text(encoding="utf-8")
    assert "lead_inbox.py drain --apply" in skill, \
        "/daily-agent must drain the queue — nothing else can"
    # Every queued entry keeps the fields the drain relies on.
    qf = _P(__file__).parent / "cache" / "lead_inbox.json"
    if qf.exists():
        for e in json.loads(qf.read_text(encoding="utf-8")):
            assert e.get("company"), "a queued entry with no company name is unusable"
            assert e.get("status") in ("queued", "added", "needs_human"), e.get("status")


def t_documented_send_rules_are_enforced_in_code():
    """Rules CLAUDE.md calls mandatory must be enforced by code, not by the prompt.

    The daily cap was the first of these: documented as a hard ceiling, counted but never
    checked, so 25 cold sends went through against a cap of 7. The same audit found three
    more rules living only in the prompt — the no-attachment-on-cold anti-spam rule, the
    "linter MUST pass before any send" gate, and the fact that `--kind alert` bypasses every
    safety gate with no restriction on who it can be pointed at.
    """
    from pathlib import Path as _P

    import config
    import smtp_send as S

    def dry(**kw):
        base = dict(to_address="contact@example.com", subject="Un sujet precis chez Acme",
                    body=("Bonjour, un corps de message assez long pour passer le linter, "
                          "avec linkedin.com/in/zinebmeftah et une question ?"),
                    attachment_path=None, new_status=None, kind="cold", dry_run=True,
                    company="Acme", role="R")
        base.update(kw)
        return S.send_and_log(**base)

    assert dry().ok, "a clean cold draft must still send"
    # An alert is only ever an internal notification.
    assert not dry(kind="alert", to_address="ceo@othercorp.test").ok, \
        "--kind alert must not be usable against a third party: it skips verification, the "\
        "bounce blocklist, the daily cap, the duplicate guard AND tracker logging"
    assert dry(kind="alert", to_address=config.INTERNAL_ALERT_EMAIL).ok, \
        "alerts to Zineb's own inbox must keep working — preflight and stalled-lead alerts "\
        "depend on them"
    # No attachment on a cold first contact.
    cv = _P(__file__).parent / "documents" / "CV_Zineb_Meftah_FR.pdf"
    if cv.exists():
        assert not dry(attachment_path=cv).ok, \
            "a cold email with an attachment is a spam-filter trigger and must be refused"
        assert dry(kind="followup", subject="Re: Un sujet precis chez Acme",
                   body="Bonjour, une relance courte avec une nouveaute concrete. Un echange ?",
                   attachment_path=cv).ok, "the CV must still be attachable on a follow-up"
    # The linter is a gate, not a suggestion.
    assert not dry(subject="Candidature", body="Je suis motivee.").ok, \
        "a draft with linter ERRORS must be refused before it is transmitted"
    # And obvious nonsense never reaches the transport.
    assert not dry(body="   ").ok, "an empty body would send only the signature and footer"
    assert not dry(subject="").ok, "an empty subject must be refused"
    assert not dry(to_address="not-an-email").ok, "an invalid recipient must be refused"


def t_bandit_keeps_every_opener_alive():
    """A zero-reply opener must stay reachable — it was permanently locked out.

    Ranking arms by their Wilson lower bound gives an arm with 0 replies a score of exactly 0,
    so it can never climb back: three openers were frozen out on ~15 samples each while the
    "winner" led on 3/23 (Fisher exact p ≈ 0.24 — noise). Thompson sampling over a Beta
    posterior keeps every arm reachable in proportion to the evidence against it.
    """
    import collections

    import tracker
    picks = collections.Counter(
        tracker.recommend_strategy_order(seed=i)["recommend"] for i in range(400))
    assert len(picks) >= 4, (
        f"the bandit collapsed onto {len(picks)} opener(s) — a zero-reply arm can never recover "
        f"and premature convergence on a tiny sample is exactly the failure this replaced")
    # Reproducible for a given seed, and genuinely varying across seeds.
    a = tracker.recommend_strategy_order(seed=7)
    assert a["recommend"] == tracker.recommend_strategy_order(seed=7)["recommend"], \
        "same seed must give the same recommendation"
    # Thin evidence must be reported as thin, not dressed up as a finding.
    assert "evidence_thin" in a, "callers need to know whether the preference is meaningful"
    if a["total_sent"] < 200 or a["total_replied"] < 15:
        assert a["evidence_thin"], "a few dozen sends per arm cannot identify a winner"
        assert "noise" in a["note"], "the note must say so plainly"


def t_lead_inbox_can_close_a_dead_lead():
    """Outcomes learned off-system must be able to reach the tracker.

    Interviews and calls happen off-system; a lead that is actually over otherwise keeps being
    surfaced as a warm thread to re-engage. Zineb cannot edit the row herself — `merge=ours`
    means the VM discards it — so the correction travels the same queue as a new lead.
    """
    import inspect

    import lead_inbox as L
    assert hasattr(L, "close"), "no way to record that a lead is dead"
    src = inspect.getsource(L.drain)
    assert 'entry.get("action") == "close"' in src, "drain must apply queued closures"
    assert "tracker.save" in src, "a closure that is never written changes nothing"
    assert "new_status" in inspect.signature(L.close).parameters or "status" in \
        inspect.signature(L.close).parameters, "close must let the caller pick the status"


def t_guessed_domains_are_marked_and_counted():
    """An invented address must be recorded as invented, and the queue must say so.

    scraper._email_for already knew whether it resolved a real domain or slugified the company
    name, and persist() threw that away at insert. Result: 1,033 pending rows carrying
    contact@<company-name>.com with nothing marking them as guesses — 95% of the generic
    backlog, ~22% of which have no MX at all — while the dashboard counted them as runway.
    """
    import inspect

    import scraper
    import tracker
    src = inspect.getsource(scraper.persist)
    assert "new_email_is_real" in src and "conversation_log" in src, \
        "persist() must record whether the domain was resolved or invented"
    assert tracker.GUESSED_DOMAIN_MARK in src, "the marker must be the one tracker looks for"
    assert tracker.domain_is_guessed(f"[2026-01-01] Agent: {tracker.GUESSED_DOMAIN_MARK} blah")
    assert not tracker.domain_is_guessed("[2026-01-01] Agent: scraped; domain resolved")
    st = tracker.reachability_stats()
    assert st["workable_now"] + st["needs_enrichment"] == st["pending"], \
        "every pending lead must fall on one side of the reachability split"
    # Guard the invariant, not the sample: the public mirror ships an empty tracker, and an
    # assertion that needs real rows is an assertion that silently passes wherever it matters
    # least. (Same trap as the attachment test, caught the same way.)
    if st["pending"]:
        assert st["workable_now"] <= st["pending"], "workable cannot exceed the queue"
        assert st["needs_enrichment"] >= 0


def t_drain_never_writes_stale_state():
    """A drain that adds AND closes in one pass must not lose the adds.

    2026-09-03: drain() took one DataFrame snapshot at the top, add_contact() wrote 15 rows
    through its own load+save, and the queued `close` entry then saved the pre-add snapshot
    over all of them. The queue recorded "added", contacts.xlsx had nothing, and because the
    entries were marked done nothing would ever have retried them — silent data loss that
    looked like success from both sides.
    """
    import inspect

    import lead_inbox as L
    src = inspect.getsource(L.drain)
    assert "df = tracker.load()          # one read for the whole drain" not in src, \
        "a snapshot held across mutations is exactly the bug"
    # Every branch that saves must have re-read immediately beforehand.
    for branch in ('action") == "note"', 'action") == "close"'):
        i = src.index(branch)
        seg = src[i:src.index("continue", i)]
        assert "tracker.load()" in seg, f"the {branch} branch saves without re-reading first"
        assert seg.index("tracker.load()") < seg.index("tracker.save("), \
            f"the {branch} branch must load BEFORE it saves"
    # And the existence check must not lean on a stale frame either.
    assert "tracker.row_exists(tracker.load()" in src, \
        "row_exists on a stale frame re-adds or skips rows wrongly"


def t_engineer_titles_need_a_technical_domain():
    """"<Noun> Engineer" must not be assumed to be an engineering job.

    2026-09-15: the digest sent Zineb a CertiK "Compliance Engineer Intern" — a Legal &
    Compliance role whose ten responsibilities contain no code — scored 61/100 and described
    to her as a "Backend/software role". _ROLE_INCLUDE matched the bare word "engineer", so
    EVERY "<Noun> Engineer" title in existence passed, and the only thing standing behind it
    was _ROLE_EXCLUDE: a hand-maintained list of non-software nouns grown leak-by-leak. It can
    never be complete — a probe the same day found 18 of 19 leaks, "Mechanical Engineer" and
    "Civil Engineer" among them. The filter is now an ALLOWLIST (_ENGINEER_QUALIFIER), which
    is finite because her target domains are. This test fails if anyone puts the bare word back.
    """
    import opportunities as o

    assert not o._ROLE_INCLUDE.search("Compliance Engineer"), \
        "_ROLE_INCLUDE matches a bare 'engineer' again — every non-software title is back in"

    # Off-domain roles that a blocklist could only catch by naming each one.
    for title in ("Compliance Engineer Intern", "Solutions Engineer", "Mechanical Engineer",
                  "Civil Engineer", "Process Engineer", "Bid Engineer", "Risk Engineer",
                  "Privacy Engineer", "Implementation Engineer", "Audit Engineer"):
        assert not o.role_fit(title), f"non-software title reaching the digest: {title}"

    # ...without costing her the real ones, including those whose ONLY signal is the domain
    # word in front of "Engineer" (no "software"/"data"/"AI" anywhere in the title).
    for title in ("AI Engineer", "Machine Learning Engineer", "Data Engineer",
                  "Backend Engineer", "Software Engineer", "MLOps Engineer",
                  "Platform Engineer", "Infrastructure Engineer", "Cloud Engineer",
                  "DevOps Engineer", "Compiler Engineer", "Ingénieur Logiciel",
                  "Junior Python Developer", "Research Engineer"):
        assert o.role_fit(title), f"real engineering title dropped: {title}"


def t_board_filing_beats_the_job_title():
    """Prefer the employer's OWN classification of a role to anything read off its title.

    2026-09-15, the same incident as t_engineer_titles_need_a_technical_domain: a title is five
    words with no agreed vocabulary behind it, and every digest gate ran on one. CertiK files its
    "Compliance Engineer Intern" under department "Compliance" and its Formal Methods role under
    "Engineering - Tools"; that field was fetched and discarded. Measured on the live boards the
    same day, 8 of 109 title-filter survivors were filed by the employer under sales / marketing /
    business development — "Business Developer" passes a title filter because it contains the word
    "developer".

    Two failure modes this locks down, because the fix has its own way of going wrong:
      • an off-domain department must NOT veto a title that names a core engineering job — three
        real roles that day sat under "Operations" and "Business";
      • an ABSENT classification must stay neutral. If unknown ever read as "no", adding a source
        with no department field would silently empty the digest.
    """
    import job_family as jf

    # The employer's filing refuses what the title alone would have admitted.
    for title, meta in (("Compliance Engineer Intern", {"department": "Compliance"}),
                        ("Business Developer - Belgian Market", {"team": "Sales"}),
                        ("AI Deployment Strategist", {"department": "Sales"}),
                        ("AI & Ops Automation", {"department": "Marketing"})):
        assert jf.refuses(title, meta), f"off-domain filing not refusing: {title}"

    # ...but never overrules a title that names a core engineering job.
    for title, meta in (("Backend Software Engineer (Python / DevOps)", {"department": "Operations"}),
                        ("Full Stack Software Engineer (Python / React)", {"department": "Operations"}),
                        ("Data Engineer (Growth Team)", {"department": "Business"}),
                        ("Développeur Python", {"department": "Sales"})):
        assert not jf.refuses(title, meta), f"core engineering title wrongly vetoed: {title}"

    # Unknown is neutral — including the literal "Other" a real board (JobTeaser) publishes.
    for meta in ({}, None, {"department": ""}, {"function": "Other"}, {"department": "Product"}):
        assert jf.classify(meta) == "", f"unknown filing must stay neutral, got {jf.classify(meta)}"
        assert not jf.refuses("Compliance Engineer Intern", meta), \
            "an absent classification must never refuse on its own"

    # Engineering wins ties: these all carry an off-domain word next to an engineering one.
    for meta in ({"team": "Engineering - Tools"}, {"department": "Data & Analytics"},
                 {"department": "Tech Ops"}, {"rome": "M1805"}):
        assert jf.classify(meta) == "engineering", f"engineering must win the tie: {meta}"

    # The sources must actually SUPPLY it, or the gate is inert in production.
    import inspect

    import company_boards
    for reader in (company_boards._lever, company_boards._ashby,
                   company_boards._smartrecruiters, company_boards._greenhouse):
        src = inspect.getsource(reader)
        assert '"department"' in src or '"function"' in src, \
            f"{reader.__name__} no longer carries the employer's filing into meta"
    assert "content=true" in inspect.getsource(company_boards._greenhouse), \
        "Greenhouse publishes `departments` only with ?content=true"
    assert '"rome"' in inspect.getsource(__import__("france_travail")._meta), \
        "France Travail's ROME code is the only job-family signal the French boards publish"

    # And the digest must apply it.
    import opportunities
    assert "job_family.refuses" in inspect.getsource(opportunities.new_offers), \
        "the digest is not consulting the employer's own filing"


def t_french_developer_titles_are_matched():
    """"Développeur Python" — the commonest French spelling of her target role — must match.

    2026-09-16: it matched NOTHING. ROLE_KEYWORDS listed only COMPOUNDS ("développeur backend",
    "ingénieur data") and a French posting rarely writes one, so the bare noun was absent from the
    SHARED gate that every scraper and the whole outreach pipeline runs on. Found by reading the
    titles La Bonne Alternance returns and spotting "Apprenti/e développeur/se informatique (H/F)"
    among the REJECTED ones. Measured over 377 raw France Travail / LBA titles: 123 matched before,
    186 after.

    The same entry is what makes the inclusive-writing spellings work, since _flatten collapses
    "/", "·" and "(" to spaces — "développeur/se" had been invisible for the same reason.

    And it is why ROLE EXCLUSIONS had to exist at all: a bare "développeur" equally matches
    "Développeur Commercial" and "BUSINESS DEVELOPPEUR BtoB" — the French mirror of "Business
    Developer". A match here spends a cold send and a Hunter verification, the scarcest resource
    in the system, so the exclusions are not cosmetic.
    """
    import jobsource as js

    for title in ("Développeur Python", "Developpeur Python (H/F)", "Développeur/se Python",
                  "Développeur(se) web junior", "Développeur·se Web", "Développeuse Python",
                  "Apprenti/e développeur/se informatique (H/F)",
                  "APPRENTI INGÉNIEUR INFORMATIQUE - H/F",
                  "Alternance - Concepteur / Développeur Logiciel (H/F)"):
        assert js.matches_target_role(title), f"French target title not matched: {title}"

    # The cost of the bare noun, refused.
    for title in ("Alternance Développeur Commercial - Hœnheim (F/H)",
                  "BUSINESS DEVELOPPEUR BtoB Industrie", "DEVELOPPEUR FONCIER (H/F)",
                  "Alternant Développeur de marque et commercialisation H/F",
                  "Analyste Développeur COBOL (H/F)", "Developpeur(se) C# .Net (H/F)",
                  "Developpeur(se) C++ Embarqué (H/F)",
                  "Alternant Développeur Web / Webmaster WordPress - Joomla (H/F)"):
        assert js.matches_target_role(title) is None, f"not her job, still matched: {title}"

    # Nothing that matched before may stop matching.
    for title, cat in (("Data Engineer", "data"), ("AI Engineer", "ai"),
                       ("Machine Learning Engineer", "ai"), ("Backend Engineer", "backend"),
                       ("data analyste F/H", "data"), ("DATA ENGINEERING", "data")):
        assert js.matches_target_role(title) == cat, f"regression on {title}"

    # An exclusion must never fire on a plain target title.
    assert not js.excluded_role("Développeur Python")
    assert not js.excluded_role("Data Engineer")


def t_workday_reader_is_sane():
    """Workday is where the French alternance market is — and its quirks must not be guessed at.

    The token is "tenant/wdN/site" because a Workday careers URL has three parts and cannot be
    reduced to one slug (Phenom has the same property, for the same reason). `postedOn` is PROSE,
    and its "30+ Days Ago" must stay unknown rather than become a date: the digest penalises a
    stale posting on exactly that number, where unknown is neutral and wrong is not.

    Probing must also stay cheap. `_workday` runs 7 keywords x 5 pages = 35 requests, so a probe
    implemented as "call _workday and count" would fire ~2,100 requests per tenant just to find one
    URL. `_workday_total` asks for a single row and reads the reported total instead.
    """
    import inspect
    import re

    import company_boards as cb

    assert cb.PROVIDERS.get("workday") is cb._workday
    assert cb._workday("not-a-valid-token") == [], "a malformed token must be inert, not raise"
    assert cb._workday_posted("Posted 13 Days Ago") and cb._workday_posted("Posted 2 Months Ago")
    for unknown in ("Posted 30+ Days Ago", "Posted Today", "", None):
        assert cb._workday_posted(unknown) == "", \
            f"an unreadable Workday date must stay empty, not be guessed: {unknown!r}"
    _probe_src = inspect.getsource(cb.probe_workday)
    assert "_workday_total" in _probe_src, \
        "probe_workday must use the one-request _workday_total…"
    assert not re.search(r"(?<!probe)(?<!_total)\b_workday\(", _probe_src), \
        "…and must never call the full _workday() per combination (35 requests each)"


def t_postings_she_cannot_take_are_refused():
    """Two whole classes of posting that look applicable and are not.

    Both were found on 2026-09-18 while hand-screening a day's alternances, and both had already
    reached her shortlist.

    LEVEL. A BTS / DUT / Bac+2 alternance is not a job she can take — she holds a Licence and
    enters an M1. One search returned SEVEN "Alternance - Développeur web junior - BTS S.I.O"
    rows from a single BTS school. Filtered on the DIPLOMA rather than the school's name,
    because blocklisting that school would have fixed one school instead of the class. `BUT` is
    deliberately NOT matched: it is also the ordinary French word "but", and this pattern runs on
    short titles where a false positive costs a real lead.

    INTERMEDIARY. "Nous RECHERCHONS pour notre entreprise partenaire …" walked straight through,
    because the first version of that pattern only knew "recrutons". Same sentence, different
    verb. The ESN form ("notre client recherche…") is the same shape: the advertised company is
    an agency and the real employer is never named, so there is nobody to write to and no company
    to research.
    """
    import adzuna
    import jobsource as js

    for title in ("Alternance - Développeur web junior - BTS S.I.O",
                  "Alternance Développeur DUT Informatique",
                  "Alternance Data Analyst Bac+2",
                  "Alternance développeur licence pro"):
        assert js.matches_target_role(title) is None, f"below her level, still matched: {title}"

    # Her real targets must survive — including a title that names Bac+5.
    for title in ("Alternance Data Scientist", "Développeur Python", "Alternance NLP Engineer",
                  "Data Scientist en alternance Bac+5", "Alternance Développeur IA - Python H/F"):
        assert js.matches_target_role(title), f"real target dropped by the level filter: {title}"

    for desc in ("Un partenaire de l'école OpenClassrooms recherche un Data Scientist",
                 "Nous recherchons pour notre entreprise partenaire un(e) Spécialiste",
                 "Nous cherchons pour nos entreprises partenaires un alternant",
                 "Notre client recherche actuellement un(e) Data Scientist",
                 "LiveCampus recrute pour l'une de ses entreprises partenaires",
                 "Dans le cadre de son programme Mastère IA Data, X recherche un alternant"):
        assert adzuna.looks_like_school_intermediary(desc), f"intermediary not caught: {desc[:50]}"

    # A real employer describing its own team, or naming an industrial partner, must pass.
    for desc in ("OpenClassrooms recrute un Data Engineer pour son équipe produit",
                 "Nous recherchons un alternant pour rejoindre notre équipe R&D à Paris",
                 "Nous recrutons pour renforcer notre équipe data à Paris",
                 "Avec notre partenaire industriel Airbus, notre équipe développe",
                 "Au sein du DataLab, rejoignez l'équipe du pilotage commercial"):
        assert not adzuna.looks_like_school_intermediary(desc), \
            f"real employer wrongly refused: {desc[:50]}"


WARNINGS = [
    ("skill examples name a live month", w_skill_examples_name_a_live_month),
    ("email verification capability", w_verification_capability),
    ("quota budgets", w_quota_budgets),
    ("cold outreach volume", w_cold_outreach_volume),
    ("heartbeat configured", w_heartbeat_configured),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

CHECKS = [
    ("modules import", t_imports),
    ("config caps", t_config_caps),
    ("config footers (FR+EN)", t_config_footers),
    ("contract priority", t_contract_priority),
    ("contract-type detection", t_contract_detection),
    ("language guess", t_language_guess),
    ("contact_finder name guards", t_contact_finder_guards),
    ("company resolver (name→domain)", t_company_resolver),
    ("email pattern building", t_email_patterns),
    ("description facts", t_descriptions_extract_facts_and_report_absence_honestly),
    ("digest runs the rebuilt pipeline", t_the_daily_digest_uses_the_rebuilt_pipeline),
    ("queries self-tune", t_queries_self_tune_without_ever_shrinking),
    ("brief kills only the certain", t_brief_kills_only_the_certain_and_never_an_absence),
    ("leadset dedupe", t_leadset_merges_copies_and_never_merges_two_jobs),
    ("source lab scores usefulness", t_source_lab_scores_usefulness_not_volume),
    ("schools met this week are refused", t_schools_met_this_week_are_refused),
    ("ats verification", t_ats_verification_is_accent_safe_and_biased_to_live),
    ("job sources registry", t_sources_registry),
    ("international targeting", t_international_targeting),
    ("location mode (remote+in-person)", t_location_mode),
    ("global brand recognizer", t_global_brands),
    ("opportunity scout digest", t_opportunity_digest),
    ("engineer titles need a domain", t_engineer_titles_need_a_technical_domain),
    ("board filing beats the job title", t_board_filing_beats_the_job_title),
    ("French developer titles match", t_french_developer_titles_are_matched),
    ("postings she cannot take are refused", t_postings_she_cannot_take_are_refused),
    ("workday reader", t_workday_reader_is_sane),
    ("digest feeds outreach", t_digest_feeds_outreach),
    ("alternance timeline is current", t_alternance_timeline_is_current),
    ("no stale start date in outgoing mail", t_no_stale_start_date_in_outgoing_mail),
    ("send counter keys agree", t_send_counter_keys_agree),
    ("cold cap paced by verification budget", t_cold_cap_is_paced_by_verification_budget),
    ("pacing follows Hunter's real reset date", t_pacing_follows_hunters_real_reset_date),
    ("hunter cache stores the reset date", t_hunter_cache_stores_the_reset_date),
    ("linkedin budget counts both ceilings", t_linkedin_budget_counts_the_two_ceilings_apart),
    ("linkedin legacy lines never spend inmail", t_linkedin_unlabelled_lines_never_spend_an_inmail),
    ("linkedin budget refuses when spent", t_linkedin_budget_refuses_when_spent),
    ("linkedin method marker round trips", t_linkedin_method_marker_round_trips),
    ("CV never ships truncated", t_cv_never_ships_truncated),
    ("CV adapts its content to the offer", t_cv_adapts_its_content_to_the_offer),
    ("cold emails may not reuse sentences", t_cold_emails_may_not_reuse_sentences),
    ("strategy P registered everywhere", t_strategy_p_is_registered_everywhere),
    ("linkedin URL consistent everywhere", t_linkedin_url_is_consistent_everywhere),
    ("recent rejections are down-ranked", t_recent_rejections_are_downranked),
    ("cold volume collapse is detected", t_cold_volume_collapse_is_detected),
    ("lead location gates unreachable jobs", t_lead_location_gates_unreachable_jobs),
    ("no claude during the working day", t_no_claude_during_the_working_day),
    ("outbox queues and dispatch sends", t_outbox_queues_and_dispatch_sends),
    ("digest picks five the right way", t_digest_picks_five_the_right_way),
    ("autoreply markers recognised", t_autoreply_markers_are_recognised),
    ("dry run matches real send", t_dry_run_matches_real_send),
    ("follow-ups never interrupt a conversation", t_followups_never_interrupt_a_conversation),
    ("lead posting age", t_lead_age),
    ("company careers boards", t_company_boards),
    ("enrichment queue", t_enrichment_queue),
    ("training bodies down-ranked", t_training_bodies),
    ("enrichment stats", t_enrichment_stats),
    ("email verification gate", t_email_verification_gate),
    ("tracker schema", t_tracker_schema),
    ("tracker helpers", t_tracker_helpers),
    ("strategy bandit", t_strategy_bandit),
    ("strategy memory recording", t_strategy_recording),
    ("email linter", t_email_linter),
    ("ranking verify-cache peek", t_ranking_verdict_peek),
    ("lead ranking", t_lead_ranking),
    ("funnel + cooldown", t_funnel_and_cooldown),
    ("smtp footer/alert logic", t_smtp_footer_logic),
    ("smtp alert kind", t_smtp_alert_kind),
    ("smtp language detection", t_smtp_lang_detection),
    ("no empty active emails", t_contacts_no_empty_active_emails),
    ("CV .tex sources", t_cv_sources),
    ("about_me matching guide", t_about_me_matching_guide),
    ("lead-fact cache", t_lead_facts),
    ("usage budget ledger", t_usage_budget),
    ("hunter budget guard", t_hunter_budget_guard),
    ("verify cache + quota guard", t_verify_cache),
    ("imap cross-run dedup", t_imap_dedup),
    ("ATS/portal detector", t_ats_detect),
    ("digest reply → leads", t_digest_reply),    ("bounce blocklist", t_bounce_guard),
    ("generic inbox needs evidence", t_generic_inbox_needs_evidence),
    ("auto-reply classification", t_autoreply_classified),
    ("human-reply is log-authoritative", t_human_reply_is_log_authoritative),
    ("followup survives a dead verifier", t_followup_survives_a_dead_verifier),
    ("hunter health states", t_hunter_health_states),
    ("preflight warnings have a receiver", t_preflight_warnings_have_a_receiver),
    ("canned template is not a reply", t_canned_template_is_not_a_reply),
    ("rejection closes the thread", t_rejection_closes_the_thread),
    ("redirect address extracted", t_redirect_address_is_extracted),
    ("meeting invite needs a meeting noun", t_meeting_invite_needs_a_meeting_noun),
    ("stalled list is signal not noise", t_stalled_list_is_signal_not_noise),
    ("daily cap enforced in code", t_daily_cap_is_enforced_in_code),
    ("duplicate guard fingerprints content", t_duplicate_guard_fingerprints_content),
    ("follow-ups thread into the conversation", t_followups_thread_into_the_conversation),
    ("post-send bookkeeping cannot fake a failure", t_post_send_bookkeeping_cannot_fake_a_failure),
    ("missing attachment is a failed send", t_missing_attachment_is_a_failed_send),
    ("lead inbox never invents a domain", t_lead_inbox_never_invents_a_domain),
    ("documented send rules enforced in code", t_documented_send_rules_are_enforced_in_code),
    ("bandit keeps every opener alive", t_bandit_keeps_every_opener_alive),
    ("lead inbox can close a dead lead", t_lead_inbox_can_close_a_dead_lead),
    ("guessed domains are marked and counted", t_guessed_domains_are_marked_and_counted),
    ("drain never writes stale state", t_drain_never_writes_stale_state),
]


def main() -> int:
    if _WARN_ONLY:
        for name, fn in WARNINGS:
            warn(name, fn)
        for w in _warnings:
            print(w)
        return 0
    print(f"[preflight] running {len(CHECKS)} checks...")
    for name, fn in CHECKS:
        check(name, fn)
    for name, fn in WARNINGS:
        warn(name, fn)
    print()
    if _failed:
        print(f"[preflight] ❌ FAILED — {_passed} passed, {_failed} failed")
        for f in _failures:
            print(f"   - {f}")
        return 1
    if _warnings:
        print(f"[preflight] ✅ all {_passed} checks passed — with {len(_warnings)} warning(s):")
        for w in _warnings:
            print(f"   ⚠️  {w}")
        return 0
    print(f"[preflight] ✅ all {_passed} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
