#!/usr/bin/env python3
"""
Email quality linter — enforces the drafting rules in /daily-agent mechanically.

The daily-agent skill describes how a good email should look; this turns those
rules into an executable gate. The agent drafts, runs the linter, and revises
until it passes — so the quality rules can't silently slip.

Usage (CLI):
  python email_lint.py --kind cold   --subject "..." --body-file draft.txt
  python email_lint.py --kind followup --subject "Re: ..." --body "inline text"
  → prints ERRORS (block send) and WARNINGS (should fix); exit 1 if any ERROR.

Module:
  from email_lint import lint
  errors, warnings = lint(body, subject, kind="cold", company="Acme")
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Cold is MEDIUM by design (~150–180 words): a company hook + a directly-relevant proof + a
# "what I'd bring you" line + a one-line credibility signal + the ask. That richer body sells Zineb
# without tipping into a CV-dump wall of text (structure, gated below, keeps it scannable). Follow-ups
# and replies stay tight. THIN_MIN flags a cold email that's so short it reads as a drive-by.
# Cold target is ~150–180 (medium); the hard cap sits a touch above at 190 so a strong full-medium
# email to a CTO isn't rejected over a handful of words (that only burns regeneration cycles). Real
# bloat — the 250-word CV-dump — is still blocked.
WORD_LIMIT = {"cold": 190, "followup": 65, "reply": 80}
THIN_MIN_COLD = 90  # below this a cold email is likely under-selling (soft warn; Strategy U is exempt)

_BANNED_OPENERS = [
    "je m'appelle", "je me permets", "je suis zineb", "je me présente",
    "votre offre m'a interpellé", "je suis à la recherche", "je vous écris pour",
    "je reviens vers vous", "suite à mon précédent",
]
_BANNED_SUBJECTS = [
    "candidature alternance", "candidature", "ma candidature", "je m'appelle",
    "demande d'alternance", "recherche alternance", "cv zineb", "lettre de motivation",
]
_FILLER = ["je me permets", "n'hésitez pas", "dans l'attente de votre retour",
           "veuillez agréer", "cordialement"]
# Generic flattery / template tells — reply-killers. Warned, so the agent rewrites them
# into something specific and true about the company.
_CLICHES = [
    "votre entreprise", "votre société", "acteur majeur", "leader dans", "leader du",
    "à la pointe", "passionné par", "passionnée par", "je suis passionn",
    "rejoindre votre équipe", "intégrer votre équipe", "je serais ravi",
    "force de proposition", "votre domaine d'activité", "vos valeurs",
    "fort de mon expérience", "forte de mon expérience", "je suis convaincu",
    "je suis convaincue", "je n'ai aucun doute",
]
_LINKEDIN_RE = re.compile(r"linkedin\.com/in/", re.I)
# Spam-trigger words that hurt inbox placement (checked in subject + body). Kept tight so it
# flags real spam cadence, not normal outreach vocabulary. Warned, so the agent rewrites.
_SPAM_TRIGGERS = re.compile(
    r"\b(gratuit|100\s?%|garanti[e]?|sans engagement|offre spéciale|urgent|"
    r"cliquez ici|click here|act now|limited time|risk[- ]free|gagnez|cash|revenus?|"
    r"opportunité unique|félicitations|congratulations|free money|no obligation|winner)\b",
    re.I)  # NB: "promo"/"promotion" intentionally excluded — in FR it means graduating class
           # ("major de ma promo"), one of Zineb's core credentials, not marketing spam.
_URL_RE = re.compile(r"https?://|www\.|\b[\w.-]+\.(?:com|fr|io|ai|co|net|org|dev)\b", re.I)
# ALL-CAPS shouting (≥6 letters) that isn't a normal acronym — a classic spam/formatting tell.
_CAPS_RE = re.compile(r"\b[A-ZÀ-Þ]{6,}\b")
_CAPS_OK = {"HEALTHCARE", "LINKEDIN", "GITHUB"}   # legit tokens that may appear upper-cased
# LLM-cadence tells: stacked em-dashes (rhythmic asides) and the three-part rhythmic
# list ("X, Y et Z" / "X, Y, and Z"). Both read as machine-generated. Warned, not blocked.
_EMDASH_RE = re.compile(r"[—–]")
_TRIAD_RE = re.compile(
    r"[\wÀ-ÿ'’-]+,\s+[\wÀ-ÿ'’-]+(?:\s+[\wÀ-ÿ'’-]+){0,2}\s+(?:et|and)\s+[\wÀ-ÿ'’-]+", re.I)
_FOOTER_MARKERS = ["ce message a été entièrement rédigé", "this message was entirely written",
                   "p.s. ce message", "p.s. this message"]
_COST_TERMS = re.compile(r"(\bAUA\b|€|exonérat|charges patronales|coût réel|400[\s–-]*700|6\s?000)", re.I)


_DRAFTS_DIR = Path(__file__).parent / "drafts"
RECENT_DAYS = 21          # how far back to look for reused phrasing
REUSE_MIN_WORDS = 7       # shorter fragments repeat innocently ("10 minutes cette semaine ?")
REUSE_LIMIT = 2           # a sentence already used this many times must be rewritten


def _sentences(text: str) -> list[str]:
    """Normalised sentences: lowercased, whitespace collapsed, links and names stripped.

    Names and URLs are removed so that "Pour Veesion : ..." and "Pour Foodvisor : ..." are
    recognised as the SAME sentence — swapping the company name is exactly how a template
    disguises itself as personalisation.
    """
    t = re.sub(r"https?://\S+|\b[\w.-]+@[\w.-]+\b|\b(?:www\.)?[\w-]+\.(?:com|fr|io|ai|co)\S*", " ", text)
    # Strip capitalised words BEFORE lowercasing — afterwards there is nothing left to match, and
    # the whole point is that "Pour Veesion : …" and "Pour Foodvisor : …" are the SAME sentence.
    # Sentence-initial words are spared so an ordinary opening word isn't silently deleted.
    t = re.sub(r"(?<![.!?\n]\s)(?<!^)\b[A-ZÀ-Ý][\w'’-]*", " ", t, flags=re.M)
    t = t.lower()
    out = []
    for s in re.split(r"[.!?\n]+", t):
        s = re.sub(r"[^\w'’ ]+", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        if len(s.split()) >= REUSE_MIN_WORDS:
            out.append(s)
    return out


def _recent_bodies(days: int = RECENT_DAYS, kind: str = "cold") -> list[str]:
    """Bodies of the cold emails drafted in the last `days` days. Empty when there are none.

    Reads drafts/ rather than contacts.xlsx: the Conversation Log stores subjects, not bodies,
    and it is the BODY that had gone formulaic. Best-effort — a missing drafts/ dir (a fresh
    clone, the public mirror) simply means no reuse data, never a crash or a blocked send.
    """
    import datetime as _dt
    if not _DRAFTS_DIR.is_dir():
        return []
    cutoff = _dt.date.today() - _dt.timedelta(days=days)
    out = []
    for day_dir in _DRAFTS_DIR.iterdir():
        if not day_dir.is_dir():
            continue
        try:
            if _dt.date.fromisoformat(day_dir.name) < cutoff:
                continue
        except ValueError:
            continue
        for f in day_dir.glob(f"*{kind}*.txt"):
            try:
                out.append(f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    return out


def reused_sentences(body: str, days: int = RECENT_DAYS, kind: str = "cold",
                     _corpus: list[str] | None = None) -> list[tuple[str, int]]:
    """Sentences in `body` already sent verbatim in recent emails, as (sentence, times seen).

    This is the check that the "vary every email" instruction needed to become real. The rule was
    written in the skill and ignored in practice: across one day's batch of seven cold emails,
    "Major de ma promo L3 IA (1ère/126)" appeared verbatim in six, "10 minutes cette semaine ?"
    closed six, and four opened on "le vrai mur n'est pas X, c'est Y". Each email personalised its
    hook and then fell back into the same five stock lines — which is a template, and templates are
    what the whole strategy system exists to avoid.
    """
    corpus = _corpus if _corpus is not None else _recent_bodies(days, kind)
    if not corpus:
        return []
    seen: dict[str, int] = {}
    for other in corpus:
        for s in set(_sentences(other)):
            seen[s] = seen.get(s, 0) + 1
    out = []
    for s in dict.fromkeys(_sentences(body)):
        n = seen.get(s, 0)
        if n >= REUSE_LIMIT:
            out.append((s, n))
    return out


def _words(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


# Availability phrasing followed by a month that has already gone by. Scoped to that context on
# purpose: a blanket "no past month" rule would fire on Zineb's own history, which is the strongest
# thing in these emails ("en production depuis juin", "sat 08 Feb 2026"). It is only a defect when
# the past month is what she is offering — "je cherche une alternance à partir de septembre 2026",
# still being written on 7 September 2026 because the target date in config had quietly gone by.
# That reads either as careless or as "she found nothing for September", which is the opposite of
# the in-demand framing the whole message is built on.
_MONTHS = {
    "janvier": 1, "january": 1, "février": 2, "fevrier": 2, "february": 2, "mars": 3, "march": 3,
    "avril": 4, "april": 4, "mai": 5, "may": 5, "juin": 6, "june": 6, "juillet": 7, "july": 7,
    "août": 8, "aout": 8, "august": 8, "septembre": 9, "september": 9, "octobre": 10,
    "october": 10, "novembre": 11, "november": 11, "décembre": 12, "decembre": 12, "december": 12,
}
_AVAILABILITY_RE = re.compile(
    r"(?:à partir d[eu]|a partir d[eu]|dès(?: le)?|des le|disponible|dispo\b|pour la rentrée|"
    r"pour la rentree|rentrée d[eu]|rentree d[eu]|starting|available(?: from| in)?|"
    r"start(?:ing)? in|commenc\w+)"
    r"[^.!?\n]{0,40}?"
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b"
    r"(?:\s+(\d{4}))?", re.I)


def _stale_availability(text: str, today=None) -> str | None:
    """The offered start month, if it has already passed. None otherwise."""
    import datetime
    now = today or datetime.date.today()
    for m in _AVAILABILITY_RE.finditer(text or ""):
        month = _MONTHS[m.group(1).lower()]
        year = int(m.group(2)) if m.group(2) else now.year
        if (year, month) < (now.year, now.month):
            return f"{m.group(1)} {year}"
    return None


def lint(body: str, subject: str = "", kind: str = "cold",
         company: str = "") -> tuple[list[str], list[str]]:
    """Return (errors, warnings). errors block the send; warnings should be fixed."""
    errors: list[str] = []
    warnings: list[str] = []
    b = (body or "").strip()
    bl = b.lower()
    subj = (subject or "").strip()
    kind = kind if kind in WORD_LIMIT else "cold"

    if not b:
        errors.append("body is empty")
        return errors, warnings

    stale = _stale_availability(f"{subj}\n{b}")
    if stale:
        errors.append(
            f"offers a start date that has already passed ('{stale}') — say what is true now "
            f"(a rapid start, or the next window) instead of a month that is behind us")

    # ── Trailer must NOT be in the draft (smtp_send adds signature/footer) ──
    if any(m in bl for m in _FOOTER_MARKERS):
        errors.append("the P.S. footer is in the draft — smtp_send.py adds it automatically; remove it")
    last_line = b.splitlines()[-1].strip().lower()
    if last_line in ("zineb meftah", "zineb", "zineb meftah.", "— zineb meftah", "cordialement, zineb"):
        errors.append("body ends with a name/signature — smtp_send.py adds the signature; remove it")

    # ── Banned openers ──
    opener = bl[:80]
    for bad in _BANNED_OPENERS:
        if bad in opener:
            errors.append(f"banned opener: '{bad}' — lead with something about THEM, not about Zineb")
            break

    # ── Word count ──
    wc = _words(b)
    limit = WORD_LIMIT[kind]
    if wc > limit:
        errors.append(f"{wc} words > {limit}-word limit for {kind} — cut ruthlessly")
    # Lower bound (cold only): a very short cold email under the medium target reads as a thin
    # drive-by and under-sells. Soft warning — ultra-short (Strategy U for busy execs) is a valid
    # exception, so this never blocks; it just nudges toward the medium what-I'd-bring + credibility.
    if kind == "cold" and wc < THIN_MIN_COLD:
        warnings.append(f"{wc} words — thin for the medium style (target ~150–180). Add a 'Pour "
                        "[Company] : …' what-I'd-bring line + a one-line credibility signal, unless "
                        "this is a deliberate ultra-short (Strategy U) to a slammed exec.")

    # ── Rhythm: many tiny blocks read as fragments, not as a person writing ──────────
    # "6 short blocks" was meant to stop wall-of-text; taken literally it produced the opposite
    # failure — 160 words chopped into seven one-line paragraphs. Zineb's word for the result was
    # "ugly structured". A block that is a single short sentence, repeated, has no argument running
    # through it; two or three sentences that build is what reads as written rather than assembled.
    if kind == "cold":
        blocks = [b.strip() for b in re.split(r"\n\s*\n", b) if b.strip()]
        # Ignore the greeting and any link-only lines — neither is a paragraph.
        body_blocks = [x for x in blocks
                       if not _URL_RE.fullmatch(x.strip())
                       and not re.match(r"^(bonjour|bonsoir|hello|hi)\b", x.strip(), re.I)
                       and not re.match(r"^(projets?|démo|demo|code|proof|profil)\s*:", x.strip(), re.I)]
        if len(body_blocks) >= 6:
            avg = sum(len(x.split()) for x in body_blocks) / len(body_blocks)
            if avg < 28:
                warnings.append(
                    f"{len(body_blocks)} paragraphs averaging {avg:.0f} words — this reads as "
                    "disconnected fragments rather than a written argument. Merge into 3–4 blocks "
                    "that build on each other (2–3 sentences each).")

    # ── Subject ──
    if not subj:
        errors.append("missing subject line")
    else:
        sl = subj.lower()
        for bad in _BANNED_SUBJECTS:
            if bad in sl:
                errors.append(f"generic subject contains '{bad}' — make it specific to the company")
                break
        if kind in ("followup", "reply") and not sl.startswith("re:"):
            warnings.append("follow-up/reply subject should start with 'Re:' to thread in their inbox")

    # ── Cold-specific rules ──
    if kind == "cold":
        if not _LINKEDIN_RE.search(b):
            errors.append("cold email must include the LinkedIn URL inline (no attachment on cold)")
        je = len(re.findall(r"\bje\b|\bj'", bl))
        if je > 5:
            warnings.append(f"'je' appears {je}× — drifting self-centered; the HOOK must be about THEM "
                            "(the what-I'd-bring + credibility lines legitimately use some 'je')")
        # Cost/AUA is a JUDGMENT call (include for small startups + alternance ask, drop
        # for large co / CDI focus — the skill decides). When present, it must NEVER be its
        # own paragraph (the #1 template tell) — fold it into one clause, ideally the CTA.
        paras = [p.strip() for p in re.split(r"\n\s*\n", b) if p.strip()]
        for p in paras:
            if _COST_TERMS.search(p) and "?" not in p and _words(p) > 18:
                warnings.append("finance/AUA info looks like a standalone paragraph — fold it into ONE "
                                "clause inside another sentence (ideally the CTA), or drop it")
                break
        # Blank-company test (cheap proxy): the company should be referenced somewhere. Match the
        # full name OR a distinctive token of it — companies are naturally referenced by their short
        # name ("Mistral" for "Mistral AI", "Hugging Face" written without a suffix), so requiring the
        # exact legal string produced false-positive nags. Strip generic suffixes, keep tokens ≥3 chars.
        if company:
            haystack = bl + " " + subj.lower()
            _generic = {"ai", "sas", "sasu", "sarl", "sa", "inc", "ltd", "llc", "gmbh", "group",
                        "groupe", "technologies", "technology", "labs", "lab", "io", "app", "the"}
            core = [t for t in re.split(r"[^\w]+", company.lower())
                    if len(t) >= 3 and t not in _generic]
            referenced = company.lower() in haystack or any(t in haystack for t in core)
            if not referenced:
                warnings.append(f"company name '{company}' not referenced — hook may be too generic "
                                "(blank-company test): would this email work for any company?")
    else:
        # Follow-ups/replies must not re-pitch credentials
        if "major de promotion" in bl or "1ère/126" in bl or "1ere/126" in bl:
            warnings.append("follow-up repeats credentials — don't re-pitch; add ONE new element only")
        if _LINKEDIN_RE.search(b):
            warnings.append("follow-up/reply repeats the LinkedIn link — it was already in the cold email")

    # ── Filler (all kinds) ──
    for f in _FILLER:
        if f in bl:
            warnings.append(f"filler phrase '{f}' — drop it; be warm and direct")
            break

    # ── Generic flattery / template tells (all kinds) ──
    for c in _CLICHES:
        if c in bl:
            warnings.append(f"generic/cliché phrase '{c}' — say something SPECIFIC and true "
                            "about them instead of generic flattery")
            break

    # ── LLM-cadence tells (all kinds) ──
    if len(_EMDASH_RE.findall(b)) >= 3:
        warnings.append("stacked em-dashes (3+) — the rhythmic-aside cadence reads as AI-written; "
                        "recast one or two as plain sentences")
    if _TRIAD_RE.search(b):
        warnings.append("three-part rhythmic list ('X, Y et Z') — a classic LLM tell; "
                        "break it up or cut to one item")

    # ── Content quality (cold) ──
    if kind == "cold":
        # Open with THEM, not with Zineb.
        first_sentence = re.split(r"[.!?\n]", b, maxsplit=1)[0].strip().lower()
        if first_sentence.startswith(("je ", "j'", "i ", "i'm", "i am", "mon ", "ma ")):
            warnings.append("first sentence is about Zineb — open with something specific about THEM")
        # A cold email needs one low-friction question as its CTA.
        if "?" not in b:
            warnings.append("no question/CTA — end with ONE low-friction question so replying is effortless")

    # ── Structure & readability (all kinds) — the difference between "read" and "deleted" ──
    # Run-on sentences are the #1 readability killer: a busy founder won't parse a 30-word,
    # comma-spliced breath. Flag the longest sentence so it gets split.
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", b) if s.strip()]
    longest = max((_words(s) for s in sentences), default=0)
    if longest > 28:
        warnings.append(f"longest sentence is {longest} words — too long to read in one breath; "
                        "split it into two short sentences (aim ≤20 words each)")
    # Wall of text: a cold email crammed into one dense block is unscannable. Count content
    # blocks (blank-line separated); a greeting + one big block (≤2 blocks) is the tell.
    if kind == "cold" and _words(b) > 50:
        blocks = [p for p in re.split(r"\n\s*\n", b) if p.strip()]
        if len(blocks) <= 2:
            warnings.append("body is one dense block — structure it into scannable blocks "
                            "(hook / proof / contract ask / links / CTA) separated by blank lines")
    # Links crammed into a prose line (the credential-dump tell, e.g. "1ère/126 — url1, url2").
    # Each link belongs on its own short line ("Mes projets : <url>") so the email stays scannable.
    for line in b.splitlines():
        if len(_URL_RE.findall(line)) >= 2 and _words(line) > 6:
            warnings.append("multiple links crammed on one prose line — give each its own short "
                            "line (e.g. 'Projets : <url>') instead of stuffing them into a sentence")
            break

    # ── Deliverability / spam-trigger checks (all kinds) — protect inbox placement ──
    st = _SPAM_TRIGGERS.search(b + " " + subj)
    if st:
        warnings.append(f"spam-trigger word '{st.group(0)}' — hurts inbox placement; rephrase plainly")
    if subj.count("!") >= 1 or b.count("!") >= 2:
        warnings.append("too many '!' — exclamation marks read as spammy; use at most one, ideally none")
    caps = [w for w in _CAPS_RE.findall(b) if w not in _CAPS_OK]
    if caps:
        warnings.append(f"ALL-CAPS word(s) {caps[:2]} — shouting is a spam/formatting tell; use normal case")
    if kind == "cold":
        n_links = len(_URL_RE.findall(b))
        if n_links > 2:
            warnings.append(f"{n_links} links in the body — >2 hurts deliverability and reads as bulk; "
                            "keep LinkedIn + one proof link, no more")

    # ── Boilerplate: sentences already sent verbatim in recent emails ──────────────
    # "Vary every email" was a rule in the skill and nothing enforced it, so each email
    # personalised its hook and then fell back into the same five stock lines. A reader who
    # gets one of these sees a bespoke email; the CHANNEL sees a template, and so does anyone
    # comparing notes. Blocking, because a warning here is exactly what got ignored before.
    if kind == "cold":
        for sentence, times in reused_sentences(b, kind="cold"):
            errors.append(
                f"this sentence has already gone out {times}x in the last {RECENT_DAYS} days — "
                f"rewrite it in your own words for THIS company: \"{sentence[:70]}…\"")

    return errors, warnings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Lint an outreach email against the quality rules")
    ap.add_argument("--kind", default="cold", choices=["cold", "followup", "reply"])
    ap.add_argument("--subject", default="")
    ap.add_argument("--company", default="")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--body-file")
    g.add_argument("--body")
    args = ap.parse_args(argv)

    body = open(args.body_file, encoding="utf-8").read() if args.body_file else args.body
    errors, warnings = lint(body, subject=args.subject, kind=args.kind, company=args.company)

    for w in warnings:
        print(f"⚠️  WARNING: {w}")
    for e in errors:
        print(f"❌ ERROR: {e}")
    if errors:
        print(f"\n❌ {len(errors)} error(s) — do NOT send; revise the draft.")
        return 1
    print(f"✅ passed{' with ' + str(len(warnings)) + ' warning(s)' if warnings else ''} — OK to send.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
