"""What the BOARD says the job IS — not what its title looks like.

Every gate in the digest used to run on the job TITLE: five words, chosen by whoever wrote the
posting, with no agreed vocabulary behind them. That is how a CertiK "Compliance Engineer Intern"
— a Legal & Compliance role with no code in any of its ten responsibilities — reached Zineb on
2026-09-15 scored 61/100 and labelled "Backend/software role". The title said "Engineer", and the
title was all anything looked at.

Meanwhile the employer had already answered the question. CertiK files that posting under
`categories.department = "Compliance"`; it files the Formal Methods role under
`"Engineering - Tools"`. Lever, Greenhouse, Ashby and SmartRecruiters all publish a department,
SmartRecruiters publishes a NORMALISED `function` on top of it, and France Travail publishes the
state ROME code. All of it was fetched and discarded.

This is the same lesson CLAUDE.md already records for contract type and experience level ("What
the BOARD says about her chances") applied to the job FAMILY: prefer the board's own structured
classification to anything inferred from a title.

Three verdicts, and the third one matters as much as the others:

    "engineering"  the employer files this under engineering/tech/data/R&D.
    "off_domain"   the employer files it under sales, marketing, compliance, legal, finance, HR…
                   REFUSED by the digest whatever the title claims.
    ""             no usable classification — most French board rows, and ATS values like
                   JobTeaser's literal "Other". UNKNOWN IS NEUTRAL: the title logic decides, exactly
                   as before. An absent field must never be read as a "no", or adding a source with
                   no department would silently empty the digest.

ENGINEERING WINS TIES on purpose. "Data & Analytics", "Tech Ops" and "Engineering - Tools" all
carry an off-domain-looking word next to an engineering one, and in every case the engineering word
is the true one. The cost of the two errors is not symmetric: a wrongly-refused platform role costs
nothing (~240 candidates compete for 5 slots), a wrongly-admitted compliance internship costs a
whole slot AND the time Zineb spends working out it is wrong.

`Product` is deliberately on NEITHER list. Product departments hold engineers and PMs alike, so the
department says nothing there; the title gate already refuses "Product Manager".
"""

from __future__ import annotations

import re

# Departments/functions that mean engineering. Matched as whole words on the board's own label.
_ENGINEERING = re.compile(
    r"\b(engineering|engineer|software|développement|developpement|developer|développeur|"
    r"tech|technical|technology|technologie|informatique|it engineering|"
    r"r\s*&\s*d|rnd|research|recherche|science|data|analytics|"
    r"machine learning|\bml\b|\bai\b|artificial intelligence|"
    r"platform|infrastructure|devops|cloud|architecture)\b", re.I)

# …and the functions that do not. Only fires when nothing above did.
_OFF_DOMAIN = re.compile(
    r"\b(sales|selling|marketing|growth|business development|\bbd\b|revenue|partnerships|"
    r"finance|financial|accounting|comptabilit[ée]|treasury|tax|"
    r"legal|juridique|compliance|conformit[ée]|regulatory|risk|audit|governance|privacy|"
    r"\bhr\b|human resources|ressources humaines|people|talent|recruit|recruiting|staffing|"
    r"customer|client|support|success|service desk|helpdesk|"
    r"communication|content|editorial|brand|creative|press|public relations|"
    r"operations|administration|administrative|office|facilities|procurement|purchasing|"
    r"supply chain|logistics|manufacturing|production industrielle|quality assurance|"
    r"training|education|pedagogy|p[ée]dagogie)\b", re.I)

# France Travail / ROME: the state occupational taxonomy. M18xx is "Systèmes d'information et de
# télécommunication" — the informatique family (M1805 études et développement, M1810 exploitation,
# M1802 conseil SI…). A code is a far harder fact than a job title, and FT puts one on every offer.
_ROME_ENGINEERING = re.compile(r"^M18", re.I)

# The keys a source may fill in JobListing.meta / the digest's offer dict. Free-form by design —
# boards name things differently and only some publish anything, so consumers read what they know.
_LABEL_KEYS = ("function", "department", "team", "rome_label")


def classify(meta: dict | None) -> str:
    """-> "engineering" | "off_domain" | "" (unknown). Never raises: a malformed meta is unknown."""
    if not isinstance(meta, dict):
        return ""

    rome = str(meta.get("rome") or "").strip()
    if rome and _ROME_ENGINEERING.match(rome):
        return "engineering"

    labels = " | ".join(str(meta.get(k) or "").strip() for k in _LABEL_KEYS).strip(" |")
    if not labels.replace("|", "").strip():
        return ""
    if _ENGINEERING.search(labels):
        return "engineering"
    if _OFF_DOMAIN.search(labels):
        return "off_domain"
    return ""


# An "off_domain" department must NOT be able to veto a title that names a core engineering job
# outright. Startups file infrastructure under "Operations" and embed engineers in "Growth" or
# "Business" teams: the live board on 2026-09-15 had a "Backend Software Engineer (Python/DevOps)"
# and a "Full Stack Software Engineer (Python/React)" under Operations, and a "Data Engineer" on a
# Growth team. Those are real engineering jobs whoever the reporting line runs to.
#
# So the department BREAKS TIES; it does not overrule strong evidence. This pattern is what counts
# as strong: a CORE job function, not merely a technical word somewhere in the string. It is
# deliberately a short list of whole phrases — "Business Developer" and "AI Deployment Strategist"
# must not qualify, and both would if a bare "developer" or "AI" were enough.
_CORE_TECHNICAL_TITLE = re.compile(
    r"\b(software|backend|back[- ]end|frontend|front[- ]end|full[- ]?stack|web|mobile|"
    r"data|database|ml|machine learning|deep learning|nlp|computer vision|research|"
    r"devops|mlops|sre|platform|infrastructure|cloud|systems|security|qa|test)\s+"
    r"(engineer|developer|d[ée]veloppeur|scientist|architect)\b"
    r"|\b(software|backend|full[- ]?stack|web|mobile|data)\s+engineering\b"
    r"|\bd[ée]veloppeur[- ]?(se)?\b|\bing[ée]nieur[- ]?e?\s+(logiciel|informatique|d[ée]veloppement)\b",
    re.I)


def is_core_technical_title(title: str) -> bool:
    """True when the title names a core engineering job, not merely a technical-sounding word."""
    return bool(_CORE_TECHNICAL_TITLE.search(title or ""))


def refuses(title: str, meta: dict | None) -> bool:
    """The digest's actual question: should this posting be dropped on the board's own filing?

    True only when the employer files it outside engineering AND the title does not independently
    name a core engineering job. Unknown filings never refuse anything.
    """
    return classify(meta) == "off_domain" and not is_core_technical_title(title)


def label(meta: dict | None) -> str:
    """The board's own words, for the digest's ↳ explanation line. "" when it published none."""
    if not isinstance(meta, dict):
        return ""
    for k in _LABEL_KEYS:
        v = str(meta.get(k) or "").strip()
        if v:
            return v
    return ""


if __name__ == "__main__":  # pragma: no cover - manual inspection
    import json
    import sys
    print(json.dumps({"verdict": classify(json.loads(sys.argv[1])),
                      "label": label(json.loads(sys.argv[1]))}, ensure_ascii=False))
