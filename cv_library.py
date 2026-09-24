"""Les cinq CV prêts, un par famille de métier.

Son idée du 2026-09-24 : au lieu de faire décider un modèle à CHAQUE candidature quels projets
garder et comment tourner les puces — un appel par offre, lent, et dont la qualité varie —, on
fabrique UNE FOIS cinq CV soignés, un par focus, et on les réutilise. Le modèle ne revient sur
le CV que s'il juge qu'une annonce précise mériterait vraiment mieux.

Ce que ça change, mesuré sur les packs du 2026-09-24 : le CV coûtait un plan par candidature
(0 à 4 groupes de puces réécrits selon le modèle, 220 à 350 s) ; il coûte maintenant zéro appel
et zéro seconde dans le cas courant.

⚠ CES CV SONT FAITS POUR UNE FAMILLE, PAS POUR UNE ANNONCE. Le brief de famille décrit ce que
  ce type de poste demande en général — c'est volontairement plus large qu'une offre. Un CV
  taillé sur une annonce unique serait pire sur les vingt suivantes.

⚠ LES MÊMES GARDES QU'AILLEURS s'appliquent : le plan ne peut que CHOISIR et ORDONNER, et
  `cv_builder` refuse tout outil absent de son dossier et tout chiffre absent de la puce
  d'origine. Un CV de bibliothèque ne peut pas contenir ce qu'une candidature ne pourrait pas.

    python cv_library.py build            # (re)construit les cinq
    python cv_library.py build data ai    # seulement ceux-là
    python cv_library.py check            # ce qui existe, et ce que chacun vaut
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv_builder  # noqa: E402

FOCUSES = ("ai", "data", "mlops", "backend", "fullstack")


def path_for(focus: str, lang: str = "fr") -> Path:
    suffix = "FR" if lang == "fr" else "EN"
    return ROOT / "documents" / f"CV_Zineb_Meftah_{suffix}_{focus}.pdf"


def available(lang: str = "fr") -> dict[str, Path]:
    """Les CV prêts qui existent réellement sur le disque."""
    return {f: p for f in FOCUSES if (p := path_for(f, lang)).is_file()}


# Ce que demande CHAQUE FAMILLE d'annonces, écrit à partir des offres réelles qu'elle reçoit.
# Ce texte tient lieu d'annonce pour le plan : plus large qu'une offre, mais de même nature.
FAMILY_BRIEFS = {
    "ai": """Alternance Ingénieur·e IA / Machine Learning. L'employeur cherche quelqu'un qui
construit des systèmes à base de modèles de langage et les met en état de fonctionner : RAG,
agents, évaluation de la qualité des réponses, traçabilité vers la source, choix entre une
solution sur étagère et un développement propre, maîtrise du coût d'inférence. Python, PyTorch,
Transformers, embeddings, bases vectorielles. On attend une personne capable de passer d'un
prototype à quelque chose qu'une équipe utilise, et de défendre ses arbitrages techniques.""",
    "data": """Alternance Data Analyst / Data Engineer. L'employeur cherche quelqu'un qui rend
la donnée utilisable et lisible : modèle relationnel, SQL, préparation et exploration en Python,
qualité et fiabilité des indicateurs publiés, restitution à des équipes métier qui n'écrivent
pas de code, documentation du sens des chiffres. Pandas, NumPy, SQL, visualisation. On attend
de la rigueur sur ce qui est mesuré et la capacité à défendre un indicateur devant celui qui
s'en sert pour décider.""",
    "mlops": """Alternance DevOps / MLOps. L'employeur cherche quelqu'un qui fait tourner des
systèmes en continu : conteneurisation, intégration et déploiement continus, supervision,
sondes, alertes, seuils, journalisation, automatisation des tâches répétitives, Linux, cloud.
On attend quelqu'un qui pense à la maintenance et au coût d'exploitation autant qu'à la
fonctionnalité, et qui sait diagnostiquer une panne à partir de ce qu'il a instrumenté.""",
    "backend": """Alternance Développeur·se Backend. L'employeur cherche quelqu'un qui conçoit
et tient des services : API, modèle de données, tests, performance, architecture, qualité du
code, revue, systèmes distribués. Python, Java, C/C++, SQL, Flask, Docker, Git. On attend une
personne à l'aise avec l'algorithmique et les structures de données, capable de justifier une
conception et d'en mesurer les effets.""",
    "fullstack": """Alternance Développeur·se Fullstack. L'employeur cherche quelqu'un qui livre
une fonctionnalité de bout en bout : interface, service, base de données, mise en ligne. Flask,
Node.js, Next.js, JavaScript, SQL, Docker. On attend de l'autonomie sur toute la chaîne, le
souci de ce que voit l'utilisateur, et la capacité à livrer vite une première version puis à la
corriger sur les usages.""",
}


def build(focus: str, lang: str = "fr", verbose: bool = True) -> Path:
    """Construit LE CV de cette famille. Le plan vient du modèle par défaut (Opus) : c'est un
    travail fait une fois et réutilisé des dizaines de fois, la qualité prime sur le coût."""
    import webui.claude_bridge as bridge

    base = (ROOT / "documents" /
            f"CV_Zineb_Meftah_{'FR' if lang == 'fr' else 'EN'}.tex").read_text(encoding="utf-8")
    blocks = cv_builder.cv_blocks(base)
    for b in blocks:
        seg = "\n".join(l for l in base[b["span"][0]:b["span"][1]].split("\n")
                        if not l.lstrip().startswith("% @"))
        b["title"] = " ".join(cv_builder.strip_latex(seg).split())[:170]
    skills = [cv_builder.strip_latex(r)
              for r in cv_builder._SKILLS_RE.search(base).group("body").split("\\\\\n") if r.strip()]
    prof = cv_builder._PROFIL_RE.search(base).group("body").strip()

    brief = FAMILY_BRIEFS[focus]
    offer = {"company": "", "role": f"Alternance — famille {focus}", "location": "Île-de-France"}
    ok, plan = bridge.plan_cv(offer, brief, blocks, skills, prof,
                              bullets=cv_builder.bullet_groups(base), facts={})
    if not ok:
        plan = {}
        if verbose:
            print(f"[{focus}] plan indisponible — construction déterministe")
    elif verbose:
        print(f"[{focus}] {plan.get('why', '')[:120]}")

    # Le focus est IMPOSÉ ici : c'est la définition même du CV qu'on fabrique.
    plan.pop("focus", None)
    out = cv_builder.build(lang=lang, focus=focus, role=f"Alternance {focus}",
                           offer_text=brief, plan=plan)
    dest = path_for(focus, lang)
    dest.write_bytes(Path(out).read_bytes())
    if verbose:
        print(f"[{focus}] → {dest.relative_to(ROOT)}")
    return dest


def inspect(focus: str, lang: str = "fr") -> dict:
    """Ce que vaut un CV prêt, lu comme un analyseur le lit."""
    p = path_for(focus, lang)
    if not p.is_file():
        return {"focus": focus, "existe": False}
    txt = subprocess.run(["pdftotext", str(p), "-"], capture_output=True, text=True).stdout
    info = subprocess.run(["pdfinfo", str(p)], capture_output=True, text=True).stdout
    import re
    lignes = [x for x in txt.split("\n") if x.strip()]
    return {
        "focus": focus, "existe": True,
        "pages": int(re.search(r"Pages:\s+(\d+)", info).group(1)) if "Pages:" in info else 0,
        "ligatures": len(re.findall(r"[ﬀ-ﬆ]", txt)),
        "titre": lignes[1] if len(lignes) > 1 else "",
        "sections": [s for s in ("PROFIL", "COMPÉTENCES", "FORMATION", "EXPÉRIENCE") if s in txt],
        "liens": [w for w in ("you@example.com", "linkedin.com/in/zinebmeftah",
                              "github.com/ZinebMEFTAH", "huggingface.co/zino36") if w in txt],
    }


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "check"
    cibles = [a for a in argv[1:] if a in FOCUSES] or list(FOCUSES)
    if cmd == "build":
        for f in cibles:
            build(f)
        return 0
    for f in FOCUSES:
        d = inspect(f)
        if not d["existe"]:
            print(f"  {f:<10} ABSENT")
            continue
        ok = d["pages"] == 1 and not d["ligatures"] and len(d["sections"]) == 4 and len(d["liens"]) == 4
        print(f"  {'✓' if ok else '⚠'} {f:<10} {d['pages']}p · {d['ligatures']} ligature · "
              f"{len(d['sections'])}/4 sections · {len(d['liens'])}/4 liens · {d['titre'][:44]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
