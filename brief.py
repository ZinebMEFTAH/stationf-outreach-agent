"""Turn the pool into a queue Claude can judge — killing only what is certainly wrong.

WHY. Steps 1 and 2 made the pool clean and readable: `leadset` collapses the same job arriving
from several boards, `descriptions` gets the employer's own words. What was still missing is the
step between "526 leads" and "apply to this one" — someone has to decide. That decision IS
Zineb's odds, so it is deliberately not automated here.

THE SPLIT, and it is the whole design:

    this module          fetch · kill the CERTAIN no's · assemble evidence · order the queue
    Claude, in session   read the brief and judge suitability

A rule may only kill what is certainly wrong — a BTS-level posting, a school advertising its own
course, a job in Toulouse. Everything else survives and reaches Claude WITH its evidence, because
the calls that actually matter (is this engineering or change management? does the GE HealthCare
arc transfer? which hook leads?) are judgement, and a regex making them silently is exactly how a
real opportunity disappears.

⚠ ABSENCE NEVER KILLS — the rule this module is built around, already paid for twice. CACIB's
  "Ingénieur Data H/F" carried no alternance flag anywhere and was a real 24-month alternance;
  and measured against France Travail's own boolean, 12 of 40 alternances never say the word in
  their body at all. A missing contract, a missing level and a missing location all mean UNKNOWN,
  and unknown stays in the queue.

⚠ NO CEILING. Her instruction: "candidate for the most possible from the most suitable to the
  least till it becomes not suitable at all." So tiers ORDER the queue; they never truncate it.
  Nothing here returns a shortlist.

⚠ THE TIERS RANK EVIDENCE, NOT SUITABILITY. Tier 1 means "there is enough here to judge on",
  not "this is a good job". A posting can sit in tier 1 and be plainly wrong for her — that is
  the reader's call, and the brief gives the reader what they need to make it.
"""
from __future__ import annotations

import re

import descriptions as D
import jobsource as js
import leadset as ls
import tracker

# Words that prove the employer wants an alternant. Read from the BOARD's own field first — the
# standing rule everywhere in this repo — and only then from the title.
_ALT_TITLE = re.compile(r"\balternan[ct]e?s?\b|\bapprenti(?:e|s|es)?\b|"
                        r"contrat\s+d['’]apprentissage|\ben\s+alternance\b", re.I)

# An explicitly NON-alternance contract, as stated by the board itself. Only these kill.
_EXPLICIT_NOT_ALT = {"cdi", "cdd", "stage", "internship", "permanent", "freelance", "interim"}

# Diplomas below her level. She holds a Licence and has entered M1, so a BTS / DUT / Bac+2 /
# licence pro alternance is not a posting she can take — the one class of level rule that is
# certain. `BUT` is deliberately absent: it is the ordinary French noun, and both jobsource and
# descriptions refuse it for the same reason.
_BELOW = re.compile(r"\bbts\b|\bdut\b|bac\s*\+\s*2\b|licence\s+pro", re.I)

# Anything she has already applied to must not come back around. Read from her own log.
_APPLIED_LOG = "applications_log.md"


# Noise that says nothing about WHICH job this is: contract words, gender marks, city names in
# the logged line. Dropped before comparing a posting against something she already applied to.
_ROLE_NOISE = re.compile(r"\b(alternan\w*|apprenti\w*|stage|h\s*f|f\s*h|paris|ile|france|"
                         r"mois|ans?|le|la|les|de|du|des|en|et|pour|chez|poste)\b", re.I)


def _tokens(text: str) -> set[str]:
    folded = ls._fold(_ROLE_NOISE.sub(" ", text or ""))
    return {w for w in re.split(r"[^a-z0-9]+", folded) if len(w) > 2}


# Words that name an industry, a place or a legal form rather than an employer. A match resting
# on one of these alone is not evidence: "Air France" and "France Travail" share exactly one
# token, and so do "Crédit Agricole CIB" and "Société Générale CIB".
_GENERIC_CO = frozenset("""
france french paris europe european international monde world global emea idf
sas sarl sasu plc inc ltd llc gmbh spa group groupe holding company compagnie cie corp
corporation corporate enterprise entreprise filiale
bank banque banking assurance assurances investment finance financial financiere capital
conseil consulting consultants services service solutions solution partners partenaires
technologies technologie technology tech digital numerique informatique systems systemes
industries industrie industriel energy energie sante health medical retail distribution
mobility transport logistique immobilier ingenierie engineering data cloud software
national nationale nationaux general generale generali centre center agence agency
cib ebs sdn division departement direction branche business commerce commercial
eau water environnement environment recherche research innovation labs siege
""".split())


def _same_employer(a: set[str], b: set[str]) -> bool:
    """Do these two names mean the same employer?

    Her log writes employers more fully than the boards do — "IBM France" against "IBM",
    "Veolia Environnement" against "Veolia", "Natixis CIB (BPCE)" against "BPCE SA" — so exact
    key equality matched only 2 of 6 real pairs. Subset, or two tokens in common.

    ⚠ ONE SHARED TOKEN IS ENOUGH WHEN IT IS DISTINCTIVE, and requiring two silently let a
    DUPLICATE APPLICATION through (2026-09-21). Her log says "Natixis CIB (BPCE)"; LinkedIn says
    "Natixis Corporate & Investment Banking". Neither is a subset of the other and they share
    exactly {natixis} — so the posting she had ALREADY APPLIED TO came back unflagged, at the top
    of the queue, ten days later. The guard the two-token rule existed for is real ("Air France"
    vs "France Travail"), but it was aimed at the wrong thing: what makes that pair a false match
    is not the COUNT of shared tokens, it is that the shared token is `france`. So the test is
    now the token's distinctiveness. Both directions are locked by preflight.
    """
    if not a or not b:
        return False
    if a <= b or b <= a:
        return True
    # ⚠ TWO SHARED TOKENS ARE NOT EVIDENCE EITHER WHEN BOTH ARE GENERIC — "Thales Digital
    # Solutions" and "Atos Digital Solutions" share two, and are different employers. What
    # carries a match is a DISTINCTIVE token, however many there are.
    return bool((a & b) - _GENERIC_CO)


_STAGE_TITLE = re.compile(r"\bstages?\b|\bstagiaire\b|\binternship\b|\bintern\b", re.I)


def _kind(role: str) -> str | None:
    """"alternance" | "stage" | None. None means the wording simply does not say."""
    if _ALT_TITLE.search(role or ""):
        return "alternance"
    if _STAGE_TITLE.search(role or ""):
        return "stage"
    return None


def _applied() -> list[tuple[set[str], set[str], str | None]]:
    """(employer tokens, role tokens) for every application she has already sent.

    Read from her own hand-maintained log, whose application rows are markdown table lines
    shaped `| N | **Employer** | Role, city — terms | channel | pack |`. The numeric first cell
    is what separates them from the other tables in that file.
    """
    try:
        from pathlib import Path
        txt = Path(__file__).parent.joinpath(_APPLIED_LOG).read_text(encoding="utf-8")
    except Exception:
        return []
    rows = re.findall(r"^\|\s*\d+\s*\|\s*\*\*(.+?)\*\*\s*\|\s*([^|]*)\|", txt, re.M)
    return [(_tokens(c), _tokens(r), _kind(r)) for c, r in rows]


def applied_verdict(lead: dict, applied: list) -> str:
    """"" | "company" | "exact".

    TWO FACTORS BEFORE ANYTHING IS KILLED. A big employer runs independent teams, so a second
    role at GE HealthCare is a real opportunity and must only be FLAGGED; re-applying to the
    posting she already sent a pack for is a waste of her time and is refused. Requiring the
    role to match too is also what makes the fuzzy employer match safe: a wrong company match
    then costs a flag, never a lead.
    """
    ct, rt = _tokens(lead.get("company") or ""), _tokens(lead.get("role") or "")
    hit = ""
    for a_co, a_role, a_kind in applied:
        if not _same_employer(ct, a_co):
            continue
        hit = "company"
        # ⚠ CONTRACT FLAVOUR MAY ONLY BLOCK ON AN EXPLICIT DISAGREEMENT. It exists because
        # Crédit Agricole Assurances' "STAGE - Data Scientist" was refused as the posting she had
        # already applied to, when she applied to their ALTERNANCE and the shared tokens were
        # just {data, scientist}. But comparing "does the title say alternance" in BOTH
        # directions made SILENCE look like disagreement: her log writes the role as "Platform
        # Engineering DevOps (Kubernetes & OpenShift), Puteaux (92) — réf. REF497Y", with no
        # contract word at all, so Generix — which she HAD applied to — came back into the queue
        # with a 100% role-token match. A posting is only a different KIND when both sides say
        # so and say different things.
        same_kind = _kind(lead.get("role") or "") in (None, a_kind) or a_kind is None
        if rt and same_kind and len(rt & a_role) / len(rt) >= 0.6:
            return "exact"
    return hit


# --------------------------------------------------------------------------- the certain no's

def _board_contract(lead: dict) -> str:
    return str((lead.get("meta") or {}).get("contract") or "").strip().lower()


def refuse(lead: dict, applied: set[str] | None = None) -> str:
    """Why this lead is CERTAINLY not for her — or "" to let it through.

    Cheapest checks first, and every one of them must be something a person would agree with
    instantly. If a check needs a judgement call, it does not belong here; it belongs in the
    brief, as evidence.
    """
    company, role = (lead.get("company") or ""), (lead.get("role") or "")

    # ⚠ A SCHOOL IS DEMOTED, NEVER DROPPED — see signals()/_order(). It is tempting to kill
    # these (30 ISCOD ads in one run) but the name test cannot tell a course-seller from an
    # edtech EMPLOYER: Galileo Global Education appeared 11 times in the first audit and runs its
    # own group IT, with one posting reading python + javascript. tracker.rank_pending_leads has
    # scored this -30 rather than dropping it since 2026-09-05, for exactly this reason, and
    # promoting it to a kill here contradicted that on the strength of a name.

    # Her level, checked BEFORE the role rules: jobsource also refuses BTS titles, so leaving it
    # later reported "not software/data" for a perfectly good data role that was simply
    # pitched at BTS. The refusal list is how a rule eating real leads becomes visible, so a
    # wrong reason there costs more than it looks.
    if _BELOW.search(role):
        return "BTS / DUT / Bac+2 / licence pro — below her level"

    # "Business Developer" and "Développeur Foncier" pass any filter containing "developer".
    if js.excluded_role(role):
        return "role is not software/data (commercial, real-estate, or a stack she refuses)"
    if not js.matches_target_role(role):
        return "title matches none of her target roles"

    # Geography. Reuses the digest's own gate so the two halves cannot drift apart; note that it
    # returns True for "France, city unspecified", i.e. unknown is reachable.
    try:
        import opportunities as opp
        ok, why = opp.is_reachable(lead)
        if not ok:
            return f"not reachable: {why}"
    except Exception:                                     # noqa: BLE001
        pass                                              # never let a gate failure empty the queue

    # ALTERNANCE ONLY, per her instruction — but only an EXPLICIT non-alternance contract kills.
    # A posting that says nothing is UNKNOWN, and unknown is exactly where CACIB was found.
    board = _board_contract(lead)
    if board in _EXPLICIT_NOT_ALT and not _ALT_TITLE.search(role):
        return f"the board states this is a {board}, not an alternance"

    if applied and applied_verdict(lead, applied) == "exact":
        return "she already applied to this exact posting"
    return ""


def refuse_after_reading(lead: dict, facts: dict, text: str = "") -> str:
    """The second gate, once the posting's own words are available.

    Deliberately almost empty. Only ONE thing gets to kill a lead here, and it needs three
    independent sources to agree — because this gate runs on the richest evidence in the system
    and is therefore the easiest place to throw away something real.
    """
    contract = facts.get("contract")
    if contract in ("stage", "cdi", "cdd"):
        # Three-way agreement required: the description says one thing, and neither the board nor
        # the title says otherwise. `descriptions._contract` measured 97% precision against
        # France Travail's own boolean, but its recall is 70% — so its SILENCE means nothing and
        # only a positive non-alternance verdict, uncontradicted, may kill.
        if (not _ALT_TITLE.search(lead.get("role") or "")
                and _board_contract(lead) != "alternance"
                # ...and the text must not mention alternance AT ALL. Spot-checking the first
                # live run found this gate killing real leads on incidental words: Reddit on the
                # English "similar STAGE growth companies", SYSTNAPS and TEAM.IS on sentences
                # about PRIOR experience ("une première expérience acquise en stage ou en
                # alternance est appréciée"). A single alternance mention is genuinely ambiguous
                # — 5 real against 9 not, measured — so when the word appears at all, this gate
                # stands down and the posting reaches Claude to be read.
                and not _ALT_TITLE.search(text or "")):
            return f"the posting itself describes a {contract}"
    # ⚠ level == "m2_or_final" is NOT a kill and must never become one. Plenty of two-year
    # alternances start in M1 and still write "dernière année" or "Bac+5"; it is a flag to weigh,
    # not a refusal. Same for beginner_ok being False.
    return ""


# --------------------------------------------------------------------------- evidence

# Her own stack, as the CV can defend it. Overlap with these is the single most useful thing the
# description yields, and the ABSENCE of any programming language is what separated Docaposte's
# adoption role from Veolia's engineering one.
_LANGUAGES = {"python", "java", "javascript", "typescript", "sql", "c++", "c#", "scala", "go", "php"}
_HERS = {"python", "sql", "pytorch", "tensorflow", "scikit-learn", "pandas", "numpy", "llm", "rag",
         "genai", "nlp", "agents", "mcp", "langchain", "prompt", "embeddings", "vector db",
         "docker", "git", "linux", "ci/cd", "aws", "azure", "gcp", "fastapi", "flask",
         "mlops", "hugging face", "transformers", "fine-tuning", "openai", "anthropic",
         "streamlit", "opencv", "bash", "mlflow", "rest api"}

# Boards that REMOVE closed postings, against boards that serve stale rows. Not a score — a
# caveat printed next to the lead, because a pack built on a dead aggregator row cost a full
# CV + letter once already.
_AUTHORITATIVE = re.compile(r"france\s*travail|francetravail|apec|bonne\s*alternance|careers|"
                            r"free[-_ ]?work|workday|smartrecruiters|greenhouse|lever|ashby", re.I)


MAX_AGE_DAYS = 14          # her rule, 2026-09-21: "i do not wanna offers older than two weeks"


def _STALE_BEFORE() -> str:
    from datetime import date, timedelta
    return (date.today() - timedelta(days=MAX_AGE_DAYS)).isoformat()


def signals(lead: dict, got: dict) -> dict:
    """Everything the reader needs that is a FACT, never a verdict."""
    facts = got.get("facts") or {}
    stack = set(facts.get("stack") or [])
    company = lead.get("company") or ""
    meta = lead.get("meta") or {}
    sig = {
        "alternance": bool(_ALT_TITLE.search(lead.get("role") or "")
                           or _board_contract(lead) == "alternance"
                           or facts.get("contract") == "alternance"),
        "languages": sorted(stack & _LANGUAGES),
        "overlap": sorted(stack & _HERS),
        "stack": sorted(stack),
        "level": facts.get("level"),
        "duration": facts.get("duration"),
        "start": facts.get("start"),
        "remote": facts.get("remote"),
        "beginner_ok": facts.get("beginner_ok"),
        "read": got.get("chars", 0) >= 400,
        "origin": got.get("origin"),
        "chars": got.get("chars", 0),
        "source_reliable": bool(_AUTHORITATIVE.search(lead.get("source") or "")),
        "few_applicants": bool(meta.get("few_applicants")),
        "experience": meta.get("experience") or "",
        "posted": meta.get("posted") or "",
    }
    # The two channels that beat any score, both optional sidecars — absence is silent.
    for name, mod in (("warm", "warm_network"), ("school_partner", "school_partners")):
        try:
            sig[name] = __import__(mod).summary(company) or ""
        except Exception:                                 # noqa: BLE001
            sig[name] = ""
    # THE DISCRIMINATOR, stated as what it is: an absence, and only meaningful if the text was
    # actually read. Never phrased as a verdict — Claude decides what it means.
    # ⚠ AGE IS THE STRONGEST LIVENESS SIGNAL WE HAVE, and her rule as of 2026-09-21: nothing
    # older than two weeks. Measured that day: of 70 dated alternances in the queue, 48 were
    # older than a fortnight — and every posting that turned out dead when she opened it
    # (Groupe SII, Docaret, Hermès, ANFSI) was in that group. Most employers cannot be verified
    # at all (68% run no readable ATS), so the posting date is the only evidence left.
    sig["posted"] = str(meta.get("posted") or "")[:10]
    sig["stale"] = bool(sig["posted"]) and sig["posted"] < _STALE_BEFORE()
    sig["school"] = tracker.is_training_body(company)
    sig["names_no_language"] = sig["read"] and not sig["languages"]
    # WE COULD NOT READ IT is not IT IS THIN, and conflating the two buries exactly the leads
    # worth chasing. On the first full queue run LinkedIn returned 45 throttles - her biggest
    # source - and every one of those leads sat in tier 3 looking like a weak posting. A
    # retryable failure is a fact about US: it has to say so and be re-run, never judged.
    sig["retryable"] = str(sig["origin"]).startswith(
        ("error:throttled", "error:blocked", "error:server_error", "error:unreachable"))
    sig["unread_because"] = (
        "" if sig["read"]
        else "we were throttled or blocked - RETRY, do not read this as a thin posting"
        if sig["retryable"]
        else "the posting is gone" if sig["origin"] == "error:gone"
        else "this host serves no readable description (APEC, WTTJ: JS-walled)")
    return sig


def tier(sig: dict) -> int:
    """How much there is to judge on. 1 = enough, 3 = little. NOT how good the job is."""
    if not sig["read"]:
        return 3
    if sig["alternance"] and sig["overlap"]:
        return 1
    return 2


def _order(item: dict) -> tuple:
    """Within a tier, put the leads whose evidence is strongest first."""
    s = item["signals"]
    return (1 if s.get("school") else 0,            # a course-seller sinks below every real job
            1 if s.get("stale") else 0,            # anything over MAX_AGE_DAYS sinks with it
            item["tier"],
            1 if s.get("applied_before") else 0,     # another role at the same employer: lower
            0 if s["warm"] else 1,
            0 if s["school_partner"] else 1,
            # ⚠ SOURCE RELIABILITY IS NOT A TIEBREAK, IT IS A PRECONDITION — moved up from
            # eighth place on 2026-09-21 after HelloWork went SEVEN-for-seven dead (five
            # measured on 09-19, two more today, each costing a full CV + letter pack). A lead
            # she cannot apply to is worth zero however well its stack matches, and the boards
            # that REMOVE closed postings (France Travail, APEC, La Bonne Alternance, Free-Work,
            # the employers' own ATS readers) are live by construction. Aggregator rows are not
            # dropped — that is where the volume is, and volume is the goal — they simply stop
            # monopolising the top of a queue she works from the top of.
            0 if s["source_reliable"] else 1,
            -len(s["overlap"]),
            0 if s["few_applicants"] else 1,
            0 if s["experience"] == "D" else 1,
            # ⚠ A TOTAL ORDER, or the queue is not reproducible. Company alone ties constantly
            # (Thales posts ~78 roles), and a stable sort then just preserves whatever order the
            # boards happened to answer in — so the same pool, shuffled, produced a different
            # queue. leadset had exactly this bug on its surviving URL; same fix.
            item["lead"].get("company") or "",
            item["lead"].get("role") or "",
            item["lead"].get("url") or "")


# --------------------------------------------------------------------------- the queue

def _verify_head(queue: list[dict], want: int) -> tuple[list[dict], list[dict]]:
    """Check the TOP of the ordered queue against the employer's own ATS. Expensive, so last.

    WHY IT IS BOUNDED. Verification costs a fingerprint plus a board query per employer, and the
    queue is ~500 long; she only ever works from the top. So it walks down until `want` leads
    have SURVIVED, and leaves the tail unverified and honestly marked as such.

    ⚠ 'unknown' IS NOT 'gone' AND MAY NEVER KILL. Most large French employers run an ATS this
    repo cannot read — Taleo, SuccessFactors, iCIMS, Avature — and those are precisely the
    employers who carry the alternance market and the CFA relationships. Treating unreadable as
    dead would empty the queue of exactly the leads worth having.
    """
    import ats
    kept, gone, checked = [], [], 0
    for item in queue:
        if checked >= want:
            item["signals"]["ats"] = ""
            kept.append(item)
            continue
        checked += 1
        try:
            # ⚠ THE EMPLOYER'S NAME, NOT THE BOARD'S DECORATION. ats.verify resolves a domain
            # from this string, so "Groupe SII, super recruteur" (HelloWork's badge) looks for a
            # company by that literal name and can only ever answer "unknown" — silently, for
            # every HelloWork lead in the queue.
            v = ats.verify(ls.display_company(item["lead"].get("company") or ""),
                           item["lead"].get("role") or "")
        except Exception as e:                            # noqa: BLE001
            # A verifier failure is about US. It must never read as "the posting is dead".
            item["signals"]["ats"] = f"unknown ({type(e).__name__})"
            kept.append(item)
            continue
        item["signals"]["ats"] = f"{v['verdict']} · {v.get('platform') or '?'}"
        if v["verdict"] == "gone":
            gone.append({"lead": item["lead"],
                         "why": f"closed on the employer's own {v.get('platform')} "
                                f"({v.get('note')})"})
        else:
            kept.append(item)
    return kept, gone


def build(leads: list[dict], read: bool = True, verify_top: int = 0, on_progress=None) -> dict:
    """The full ordered queue, plus what was refused and why.

    Returns {"queue": [...], "refused": [...], "stats": {...}}. `queue` is EVERY surviving lead,
    in order — never a shortlist. Refusals are kept and returned rather than dropped silently, so
    a rule that starts eating real leads is visible instead of invisible.
    """
    applied = _applied()
    merged, _ = ls.merge(leads)
    queue, refused = [], []
    for lead in merged:
        why = refuse(lead, applied)
        if why:
            refused.append({"lead": lead, "why": why})
            continue
        got = D.describe(lead) if read else {"text": "", "chars": 0, "origin": "unread", "facts": {}}
        why = refuse_after_reading(lead, got.get("facts") or {}, got.get("text") or "")
        if why:
            refused.append({"lead": lead, "why": why})
            continue
        sig = signals(lead, got)
        sig["applied_before"] = applied_verdict(lead, applied)
        item = {"lead": lead, "signals": sig, "text": got.get("text", "")}
        item["tier"] = tier(sig)
        queue.append(item)
        if on_progress:
            on_progress(len(queue), len(merged))
    queue.sort(key=_order)
    # LAST, and only over the head: the single most expensive check in the pipeline, and the one
    # that stops a full CV + letter being built for a job that no longer exists (the AP-HP pack
    # was, and HelloWork went 5-for-5 dead on the day it was measured).
    if verify_top:
        queue, gone = _verify_head(queue, verify_top)
        refused += gone
    stats = {"pool": len(leads), "merged": len(merged), "queue": len(queue),
             "refused": len(refused),
             # Reported apart from the tiers because it is a fact about the RUN, not about the
             # postings. If this is not ~0, RE-RUN before concluding anything about the tail of
             # the queue - the description cache makes the second pass nearly free.
             "retryable": sum(1 for q in queue if q["signals"].get("retryable")),
             "schools": sum(1 for q in queue if q["signals"].get("school")),
             "stale": sum(1 for q in queue if q["signals"].get("stale")),
             "verified": sum(1 for q in queue if q["signals"].get("ats")),
             "confirmed_live": sum(1 for q in queue
                                   if str(q["signals"].get("ats", "")).startswith("live")),
             "tier1": sum(1 for q in queue if q["tier"] == 1),
             "tier2": sum(1 for q in queue if q["tier"] == 2),
             "tier3": sum(1 for q in queue if q["tier"] == 3)}
    return {"queue": queue, "refused": refused, "stats": stats}


def render(item: dict, full: bool = False) -> str:
    """One lead as a brief. This is what Claude reads to decide."""
    lead, s = item["lead"], item["signals"]
    # HelloWork appends its own badge to the employer ("Groupe SII, super recruteur"), and that
    # string would land in a CV header and a letter's salutation if it were carried through.
    company = ls.display_company(lead.get("company") or "")
    out = [f"── T{item['tier']}  {company} — {lead.get('role')}",
           f"   {lead.get('url')}",
           f"   {lead.get('location') or 'lieu inconnu'} · {lead.get('source')}"
           f"{' · ⚠ aggregator: verify before building a pack' if not s['source_reliable'] else ''}"]
    if s.get("ats"):
        out.append(f"   ATS: {s['ats']}"
                   + ("  ✅ confirmed open on the employer's own system"
                      if str(s["ats"]).startswith("live")
                      else "  — unreadable ATS, this is NOT evidence it is closed"))
    if s.get("posted"):
        out.append(f"   publiée le {s['posted']}"
                   + ("  ⚠ plus de deux semaines" if s.get("stale") else ""))
    if s.get("school"):
        out.append("   ⚠ this 'employer' is a school/CFA — usually recruiting STUDENTS into its "
                   "own course, but some (Galileo, OpenClassrooms) also hire engineers")
    if s.get("applied_before") == "company":
        out.append("   ↩ she has already applied to this employer for a DIFFERENT role")
    if s["warm"]:
        out.append(f"   ⚡ WARM — {s['warm']}")
    if s["school_partner"]:
        out.append(f"   🎓 CFA partner — {s['school_partner']}")
    out.append(f"   contrat: {'alternance' if s['alternance'] else 'non précisé'}"
               f" · niveau: {s['level'] or 'non précisé'}"
               f" · durée: {s['duration'] or '—'} · début: {s['start'] or '—'}")
    if s["few_applicants"]:
        out.append("   peu de candidats (rare — the most direct evidence it gets read)")
    if s["experience"]:
        out.append("   expérience: " + {"D": "débutant accepté", "S": "souhaitée",
                                        "E": "⚠ exigée"}.get(s["experience"], s["experience"]))
    out.append(f"   stack cité: {', '.join(s['stack']) or '—'}")
    out.append(f"   recouvrement avec le sien: {', '.join(s['overlap']) or 'aucun'}")
    if s["names_no_language"]:
        out.append(f"   ⚠ names NO programming language in {s['chars']} chars read "
                   f"— adoption role, or just a short posting? read it")
    if not s["read"]:
        out.append(f"   ⚠ not read ({s['origin']}): {s['unread_because']}")
    if full and item.get("text"):
        out.append("\n" + item["text"])
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    import opportunities as opp
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    pool = opp._fetch_all()
    got = build(pool, verify_top=limit)
    st = got["stats"]
    print(f"\npool {st['pool']} → merged {st['merged']} → queue {st['queue']} "
          f"(T1 {st['tier1']} · T2 {st['tier2']} · T3 {st['tier3']}), refused {st['refused']}")
    print(f"verified {st['verified']} · confirmed live {st['confirmed_live']} · "
          f"retryable {st['retryable']}\n")
    for item in got["queue"][:limit]:
        print(render(item), "\n")
