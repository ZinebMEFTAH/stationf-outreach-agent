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

WORD_LIMIT = {"cold": 110, "followup": 65, "reply": 80}

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


def _words(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


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
        if je > 2:
            warnings.append(f"'je' appears {je}× — too self-centered; lead with them, weave in credentials")
        # Cost/AUA is a JUDGMENT call (include for small startups + alternance ask, drop
        # for large co / CDI focus — the skill decides). When present, it must NEVER be its
        # own paragraph (the #1 template tell) — fold it into one clause, ideally the CTA.
        paras = [p.strip() for p in re.split(r"\n\s*\n", b) if p.strip()]
        for p in paras:
            if _COST_TERMS.search(p) and "?" not in p and _words(p) > 18:
                warnings.append("finance/AUA info looks like a standalone paragraph — fold it into ONE "
                                "clause inside another sentence (ideally the CTA), or drop it")
                break
        # Blank-company test (cheap proxy): the company name should appear somewhere.
        if company and company.lower() not in (bl + " " + subj.lower()):
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
