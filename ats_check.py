"""Simuler ce qu'un ATS fait réellement d'un CV — en local, sans rien envoyer dehors.

POURQUOI PAS UN SITE EN LIGNE. Les scanners du marché (Jobscan, Resume Worded…) demandent de
TÉLÉVERSER le CV : nom, téléphone, adresse e-mail partent chez un tiers qui les conserve. Pour
un gain qui est reproductible ici, puisque ce qu'ils calculent tient en quatre questions :
le texte s'extrait-il, les coordonnées se retrouvent-elles, les sections sont-elles reconnues,
et le vocabulaire recouvre-t-il celui de l'annonce.

⚠ DEUX MOTEURS D'EXTRACTION, PAS UN. C'est le cœur du test et ce qu'un contrôle à l'œil ne peut
  pas voir : les ATS n'utilisent pas tous le même analyseur PDF. Un CV peut sortir parfaitement
  avec poppler et mal avec pypdf — une divergence entre les deux est exactement le genre de
  panne qui fait disparaître une candidature sans que personne ne sache pourquoi.

    python ats_check.py documents/CV_Zineb_Meftah_FR_ai.pdf offers/vo2_group_...txt
"""
from __future__ import annotations

import re
import subprocess
import sys
import unicodedata
from pathlib import Path

# Les intitulés qu'un analyseur français ou anglais cherche pour découper le document.
SECTIONS = {
    "profil": r"profil|r[ée]sum[ée]|summary|about|profile",
    "compétences": r"comp[ée]tences|skills|technical skills",
    "expérience": r"exp[ée]rience|experience|parcours professionnel|employment",
    "formation": r"formation|education|dipl[ôo]mes|academic",
    "langues": r"langues|languages",
}
# Les coordonnées, lues comme un ATS les lit : par motif, dans le texte brut.
CONTACT = {
    "e-mail": r"[\w.+-]+@[\w-]+\.[\w.]+",
    "téléphone": r"(?:\+33|0)\s?[1-9](?:[\s.-]?\d{2}){4}",
    "LinkedIn": r"linkedin\.com/in/[\w-]+",
    "GitHub": r"github\.com/[\w-]+",
}
# Mots vides : ils gonflent artificiellement un taux de recouvrement sans rien signifier.
VIDES = set("""au aux avec ce ces dans de des du elle en et eux il ils je la le les leur lui ma
mais me meme mes moi mon ne nos notre nous on ou par pas pour qu que qui sa se ses son sur ta te
tes toi ton tu un une vos votre vous c d j l a m n s t y ete etee etees etes etant suis es est
sommes etes sont serai seras sera serons serez seront the and for with you your our are was were
this that from will can have has had not but all any its their they them these those more most
other such only own same than too very a an of in on to is it be as at by or if we i
nos vous notre chez plus tres bien non oui etc afin ainsi alors apres aussi autre avant beaucoup
cela comme donc encore entre faire fait lors meme moins peu peut plutot puis quand sans selon
sous toujours tout tous toute toutes vers voir deja depuis
aimez-vous avez-vous etes-vous maitrisez-vous est-il sommes-nous qu-est-ce etre avoir
annee annees large eventail domaine domaines cas pointe sujets themes vous nous
https http www com fr min """.split())


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _mots(t: str) -> set[str]:
    return {m for m in re.findall(r"[a-z0-9][a-z0-9+#./-]{1,}", _norm(t))
            if m not in VIDES and len(m) > 2 and not m.isdigit()}


def extraire(pdf: Path) -> dict[str, str]:
    """Le même PDF vu par deux moteurs différents. Un moteur absent est signalé, pas simulé."""
    out = {}
    try:
        out["poppler"] = subprocess.run(["pdftotext", str(pdf), "-"],
                                        capture_output=True, text=True, timeout=60).stdout
    except Exception as e:                                  # noqa: BLE001
        out["poppler"] = ""
        print(f"  ⚠ poppler indisponible : {e}")
    try:
        import pypdf
        out["pypdf"] = "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(str(pdf)).pages)
    except Exception as e:                                  # noqa: BLE001
        out["pypdf"] = ""
        print(f"  ⚠ pypdf indisponible : {e}")
    return out


def audit(pdf: Path, offre: Path | None = None) -> int:
    print(f"\n╔═ {pdf.name}")
    textes = extraire(pdf)
    echecs = 0

    # 1 ── Le texte sort-il, et PAREIL des deux côtés ?
    print("\n── 1. Extraction")
    for moteur, t in textes.items():
        print(f"   {moteur:9} {len(t):6} caractères, {len(t.split()):5} mots")
    a, b = _mots(textes.get("poppler", "")), _mots(textes.get("pypdf", ""))
    if a and b:
        commun = len(a & b) / max(1, len(a | b))
        perdus = sorted(a - b)[:8]
        print(f"   accord entre moteurs : {commun:.0%}")
        if commun < 0.90:
            echecs += 1
            print(f"   ❌ DIVERGENCE — un ATS sur deux lirait autre chose. Exemples vus par "
                  f"poppler et pas par pypdf : {perdus}")
        else:
            print("   ✅ les deux moteurs lisent la même chose")

    principal = textes.get("poppler") or textes.get("pypdf") or ""

    # 2 ── Les coordonnées se retrouvent-elles par motif ?
    print("\n── 2. Coordonnées (lues par motif, comme un ATS)")
    for nom, motif in CONTACT.items():
        trouve = re.findall(motif, principal)
        if trouve:
            print(f"   ✅ {nom:10} {trouve[0]}")
        else:
            echecs += 1
            print(f"   ❌ {nom:10} INTROUVABLE")

    # 3 ── Les sections sont-elles reconnues ?
    print("\n── 3. Sections reconnues")
    # ⚠ LE MOT-CLÉ N'EST PAS TOUJOURS EN TÊTE DE L'INTITULÉ : « PROFESSIONAL EXPERIENCE »
    # commence par « professional ». Exiger le début de ligne faisait échouer la version
    # anglaise sur une section parfaitement présente — un faux négatif du testeur, pas un
    # défaut du CV. On cherche donc le mot DANS une ligne courte isolée, ce qui est la forme
    # d'un intitulé de section.
    entetes = [l for l in _norm(principal).split("\n") if 0 < len(l.strip()) <= 48]
    for nom, motif in SECTIONS.items():
        if any(re.search(rf"\b({motif})\b", l) for l in entetes):
            print(f"   ✅ {nom}")
        else:
            echecs += 1
            print(f"   ❌ {nom} — section non détectée, son contenu sera mal rattaché")

    # 4 ── Les pièges de mise en forme qui cassent la recherche.
    print("\n── 4. Pièges de mise en forme")
    coupes = [l for l in principal.split("\n") if re.search(r"[a-zà-ÿ]-$", l)]
    liga = re.findall(r"[ﬀ-ﬆ]", principal)
    for libelle, mauvais, detail in (
            ("mots coupés par la césure", coupes, coupes[:2]),
            ("ligatures non décomposées", liga, liga[:3])):
        if mauvais:
            echecs += 1
            print(f"   ❌ {libelle} : {len(mauvais)} — {detail}")
        else:
            print(f"   ✅ aucun(e) {libelle}")
    pages = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    m = re.search(r"Pages:\s+(\d+)", pages)
    print(f"   ℹ pages : {m.group(1) if m else '?'}")

    # 5 ── Le recouvrement avec l'annonce : ce que vend un Jobscan.
    if offre and offre.is_file():
        print(f"\n── 5. Recouvrement avec l'annonce ({offre.name})")
        # ⚠ LES LIGNES DE COMMENTAIRE NE SONT PAS L'ANNONCE. Le fichier porte en tête la
        # provenance (URL, date, processus de recrutement) ; les compter ferait chuter le
        # recouvrement sur des mots que l'employeur n'a jamais écrits.
        brut_offre = offre.read_text(encoding="utf-8")
        texte_offre = "\n".join(l for l in brut_offre.split("\n") if not l.lstrip().startswith("#"))
        cv, job = _mots(principal), _mots(texte_offre)
        inter = cv & job
        taux = len(inter) / max(1, len(job))
        print(f"   {len(inter)} termes de l'annonce sur {len(job)} présents dans le CV "
              f"→ {taux:.0%}")
        # LE TAUX BRUT EST UNE INDICATION FAIBLE : une annonce contient surtout de la prose
        # d'entreprise. Ce qui décide, ce sont les compétences qu'elle DÉCLARE. On les lit dans
        # sa ligne « COMPÉTENCES » et dans ses puces de mission, et on les vérifie une par une.
        declarees = []
        for ligne in texte_offre.split("\n"):
            n = _norm(ligne)
            if "competences" in n or "expertises" in n:
                continue
            if "·" in ligne and len(ligne) < 160 and not ligne.startswith("-"):
                declarees += [x.strip() for x in ligne.split("·") if 2 < len(x.strip()) < 40]
        if declarees:
            print("\n   Compétences DÉCLARÉES par l'annonce :")
            for d in declarees:
                # PARTIEL N'EST PAS ABSENT. Une compétence composée (« Travail d'équipe »,
                # « Veille technologique ») peut n'être qu'à moitié présente, et un ATS qui
                # cherche l'un des deux mots la trouvera. Un verdict binaire ferait passer
                # pour un trou ce qui est une correspondance partielle — et inversement.
                termes = _mots(d)
                vus = termes & cv
                if vus == termes:
                    print(f"     {'✅ présente':22} {d}")
                elif vus:
                    manque = " ".join(sorted(termes - vus))
                    print(f"     {'🟡 partielle':22} {d}   (manque : {manque})")
                else:
                    print(f"     {'❌ absente':22} {d}")
        manquants = sorted((job - cv), key=len, reverse=True)
        print(f"\n   Autres termes de l'annonce absents du CV ({len(manquants)}), "
              f"les plus distinctifs d'abord :")
        for i in range(0, min(len(manquants), 40), 8):
            print("     " + " · ".join(manquants[i:i + 8]))
    print(f"\n╚═ {'✅ aucun défaut bloquant' if not echecs else f'❌ {echecs} défaut(s)'}")
    return echecs


if __name__ == "__main__":
    pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("documents/CV_Zineb_Meftah_FR_ai.pdf")
    offre = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    sys.exit(1 if audit(pdf, offre) else 0)
