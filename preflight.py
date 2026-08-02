#!/usr/bin/env python3
"""
Pre-flight self-test for the StationF outreach agent.

Runs fast, offline checks (no SMTP/IMAP/network) to verify the system is healthy
BEFORE a cron job invokes Claude. If this fails, the run scripts skip the run and
send an alert — better to do nothing than to operate on a broken system.

Usage:
  python preflight.py            # full check, exit 0 = healthy, 1 = broken
  python preflight.py --quiet    # only print failures

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

_QUIET = "--quiet" in sys.argv
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
        print(f"  ⚠️  {name}: {msg}")
    elif not _QUIET:
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
    assert hasattr(o, "_fetch_france_inperson") and hasattr(o, "_fetch_arbeitnow")
    assert set(o._SECTION_LABEL) == {"remote", "france", "relocate"}
    # section routing: remote board → remote; French API onsite → france; abroad onsite → relocate
    assert o._section({"mode": "remote", "source": "RemoteOK"}) == "remote"
    assert o._section({"mode": "onsite", "source": "apec"}) == "france"
    assert o._section({"mode": "onsite", "source": "Arbeitnow"}) == "relocate"
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
    (this was the bug that duplicated Haliro's thread 3-5x)."""
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
    """Warn when email verification is BLIND.

    Mailbox verification uses Hunter.io (works anywhere) if HUNTER_API_KEY is set,
    otherwise it falls back to an outbound SMTP-port-25 probe. Cloud VMs (incl. this
    project's GCP VM) block port 25, so with NO Hunter key the probe always comes back
    inconclusive and `verify()` returns `mx_only` for everything. Consequences:
      • every named decision-maker is treated as unconfirmed → silently downgraded to
        the generic contact@ inbox (the personalization is wasted), and
      • a dead generic inbox (contact@ on a live domain) is assumed to exist and BOUNCES.
    This is a real observed failure mode — surface it loudly instead of degrading silently.
    """
    import os
    import config
    key = (os.environ.get("HUNTER_API_KEY", "") or getattr(config, "HUNTER_API_KEY", "") or "").strip()
    if key:
        return None
    return ("HUNTER_API_KEY is not set — mailbox verification relies solely on outbound SMTP "
            "port 25. On a port-25-blocked host (e.g. the GCP VM) verification is BLIND: named "
            "contacts silently degrade to contact@ and dead generic inboxes will bounce. "
            "Set HUNTER_API_KEY (hunter.io free tier) in .env to restore it.")


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
    # Claude runs this week
    wk = usage_budget.count("claude_run", usage_budget.WEEK)
    if config.CLAUDE_MAX_RUNS_7D and wk >= 0.8 * config.CLAUDE_MAX_RUNS_7D:
        msgs.append(f"Claude runs this week: {wk}/{config.CLAUDE_MAX_RUNS_7D}")
    return "; ".join(msgs) if msgs else None


WARNINGS = [
    ("email verification capability", w_verification_capability),
    ("quota budgets", w_quota_budgets),
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
]


def main() -> int:
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
