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
        "headline": r"MLOPS \& PLATFORM ENGINEER",
        "subtitle": r"MLOps \& Infrastructure IA",
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
        "headline": r"MLOPS \& PLATFORM ENGINEER",
        "subtitle": r"MLOps \& AI Platform Engineering",
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
    tmp_tex.write_text(tex, encoding="utf-8")

    # 6. Compile with tectonic
    if not shutil.which("tectonic"):
        raise RuntimeError(
            "tectonic not found. Install it:\n"
            "  macOS:  brew install tectonic\n"
            "  Linux:  cargo install tectonic   (or use the installer at tectonic-typesetting.github.io)"
        )
    result = subprocess.run(
        ["tectonic", "--outdir", str(DOCUMENTS_DIR), str(tmp_tex)],
        capture_output=True,
        text=True,
        cwd=str(DOCUMENTS_DIR),
    )

    tmp_tex.unlink(missing_ok=True)

    if result.returncode != 0:
        print("[cv_builder] tectonic stderr:", result.stderr[-800:], file=sys.stderr)
        raise RuntimeError(f"tectonic failed (exit {result.returncode})")

    # tectonic names output after the stem of the input file
    compiled = DOCUMENTS_DIR / f"_cv_tmp_{lang}.pdf"
    if not compiled.exists():
        raise FileNotFoundError(f"Expected compiled PDF not found: {compiled}")

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
