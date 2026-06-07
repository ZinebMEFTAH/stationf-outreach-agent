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
import tempfile
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

    # 1. Update the main title headline (the large ZINEB MEFTAH line is kept;
    #    we update the role subtitle below it)
    tex = re.sub(
        r"(\\fontsize\{18pt\}\{30pt\}\\selectfont\\bfseries )([^\n\\}]+)",
        lambda m: m.group(1) + profile["headline"],
        tex,
        count=1,
    )

    # 2. Update the smaller subtitle line (second \\fontsize after the headline)
    tex = re.sub(
        r"(\\fontsize\{16pt\}\{30pt\}\\selectfont )([^\n\\}]+)",
        lambda m: m.group(1) + profile["subtitle"],
        tex,
        count=1,
    )

    # 3. Update the "je recherche une alternance en X" in the profile paragraph
    #    Handles both French and English base files
    tex = re.sub(
        r"je recherche une\\s+\\\\textbf\{alternance\}[^.]*\.",
        profile["search"] + ".",
        tex,
    )
    # Simpler fallback: replace the alternance target string directly
    if "Génie Logiciel ou Développement Fullstack" in tex:
        tex = tex.replace(
            "\\textbf{alternance}\n"
            "en \\textbf{Génie Logiciel ou Développement Fullstack}",
            profile["search"],
        )
    elif "alternance}" in tex and "Génie Logiciel" in tex:
        tex = re.sub(
            r"alternance\}\s*\n\s*en\\s+\\textbf\{[^}]+\}",
            "alternance} en " + profile["search"].split("en ", 1)[-1],
            tex,
        )

    # 4. Fix internship status: "prévu" → "en cours"
    tex = tex.replace("Stagiaire IA \\& MLOps \\textit{(prévu)}", "Stagiaire IA \\& MLOps \\textit{(en cours)}")
    tex = tex.replace("(prévu)", "(en cours)")

    # 5. Write to a temp .tex file in the documents dir (tectonic needs local paths)
    tmp_tex = DOCUMENTS_DIR / f"_cv_tmp_{lang}.tex"
    tmp_tex.write_text(tex, encoding="utf-8")

    # 6. Compile with tectonic
    import shutil
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
