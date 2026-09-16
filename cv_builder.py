"""
Build a role-adapted CV PDF from the LaTeX source.

Applies targeted substitutions to the .tex file based on the target role focus,
compiles with tectonic, and returns the output PDF path.

Usage:
  python cv_builder.py --lang fr --focus ai
  python cv_builder.py --lang en --focus backend --company "Craft AI"

Output is written to documents/CV_Zineb_Meftah_{LANG}_custom.pdf
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

DOCUMENTS_DIR = Path(__file__).parent / "documents"

# Spacing factors tried in order until the content fits. 1.00 is the designed spacing; much
# below 0.80 the page looks cramped, so we refuse rather than keep shrinking — past that the
# CV genuinely has too much on it and a human has to decide what goes.
FIT_STEPS = (1.00, 0.94, 0.88, 0.82, 0.78)


_CVBLOCK_RE = re.compile(
    r"^% @cvblock (?P<attrs>[^\n]*)\n(?P<body>.*?)^% @endcvblock\n",
    re.S | re.M)


def cv_blocks(tex: str) -> list[dict]:
    """The droppable project/section blocks declared in the .tex, in document order.

    Each is marked `% @cvblock id=… rank=… focus=… [keep=1] … % @endcvblock`, where `focus` lists
    the role focuses the block actually sells and `rank` orders the sacrifice (higher goes first).
    `keep=1` pins a block that must survive any focus — the outreach agent is the flagship and is
    relevant to every role she targets.
    """
    out = []
    for m in _CVBLOCK_RE.finditer(tex):
        attrs = dict(kv.split("=", 1) for kv in m.group("attrs").split() if "=" in kv)
        out.append({
            "id": attrs.get("id", "?"),
            "rank": int(attrs.get("rank", "5")),
            "focus": {f.strip() for f in attrs.get("focus", "").split(",") if f.strip()},
            "keep": attrs.get("keep") == "1",
            "span": (m.start(), m.end()),
        })
    return out


def drop_order(tex: str, focus: str) -> list[dict]:
    """Which blocks to sacrifice first for THIS offer, worst candidate first.

    A CV is read against a specific role, so what to cut depends on the role — Zineb's own
    instruction ("depending on the offer you should drop or shorten"). A block whose `focus` does
    not include the build's focus is cut before one that does; within that, higher `rank` goes
    first. Blocks marked `keep` are never offered up.

    Nothing is dropped unless the page genuinely overflows: this list is the ORDER of last
    resort, not a plan.
    """
    cands = [b for b in cv_blocks(tex) if not b["keep"]]
    return sorted(cands, key=lambda b: (focus in b["focus"], -b["rank"]))


def strip_block(tex: str, block_id: str) -> str:
    """Remove one @cvblock by id, leaving a comment in its place so the CV stays auditable."""
    for m in _CVBLOCK_RE.finditer(tex):
        attrs = dict(kv.split("=", 1) for kv in m.group("attrs").split() if "=" in kv)
        if attrs.get("id") == block_id:
            return (tex[:m.start()]
                    + f"% [cv_builder] '{block_id}' omitted from this build to fit one page\n"
                    + tex[m.end():])
    return tex


def tex_overflow(tectonic_output: str) -> float | None:
    """Points by which the CV's content runs past the space available. None if not reported.

    The main column is a `minipage[t][\\paperheight]` — a fixed-height box that can neither grow
    nor break. LaTeX does not error on overfull content there: it draws the excess BELOW the page
    edge, invisible in every viewer and in print. That is how the Recherche section (her published
    Hugging Face article) came to be missing from every CV attached to a follow-up, while the
    build printed success.

    The .tex boxes the column and reports `CVFIT content=<h>pt available=<a>pt`. We read that
    instead of measuring the PDF because `pdftotext` DROPS text drawn far enough below the page
    edge: it under-reported this very overflow as 18pt against a true 77pt, and its estimate was
    not monotonic in the spacing factor, so an auto-fit built on it would happily converge on a
    CV that still truncates.
    """
    m = None
    for m in re.finditer(r"CVFIT content=([0-9.]+)pt available=([0-9.]+)pt", tectonic_output):
        pass          # keep the LAST report — tectonic typesets more than one pass
    if m is None:
        return None
    return float(m.group(1)) - float(m.group(2))


# ---------------------------------------------------------------------------
# Role-focus profiles
# ---------------------------------------------------------------------------

FOCUS_FR = {
    "ai": {
        "headline": r"IA \& MLOPS ENGINEER",
        "subtitle": r"Ingénieure IA \& Données",
        "search": "une \\textbf{alternance M1} en \\textbf{Ingénierie IA \\& MLOps}",
    },
    "backend": {
        "headline": r"BACKEND \& SOFTWARE ENGINEER",
        "subtitle": r"Génie Logiciel \& IA",
        "search": "une \\textbf{alternance M1} en \\textbf{Développement Backend \\& IA}",
    },
    "mlops": {
        # "DevOps" earns its place here rather than being a keyword stuffed in: French alternance
        # postings in this family are titled "DevOps / MLOps" far more often than "MLOps" alone —
        # GE HealthCare's Buc opening is literally "Alternant·e DevOps / MLOps" — and an ATS
        # screens on the words in the title. It is also accurate: Docker, CI/CD, Git and Linux are
        # already on the skills line. Kubernetes is deliberately NOT added; that is a real gap.
        "headline": r"DEVOPS \& MLOPS ENGINEER",
        "subtitle": r"DevOps, MLOps \& Infrastructure IA",
        "search": "une \\textbf{alternance M1} en \\textbf{MLOps \\& Ingénierie de Plateformes IA}",
    },
    "data": {
        "headline": r"DATA ENGINEER \& IA",
        "subtitle": r"Ingénierie des Données \& IA",
        "search": "une \\textbf{alternance M1} en \\textbf{Data Engineering \\& IA}",
    },
    "fullstack": {
        "headline": r"FULLSTACK \& IA",
        "subtitle": r"Développement Fullstack \& IA",
        "search": "une \\textbf{alternance M1} en \\textbf{Développement Fullstack \\& IA}",
    },
}

FOCUS_EN = {
    "ai": {
        "headline": r"AI \& MLOPS ENGINEER",
        "subtitle": r"AI Engineering \& MLOps",
        "search": "an \\textbf{M1 Apprenticeship} in \\textbf{AI \\& MLOps Engineering}",
    },
    "backend": {
        "headline": r"BACKEND \& SOFTWARE ENGINEER",
        "subtitle": r"Software Engineering \& AI",
        "search": "an \\textbf{M1 Apprenticeship} in \\textbf{Backend \\& AI Engineering}",
    },
    "mlops": {
        "headline": r"DEVOPS \& MLOPS ENGINEER",
        "subtitle": r"DevOps, MLOps \& AI Platform Engineering",
        "search": "an \\textbf{M1 Apprenticeship} in \\textbf{MLOps \\& AI Platform Engineering}",
    },
    "data": {
        "headline": r"DATA ENGINEER \& AI",
        "subtitle": r"Data Engineering \& AI",
        "search": "an \\textbf{M1 Apprenticeship} in \\textbf{Data Engineering \\& AI}",
    },
    "fullstack": {
        "headline": r"FULLSTACK \& AI ENGINEER",
        "subtitle": r"Fullstack Development \& AI",
        "search": "an \\textbf{M1 Apprenticeship} in \\textbf{Fullstack \\& AI Development}",
    },
}


def _detect_focus(role: str) -> str:
    """Infer focus from a role title string."""
    r = (role or "").lower()
    if any(k in r for k in ["mlops", "platform", "sre", "devops", "infra"]):
        return "mlops"
    if any(k in r for k in ["data engineer", "data analyst", "analytics", "données"]):
        return "data"
    if any(k in r for k in ["fullstack", "full stack", "full-stack"]):
        return "fullstack"
    if any(k in r for k in ["backend", "back-end", "software", "api", "django", "fastapi"]):
        return "backend"
    # Default to ai for anything else (AI, ML, alternance générique, etc.)
    return "ai"


def build(
    lang: str = "fr",
    focus: str | None = None,
    role: str | None = None,
    company: str | None = None,
) -> Path:
    """
    Compile an adapted CV PDF.

    Parameters
    ----------
    lang    : 'fr' or 'en'
    focus   : one of ai | backend | mlops | data | fullstack  (auto-detected from role if None)
    role    : raw role title (used for focus detection if focus is None)
    company : company name (currently informational; reserved for future personalisation)

    Returns
    -------
    Path to the compiled PDF.
    """
    lang = (lang or "fr").lower()[:2]
    if focus is None:
        focus = _detect_focus(role or "")

    profiles = FOCUS_FR if lang == "fr" else FOCUS_EN
    profile = profiles.get(focus, profiles["ai"])

    base_tex = DOCUMENTS_DIR / f"CV_Zineb_Meftah_{'FR' if lang == 'fr' else 'EN'}.tex"
    if not base_tex.exists():
        raise FileNotFoundError(f"Base .tex not found: {base_tex}")

    tex = base_tex.read_text(encoding="utf-8")

    # The base .tex files are kept identical to ~/candidature (see
    # project-candidature-docs). Their header block looks like:
    #
    #   {\headfont\fontsize{27pt}{30pt}\selectfont\bfseries ZINEB MEFTAH}\par\vspace{6pt}
    #   {\headfont\color{accent}\fontsize{12.5pt}{16pt}\selectfont\bfseries
    #     AI ENGINEER {\color{gold}$\cdot$} MLOPS {\color{gold}$\cdot$} DEEP LEARNING}\par\vspace{3pt}
    #   {\color{subtitleColor}\fontsize{9.5pt}{13pt}\selectfont\itshape
    #     Autonomous AI systems in production $\cdot$ M1 work-study ...}\par\vspace{4pt}
    #
    # The name line is never touched; we retarget the role line and the first
    # segment of the italic tagline (its availability/location tail is kept).

    subs = 0

    # 1. Role headline (the accent-coloured line under the name)
    tex, n = re.subn(
        r"(\\fontsize\{12\.5pt\}\{16pt\}\\selectfont\\bfseries\s*\n?\s*)(.*?)(\}\\par)",
        lambda m: m.group(1) + profile["headline"] + m.group(3),
        tex,
        count=1,
        flags=re.S,
    )
    subs += n

    # 2. Leading segment of the italic tagline, up to the first "$\cdot$"
    tex, n = re.subn(
        r"(\\fontsize\{9\.5pt\}\{13pt\}\\selectfont\\itshape\s*\n?\s*)(.*?)(\s*\$\\cdot\$)",
        lambda m: m.group(1) + profile["subtitle"] + m.group(3),
        tex,
        count=1,
        flags=re.S,
    )
    subs += n

    if subs < 2:
        print(
            f"[cv_builder] WARNING: only {subs}/2 header substitutions matched — "
            f"the base .tex layout has changed, the CV will not be role-adapted.",
            file=sys.stderr,
        )

    # 5. Write to a temp .tex file in the documents dir (tectonic needs local paths)
    tmp_tex = DOCUMENTS_DIR / f"_cv_tmp_{lang}.tex"

    # 6. Compile with tectonic, retrying at tighter spacing until the content FITS on the page.
    if not shutil.which("tectonic"):
        raise RuntimeError(
            "tectonic not found. Install it:\n"
            "  macOS:  brew install tectonic\n"
            "  Linux:  cargo install tectonic   (or use the installer at tectonic-typesetting.github.io)"
        )

    compiled = DOCUMENTS_DIR / f"_cv_tmp_{lang}.pdf"

    def _compile(source: str, fit: float):
        """Compile `source` at spacing `fit`; return (overflow_pt_or_None, tectonic_result)."""
        tmp_tex.write_text(
            source.replace(r"\providecommand{\cvFit}{1.0}",
                           r"\providecommand{\cvFit}{1.0}" + "\n"
                           + r"\renewcommand{\cvFit}{%s}" % fit, 1),
            encoding="utf-8")
        res = subprocess.run(
            # --print surfaces the \typeout line carrying the measured height.
            ["tectonic", "--print", "--outdir", str(DOCUMENTS_DIR), tmp_tex.name],
            capture_output=True, text=True, cwd=str(DOCUMENTS_DIR),
        )
        if res.returncode != 0:
            tmp_tex.unlink(missing_ok=True)
            print("[cv_builder] tectonic stderr:", res.stderr[-800:], file=sys.stderr)
            raise RuntimeError(f"tectonic failed (exit {res.returncode})")
        if not compiled.exists():
            tmp_tex.unlink(missing_ok=True)
            raise FileNotFoundError(f"Expected compiled PDF not found: {compiled}")
        return tex_overflow(res.stdout + res.stderr), res

    # Fit the page in two stages, cheapest first: tighten the spacing, and only if that is not
    # enough, drop the block that sells THIS role least (Zineb: "depending on the offer you
    # should drop or shorten"). Content is never dropped while spacing alone would do.
    source, dropped, overflow = tex, [], None
    while True:
        for fit in FIT_STEPS:
            overflow, _ = _compile(source, fit)
            if overflow is None or overflow <= 0:
                if dropped:
                    print(f"[cv_builder] omitted for --focus {focus}: {', '.join(dropped)} "
                          f"(would not fit on one page)")
                if overflow is not None and fit != FIT_STEPS[0]:
                    print(f"[cv_builder] tightened spacing to {fit:.2f} to fit the page")
                break
        else:
            nxt = next((b for b in drop_order(source, focus) if b["id"] not in dropped), None)
            if nxt is not None:
                print(f"[cv_builder] still {overflow:.0f}pt over — dropping '{nxt['id']}' "
                      f"(least relevant to --focus {focus})", file=sys.stderr)
                dropped.append(nxt["id"])
                source = strip_block(source, nxt["id"])
                continue
            tmp_tex.unlink(missing_ok=True)
            compiled.unlink(missing_ok=True)
            raise RuntimeError(
                f"CV still overflows by {overflow:.0f}pt (~{overflow / 28.45:.1f}cm) with every "
                f"droppable block removed and spacing at {FIT_STEPS[-1]:.2f}.\n"
                "The main column is a fixed-height minipage, so the excess is drawn BELOW the page "
                "edge and is invisible — still in the PDF text layer, which is why this went "
                "unnoticed while every attached CV was missing its last section.\n"
                "Refusing to ship a truncated CV: shorten a bullet in the .tex and rebuild."
            )
        break

    tmp_tex.unlink(missing_ok=True)

    # Rename to final path
    final = DOCUMENTS_DIR / f"CV_Zineb_Meftah_{'FR' if lang == 'fr' else 'EN'}_custom.pdf"
    shutil.move(str(compiled), str(final))

    print(f"[cv_builder] compiled → {final}")
    return final


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build role-adapted CV PDF from LaTeX source")
    parser.add_argument("--lang", default="fr", choices=["fr", "en"])
    parser.add_argument("--focus", choices=["ai", "backend", "mlops", "data", "fullstack"],
                        help="Role focus (auto-detected from --role if omitted)")
    parser.add_argument("--role", default="", help="Raw role title (used for focus detection)")
    parser.add_argument("--company", default="", help="Company name (informational)")
    args = parser.parse_args(argv)

    try:
        path = build(lang=args.lang, focus=args.focus, role=args.role, company=args.company)
        print(f"✅  {path}")
        return 0
    except Exception as e:
        print(f"❌  {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
