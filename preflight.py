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


def t_sources_registry():
    """Every job source is wired consistently behind the /scrape skill (skill-orchestrated)."""
    import scraper
    expected = {"stationf", "wttj", "hellowork", "apec", "francetravail", "freework",
                "labonnealternance", "remotive"}
    assert set(scraper.SOURCES) == expected, set(scraper.SOURCES)
    for name, src in scraper.SOURCES.items():
        assert callable(src.get("discover")), f"{name}: discover not callable"
        assert callable(src.get("resolve")), f"{name}: resolve not callable"
        assert "enrich" in src, f"{name}: missing enrich flag"
    import apec, france_travail, free_work, hellowork, labonnealternance, wttj, remotive
    for m in (wttj, hellowork, apec, free_work, france_travail, labonnealternance, remotive):
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
    assert not opp.role_fit("Intern AI & Management Consulting")
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
    # La Bonne Alternance (the state alternance API) must be one of the digest's French sources,
    # and its hidden-market recruiter rows must never reach her — they have no posting to apply to.
    import inspect as _i
    _src = _i.getsource(opp._fetch_france_inperson)
    assert "labonnealternance" in _src and "[Suggested]" in _src
    # both French boards must ask for alternance explicitly, not only contract-agnostic keywords
    import apec as _ap, france_travail as _ft
    for _m in (_ap, _ft):
        assert set(_m.ALTERNANCE_QUERIES) == {"ai", "backend", "data"}
        assert all("alternance" in q.lower() for q in _m.ALTERNANCE_QUERIES.values())
        assert len(_m._query_plan()) == len(_m.QUERIES) + len(_m.ALTERNANCE_QUERIES)
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
        assert len(_sel) == opp._DIGEST_CAP, len(_sel)
        assert _by.get("relocate", 0) >= opp._SECTION_MIN["relocate"]   # floor honoured...
        assert _by.get("france", 0) > opp._SECTION_MIN["france"]        # ...and spare slots reused
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
        assert "No new" in opp.format_digest([])
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
    d = o.format_digest(sample)
    assert "REMOTE" in d and "IN-PERSON" in d and "ABROAD" in d, "digest must group by section"


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
            "GE HealthCare. Mes projets : linkedin.com/in/zineb-meftah. Un échange de 10 minutes ?")
    errs, _ = lint(good, subject="Reranker chez Acme — alternance M1", kind="cold", company="Acme")
    assert errs == [], f"clean cold email should pass, got: {errs}"
    # A bad cold email is blocked (banned opener + no LinkedIn + footer in draft)
    bad = ("Je suis Zineb Meftah et je me permets de vous contacter.\n\n"
           "P.S. Ce message a été entièrement rédigé par un agent.\nZineb Meftah")
    errs2, _ = lint(bad, subject="Candidature alternance", kind="cold", company="Acme")
    assert len(errs2) >= 3, f"bad cold email should raise several errors, got: {errs2}"
    # Word-limit enforced — cold is medium (~150–180), so the cap is 180, not the old 110
    long_body = "linkedin.com/in/zineb-meftah " + "mot " * 200
    errs3, _ = lint(long_body, subject="Specific hook about Acme product", kind="cold", company="Acme")
    assert any("word" in e for e in errs3), "over-limit cold email must error on word count"
    # A 130-word cold email is now WITHIN the medium band → no word-count error
    mid_body = "Votre reranker chez Acme. linkedin.com/in/zineb-meftah ? " + "mot " * 120
    errs3b, _ = lint(mid_body, subject="Reranker chez Acme — alternance", kind="cold", company="Acme")
    assert not any("word" in e for e in errs3b), f"130-word medium cold email must NOT error: {errs3b}"
    # A too-thin cold email WARNS (soft — Strategy U is the exception, so it must not be an error)
    thin = "Votre reranker chez Acme me parle. linkedin.com/in/zineb-meftah ? Un échange ?"
    et, wt = lint(thin, subject="Reranker chez Acme — alternance", kind="cold", company="Acme")
    assert any("thin" in x.lower() for x in wt), f"thin cold email should warn: {wt}"
    assert not any("thin" in e.lower() for e in et), "thin is a warning, never a blocking error"
    # Content-quality WARNINGS: generic flattery, first-line-about-Zineb, missing CTA
    weak = "Je suis passionnée par votre entreprise. linkedin.com/in/zineb-meftah."
    _, warns = lint(weak, subject="Specific hook about Acme", kind="cold", company="Acme")
    wj = " ".join(warns).lower()
    assert "cliché" in wj or "generic" in wj, f"should warn on flattery: {warns}"
    assert "first sentence" in wj, f"should warn first-line-about-Zineb: {warns}"
    assert "cta" in wj or "question" in wj, f"should warn on missing CTA: {warns}"
    # Structure & readability: run-on sentence, one-block wall, crammed links all warn
    runon = ("Votre choix de reranking pour le triage des tickets, c'est exactement l'approche "
             "que j'aurais prise et que j'ai mise en production chez GE HealthCare sur des specs "
             "denses où chaque seuil comptait pour la précision finale du système. "
             "linkedin.com/in/zineb-meftah ? Un échange ?")
    _, w2 = lint(runon, subject="Reranking chez Acme — alternance", kind="cold", company="Acme")
    assert any("one breath" in x or "sentence is" in x for x in w2), f"should warn run-on: {w2}"
    crammed = ("Bonjour. Votre stack me parle. 1ère/126 en L3 IA — linkedin.com/in/zineb-meftah, "
               "github.com/ZinebMEFTAH. Un échange de 10 minutes ?")
    _, w3 = lint(crammed, subject="Stack Acme — alternance M1", kind="cold", company="Acme")
    assert any("own" in x and "line" in x for x in w3), f"should warn crammed links: {w3}"
    # "promo" (graduating class) must NOT be a spam false-positive
    _, w4 = lint("Major de ma promo, j'ai livré un modèle. linkedin.com/in/zineb-meftah. Un échange ?",
                 subject="hook", kind="cold", company="Acme")
    assert not any("promo" in x for x in w4), f"'promo' must not be flagged as spam: {w4}"
    # A well-structured, plain-language email passes clean of structure warnings
    good = ("Faire tenir de la perception temps réel dans le budget d'un drone, c'est le vrai verrou.\n\n"
            "De mon côté : un modèle de vision embarquée temps réel, et un détecteur qui tourne dans le "
            "navigateur. Major de ma promo L3 IA.\n\n"
            "Projets : linkedin.com/in/zineb-meftah\n\nAuriez-vous 10 minutes ?")
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
                          "avec linkedin.com/in/zineb-meftah et une question ?"),
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


WARNINGS = [
    ("email verification capability", w_verification_capability),
    ("quota budgets", w_quota_budgets),
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
    ("job sources registry", t_sources_registry),
    ("international targeting", t_international_targeting),
    ("location mode (remote+in-person)", t_location_mode),
    ("global brand recognizer", t_global_brands),
    ("opportunity scout digest", t_opportunity_digest),
    ("lead posting age", t_lead_age),
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
