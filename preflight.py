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
    import labonnealternance, lead_facts, ats_detect  # noqa: F401


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
                "labonnealternance"}
    assert set(scraper.SOURCES) == expected, set(scraper.SOURCES)
    for name, src in scraper.SOURCES.items():
        assert callable(src.get("discover")), f"{name}: discover not callable"
        assert callable(src.get("resolve")), f"{name}: resolve not callable"
        assert "enrich" in src, f"{name}: missing enrich flag"
    import apec, france_travail, free_work, hellowork, labonnealternance, wttj
    for m in (wttj, hellowork, apec, free_work, france_travail, labonnealternance):
        assert m.NAME and callable(m.discover) and callable(m.resolve_company_site), m.__name__


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
    # Word-limit enforced
    long_body = "linkedin.com/in/zineb-meftah " + "mot " * 130
    errs3, _ = lint(long_body, subject="Specific hook about Acme product", kind="cold", company="Acme")
    assert any("word" in e for e in errs3), "over-limit cold email must error on word count"
    # Content-quality WARNINGS: generic flattery, first-line-about-Zineb, missing CTA
    weak = "Je suis passionnée par votre entreprise. linkedin.com/in/zineb-meftah."
    _, warns = lint(weak, subject="Specific hook about Acme", kind="cold", company="Acme")
    wj = " ".join(warns).lower()
    assert "cliché" in wj or "generic" in wj, f"should warn on flattery: {warns}"
    assert "first sentence" in wj, f"should warn first-line-about-Zineb: {warns}"
    assert "cta" in wj or "question" in wj, f"should warn on missing CTA: {warns}"


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


WARNINGS = [
    ("email verification capability", w_verification_capability),
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
    ("enrichment stats", t_enrichment_stats),
    ("email verification gate", t_email_verification_gate),
    ("tracker schema", t_tracker_schema),
    ("tracker helpers", t_tracker_helpers),
    ("strategy bandit", t_strategy_bandit),
    ("email linter", t_email_linter),
    ("lead ranking", t_lead_ranking),
    ("funnel + cooldown", t_funnel_and_cooldown),
    ("smtp footer/alert logic", t_smtp_footer_logic),
    ("smtp alert kind", t_smtp_alert_kind),
    ("smtp language detection", t_smtp_lang_detection),
    ("no empty active emails", t_contacts_no_empty_active_emails),
    ("CV .tex sources", t_cv_sources),
    ("about_me matching guide", t_about_me_matching_guide),
    ("lead-fact cache", t_lead_facts),
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
