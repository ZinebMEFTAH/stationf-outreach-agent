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


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def t_imports():
    import config, tracker, smtp_send, imap_fetch, cv_builder
    import contact_finder, scraper, companies, email_verify  # noqa: F401
    import jobsource, wttj, hellowork, apec, france_travail, company_resolver  # noqa: F401


def t_config_caps():
    import config
    assert config.COLD_CAP == 2, f"COLD_CAP should be 2, got {config.COLD_CAP}"
    assert config.WARM_CAP == 3, f"WARM_CAP should be 3, got {config.WARM_CAP}"
    assert config.DAILY_CAP == config.COLD_CAP + config.WARM_CAP, "DAILY_CAP mismatch"
    assert config.FOLLOWUP_DAYS == 4, f"FOLLOWUP_DAYS should be 4, got {config.FOLLOWUP_DAYS}"


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
    from email_verify import build_patterns
    pats = build_patterns("Zoé", "Lefèvre", "example.com")
    assert pats[0] == "zoe.lefevre@example.com", pats[0]
    assert all("@example.com" in p for p in pats)


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
]


def main() -> int:
    print(f"[preflight] running {len(CHECKS)} checks...")
    for name, fn in CHECKS:
        check(name, fn)
    print()
    if _failed:
        print(f"[preflight] ❌ FAILED — {_passed} passed, {_failed} failed")
        for f in _failures:
            print(f"   - {f}")
        return 1
    print(f"[preflight] ✅ all {_passed} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
