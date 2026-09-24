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
import json
import re
import shutil
import unicodedata
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


# ---------------------------------------------------------------------------
# Offer-aware selection
# ---------------------------------------------------------------------------
# Zineb's standing instruction, 2026-09-19: everything chosen for a CV or a letter — which
# projects survive, which skills lead — exists to raise her chances on THAT offer.
#
# The boundary that makes this honest: this SELECTS AND ORDERS content she already has. It never
# adds a skill to match a posting. If a posting wants Spark and she has never used Spark, the
# right move is the one the AP-HP letter makes — say so — not to surface a keyword she cannot
# defend in the interview.
#
# Deterministic on purpose: no LLM call, so /daily-agent can rebuild an offer-tailored CV for a
# follow-up attachment at 23:00 on the VM without spending her Claude quota.

# Words that carry no signal about a job's content. Kept short: over-filtering costs recall, and
# a stray common word scores the same against every block so it cannot change the ranking.
_OFFER_STOP = set("""
the and for with you your our that this from are will have has les des une des aux par pour
vous nous notre votre dans sur est sont avec chez plus tous tout leur ses son sa de la le du
au en un et ou où qui que quoi dont ainsi afin entre sera seront été être avoir fait faire
poste offre mission missions profil equipe équipe travail entreprise société groupe stage
alternance alternant alternante apprenti apprentissage contrat mois ans année annee recherche
recherchons candidat candidate candidature h/f f/h paris france ile idf
""".split())


def offer_keywords(text: str) -> set[str]:
    """Technical vocabulary of a posting, lowercased and de-accented, for overlap scoring."""
    if not text:
        return set()
    flat = unicodedata.normalize("NFKD", text.lower())
    flat = "".join(c for c in flat if not unicodedata.combining(c))
    toks = re.findall(r"[a-z0-9][a-z0-9+#.]{2,}", flat)
    return {t.strip(".") for t in toks if t.strip(".") not in _OFFER_STOP and len(t.strip(".")) > 2}


def strip_latex(src: str) -> str:
    """Prose from LaTeX source: commands and syntax removed, words kept, for keyword matching."""
    out = re.sub(r"\\[a-zA-Z]+\s*", " ", src)      # \cvItem, \textbf, ...
    return re.sub(r"[{}$~\\]", " ", out)


def _block_text(tex: str, block: dict) -> str:
    """The prose of one @cvblock. cv_blocks() reports a `span`, not a body, so slice it."""
    a, b = block["span"]
    return strip_latex(tex[a:b])


def relevance(tex: str, block: dict, keywords: set[str]) -> int:
    """How many of the offer's distinct keywords this block actually evidences."""
    if not keywords:
        return 0
    return len(keywords & offer_keywords(_block_text(tex, block)))



_SKILLS_RE = re.compile(r"^% @skills\n(?P<body>.*?)^% @endskills\n", re.S | re.M)


def reorder_skills(tex: str, keywords: set[str]) -> tuple[str, list[str]]:
    """Put the skill rows this employer asked about FIRST. Content is never changed.

    A recruiter reads the first line of a skills block and an ATS matches the whole of it, so the
    order costs nothing and the top line is free signal. Rows are the ones already written in the
    .tex between `% @skills` and `% @endskills`, one per line; this only sorts them, so nothing
    can be claimed here that she cannot defend.
    """
    m = _SKILLS_RE.search(tex)
    if not m or not keywords:
        return tex, []
    rows = [r for r in m.group("body").split("\\\\\n") if r.strip()]
    scored = sorted(rows, key=lambda r: -len(keywords & offer_keywords(strip_latex(r))))
    if scored == rows:
        return tex, []
    lead = strip_latex(scored[0]).split()[:2]
    return (tex[:m.start()] + "% @skills\n" + "\\\\\n".join(scored) + "\n% @endskills\n"
            + tex[m.end():]), lead


def drop_order(tex: str, focus: str, keywords: set[str] | None = None) -> list[dict]:
    """Which blocks to sacrifice first for THIS offer, worst candidate first.

    A CV is read against a specific role, so what to cut depends on the role — Zineb's own
    instruction ("depending on the offer you should drop or shorten"). Blocks marked `keep` are
    never offered up.

    When the posting's own text is available (`keywords`), a block that evidences more of what
    THIS employer asked for survives longer than one that does not. It is deliberately a
    TIEBREAK INSIDE the focus bucket, not a replacement for it: measured against real postings the
    overlap is only 0-2 keywords per block — the CV blocks are short, and French postings and an
    English-leaning stack rarely share vocabulary — so treating that as a stronger signal than the
    hand-declared `focus` would be over-reading noise. Focus first, then offer relevance, then
    `rank`. With no keywords the behaviour is exactly as before.

    Nothing is dropped unless the page genuinely overflows: this list is the ORDER of last
    resort, not a plan.
    """
    cands = [b for b in cv_blocks(tex) if not b["keep"]]
    kw = keywords or set()
    return sorted(cands, key=lambda b: (focus in b["focus"],
                                        relevance(tex, b, kw),
                                        -b["rank"]))



# ── Le plan du modèle ────────────────────────────────────────────────────────────────────
# Le CV était choisi par des règles : bucket `--focus`, puis chevauchement de mots-clés, puis
# `rank`. Mesuré sur de vraies annonces, ce chevauchement vaut 0 à 2 mots par bloc — les blocs
# sont courts et une annonce française partage peu de vocabulaire avec une stack anglophone.
# Un modèle LIT l'annonce et comprend qu'un poste « gouvernance des données » appelle le travail
# de corpus chez GE plutôt que le compilateur, ce qu'aucun compte de mots-clés ne dira.
#
# ⚠ IL CHOISIT, IL N'INVENTE PAS. Le plan ne porte que des ORDRES et des IDENTIFIANTS existants ;
#   le seul texte libre est le paragraphe de profil, et chaque terme technique qu'il contient
#   doit déjà figurer dans le CV ou dans about_me.txt, sinon le paragraphe d'origine est gardé.
#   C'est la même règle que pour la lettre : sélectionner et ordonner, jamais ajouter.

_PROFIL_RE = re.compile(r"(?P<head>% @profil\s*\n)(?P<body>.*?)(?P<tail>\n% @endprofil)", re.S)

# Un terme « technique » : acronyme, mot capitalisé en milieu de phrase, ou token à chiffres/
# ponctuation de code. C'est là que se logerait une invention (Kubernetes, Spark, Databricks).
_TECHY = re.compile(r"\b([A-Z][A-Za-z0-9+#.]{1,}|[A-Za-z]+[0-9+#][A-Za-z0-9+#.]*)\b")


# about_me.txt NOMME ces outils — pour interdire de les revendiquer (« pas de Kubernetes, pas de
# Terraform, pas de Databricks, pas de Django »). Les chercher dans le fichier les faisait donc
# entrer dans le vocabulaire AUTORISÉ, exactement à l'envers de ce que la consigne dit.
# « tableau » est retiré ICI AUSSI : c'est d'abord le mot français de « tableau de bord », et
# le garder faisait refuser une phrase parfaitement vraie sur ses restitutions. Le produit BI du
# même nom reste couvert par la consigne du prompt, qui interdit de revendiquer un outil absent
# de son dossier — un faux refus, lui, coûte à chaque génération.
_NEVER_CLAIM = {"kubernetes", "k8s", "terraform", "databricks", "django", "spark", "airflow",
                "snowflake", "kafka", "hadoop", "power bi", "powerbi", "sap"}


# Les outils qu'un recruteur data/IA reconnaît et vérifiera. Un terme d'ici absent de son
# dossier est une invention ; un mot français ordinaire n'y figure pas et passe sans bruit.
#
# ⚠ CERTAINS NOMS D'OUTILS SONT AUSSI DES MOTS FRANÇAIS COURANTS, et les garder ici coûte plus
#   cher que le risque qu'ils couvrent. Retirés après une vraie génération (2026-09-23) où
#   « tableau » a fait refuser un profil ET un groupe de puces parlant de TABLEAUX DE BORD —
#   c'est-à-dire précisément le vocabulaire Data Analyst qu'on cherche à reprendre. Même raison
#   pour « sas » (forme juridique omniprésente dans les annonces), « lambda » (« un utilisateur
#   lambda »), « julia » (un prénom), « go » (gigaoctet) et « r » (une lettre). C'est la même
#   famille de pièges que CLAUDE.md documente pour « agent de maîtrise » et « but ».
_TECH_LEXICON = {
    "python", "java", "javascript", "typescript", "scala", "rust", "golang", "c++", "c#",
    "php", "ruby", "perl", "matlab", "sql", "nosql", "bash", "shell",
    "pytorch", "tensorflow", "keras", "jax", "scikit-learn", "sklearn", "xgboost", "lightgbm",
    "pandas", "numpy", "scipy", "matplotlib", "seaborn", "plotly", "dask", "polars",
    "spark", "pyspark", "hadoop", "hive", "kafka", "flink", "airflow", "dagster", "dbt",
    "snowflake", "databricks", "redshift", "bigquery", "synapse", "teradata",
    "postgres", "postgresql", "mysql", "oracle", "mongodb", "cassandra", "redis", "elasticsearch",
    "docker", "kubernetes", "k8s", "terraform", "ansible", "jenkins", "gitlab", "github",
    "aws", "gcp", "azure", "ec2", "s3", "sagemaker", "vertex",
    "mlflow", "kubeflow", "wandb", "dvc", "bentoml", "ray",
    "flask", "fastapi", "django", "spring", "node.js", "nodejs", "next.js", "nextjs", "react",
    "angular", "vue", "svelte",
    "powerbi", "power bi", "qlik", "looker", "superset", "metabase", "sap", "excel",
    "rag", "llm", "langchain", "llamaindex", "huggingface", "transformers", "openai", "claude",
    "mistral", "bm25", "faiss", "pinecone", "weaviate", "chroma", "qdrant", "milvus",
    "nlp", "mlops", "devops", "ci/cd", "git", "linux", "grafana", "prometheus", "kibana",
}

# about_me.txt NOMME certains de ces outils — pour INTERDIRE de les revendiquer (« pas de
# Kubernetes, pas de Terraform, pas de Databricks, pas de Django »). Les chercher dans le
# fichier les faisait entrer dans le vocabulaire AUTORISÉ, exactement à l'envers de la consigne.
def allowed_vocabulary(tex: str) -> set[str]:
    """Tout ce qu'elle peut dire d'elle-même : le CV lui-même plus son dossier."""
    # LE TRAIT D'UNION ET LA BARRE FONT PARTIE DU MOT. Sans eux, « Scikit-learn » — qui est
    # écrit noir sur blanc dans son bloc de compétences — se coupait en « scikit » et « learn »,
    # et la recherche de « scikit-learn » échouait : le garde refusait un profil citant une
    # compétence qu'elle a vraiment. Même effet sur fine-tuning, ci/cd, node.js, next.js.
    # Elle l'a vu avant moi (2026-09-23).
    mot = r"[A-Za-zÀ-ÿ0-9+#./-]+"
    words = set(re.findall(mot, strip_latex(tex).lower()))
    try:
        words |= set(re.findall(mot,
                                (DOCUMENTS_DIR.parent / "about_me.txt").read_text(encoding="utf-8").lower()))
    except Exception:
        pass
    # …et les morceaux séparément, pour que « Scikit-learn » autorise aussi « scikit ».
    words |= {p for w in list(words) for p in re.split(r"[-/]", w) if p}
    return words - _NEVER_CLAIM


def invented_terms(text: str, vocab: set[str]) -> list[str]:
    """Les TECHNOLOGIES du texte proposé qui ne sont nulle part dans son dossier.

    La première version testait la FORME du mot — majuscule, chiffre, ponctuation de code — et
    exigeait que tout terme « technique » figure dans son dossier. Rien ne distingue
    structurellement « Databricks » de « Chez », et son dossier est en anglais : le premier
    profil réellement proposé a été refusé pour le mot « Chez ». Un refus garde le paragraphe
    d'origine, donc c'était sûr, mais ça rendait la personnalisation inutilisable.

    C'est donc un LEXIQUE d'outils, pas une heuristique : un mot français ordinaire n'y est pas
    et passe, une technologie qu'elle n'a jamais utilisée y est et bloque. Le risque résiduel
    est un outil obscur absent du lexique ; le prompt l'interdit par ailleurs, et ce sont les
    outils COURANTS qu'un recruteur vérifiera en entretien.
    """
    out = []
    # La ponctuation devient de l'espace AVANT la recherche : « Power BI, partagé » ne se
    # terminait pas par un espace, donc la recherche de « power bi » entourée d'espaces
    # échouait et laissait passer précisément l'outil qu'elle n'a jamais ouvert.
    low_all = " " + re.sub(r"[^a-z0-9à-ÿ+#./-]+", " ", (text or "").lower()).strip() + " "
    # « Power BI » est DEUX tokens : un découpage en mots ne peut pas le voir, et c'est
    # précisément l'outil que l'annonce Air France demandait et qu'elle n'a jamais utilisé.
    for phrase in (t for t in (_NEVER_CLAIM | _TECH_LEXICON) if " " in t):
        if f" {phrase} " in low_all and phrase not in vocab:
            out.append(phrase)
    for m in re.finditer(r"[A-Za-zÀ-ÿ][A-Za-z0-9+#./-]*", text or ""):
        tok = m.group(0)
        low = tok.lower().strip(".")
        if low in _NEVER_CLAIM:
            out.append(tok)
        elif low in _TECH_LEXICON and low not in vocab:
            out.append(tok)
    return sorted(set(out))

# La police d'en-tête n'a pas de glyphe pour les séparateurs typographiques : un « · » proposé
# par le plan est sorti en « ů » sur le premier CV produit, en tête de page. Le dépôt documente
# déjà le piège (« un tiret cadratin est silencieusement supprimé — sépare avec
# {\color{gold}$\cdot$} »), mais rien ne l'appliquait à un en-tête venu du modèle.
_HEAD_SEP = re.compile(r"\s*[·•|–—/]+\s*")


def clean_headline(h: str) -> str:
    """Un en-tête sûr à injecter tel quel dans le LaTeX."""
    h = _HEAD_SEP.sub(r" {\\color{gold}$\\cdot$} ", (h or "").strip())
    h = h.replace("&", r"\&") if r"\&" not in h else h
    h = re.sub(r"[^\w\s\\{}$&+#.'-]", "", h, flags=re.UNICODE)
    return re.sub(r"\s{2,}", " ", h).strip()


def apply_profil(tex: str, profil: str) -> tuple[str, str]:
    """Remplace le paragraphe de profil. Renvoie (tex, motif de refus ou '')."""
    m = _PROFIL_RE.search(tex)
    if not m:
        return tex, "aucun repère @profil dans le modèle LaTeX"
    txt = (profil or "").strip()
    if not (60 <= len(txt) <= 700):
        return tex, f"longueur inattendue ({len(txt)} caractères)"
    bad = invented_terms(strip_latex(txt), allowed_vocabulary(tex))
    if bad:
        return tex, "termes absents de son dossier : " + ", ".join(bad[:6])
    return tex[:m.start("body")] + txt + tex[m.end("body"):], ""


_BULLETS_RE = re.compile(r"% @bullets id=(?P<id>[\w-]+)\n(?P<body>.*?)\n% @endbullets", re.S)
_ITEM_RE = re.compile(r"\\cvItem\{", re.S)
_NUM_RE = re.compile(r"\d[\d\s.,/%-]*")


def bullet_groups(tex: str) -> dict[str, list[str]]:
    """Les puces actuelles, par groupe. C'est ce qu'on donne au modèle à réécrire."""
    out = {}
    for m in _BULLETS_RE.finditer(tex):
        out[m.group("id")] = _split_items(m.group("body"))
    return out


def _split_items(body: str) -> list[str]:
    r"""Le contenu de chaque \cvItem{...}, accolades équilibrées."""
    items, i = [], 0
    while True:
        m = _ITEM_RE.search(body, i)
        if not m:
            return items
        depth, j = 1, m.end()
        while j < len(body) and depth:
            depth += (body[j] == "{") - (body[j] == "}")
            j += 1
        items.append(body[m.end():j - 1])
        i = j


def _numbers(text: str) -> set[str]:
    return {re.sub(r"[\s.,]", "", n) for n in _NUM_RE.findall(text or "") if re.sub(r"\D", "", n)}


def apply_bullets(tex: str, proposals: dict, vocab: set[str] | None = None) -> tuple[str, list[str]]:
    """Réécrit les puces, groupe par groupe. Renvoie (tex, journal des refus).

    ⚠ DEUX GARDES, ET LE SECOND EST LE PLUS IMPORTANT.
      · Aucune technologie absente de son dossier (même lexique que le profil).
      · AUCUN CHIFFRE QUI N'EST PAS DÉJÀ DANS LA PUCE D'ORIGINE. La méthode XYZ — « j'ai
        accompli X, mesuré par Y, en faisant Z » — pousse à quantifier, et quantifier pousse
        à inventer : « réduit de 40 % », « sur 3 To de données ». Un chiffre faux sur un CV
        est vérifiable en entretien et coûte la candidature. Les chiffres se REPRENNENT, ils
        ne se produisent pas.
      · Jamais PLUS de puces qu'à l'origine, et pas plus de 15 % de texte en plus : la page
        est déjà juste, et l'ajustement automatique paierait la différence en sacrifiant un
        projet.
    Un groupe qui échoue garde ses puces d'origine — les autres passent quand même.
    """
    vocab = vocab if vocab is not None else allowed_vocabulary(tex)
    journal = []
    for gid, items in (proposals or {}).items():
        m = next((x for x in _BULLETS_RE.finditer(tex) if x.group("id") == gid), None)
        if not m:
            journal.append(f"{gid} : groupe inconnu")
            continue
        avant = _split_items(m.group("body"))
        # UNE CHAÎNE EST UNE PUCE, PAS UNE LISTE DE CARACTÈRES. Le modèle rend parfois
        # "bullets": {"outreach-agent": "texte"} au lieu d'une liste d'un élément ; itérer
        # dessus donnait 284 puces et le groupe était refusé — une réponse valide jetée pour
        # sa forme. Vu le 2026-09-24 en construisant le CV fullstack.
        if isinstance(items, str):
            items = [items]
        items = [str(x).strip() for x in (items or []) if str(x).strip()]
        if not items or len(items) > len(avant):
            journal.append(f"{gid} : {len(items)} puce(s) pour {len(avant)} — refusé")
            continue
        plats = [strip_latex(x) for x in items]
        bad = sorted({t for x in plats for t in invented_terms(x, vocab)})
        if bad:
            journal.append(f"{gid} : outils absents de son dossier — {', '.join(bad[:4])}")
            continue
        connus = _numbers(" ".join(avant)) | _numbers(" ".join(
            (DOCUMENTS_DIR.parent / "about_me.txt").read_text(encoding="utf-8")
            if (DOCUMENTS_DIR.parent / "about_me.txt").exists() else ""))
        inventes = sorted(_numbers(" ".join(plats)) - connus)
        if inventes:
            journal.append(f"{gid} : chiffres inventés — {', '.join(inventes[:4])}")
            continue
        if len(" ".join(plats)) > len(" ".join(strip_latex(x) for x in avant)) * 1.30 + 40:
            journal.append(f"{gid} : trop long, la page ne tiendrait pas")
            continue
        corps = "\n".join("      \\cvItem{" + x + "}" for x in items)
        tex = tex[:m.start("body")] + "    \\cvBullets{\n" + corps + "\n    }" + tex[m.end("body"):]
    return tex, journal


def apply_skill_order(tex: str, order: list[str]) -> tuple[str, list[str]]:
    """Remonte les lignes de compétences nommées, dans l'ordre donné. Le CONTENU ne bouge pas."""
    m = _SKILLS_RE.search(tex)
    if not m or not order:
        return tex, []
    rows = [r for r in m.group("body").split("\\\\\n") if r.strip()]
    def rank(row):
        lab = strip_latex(row).lower()
        for i, want in enumerate(order):
            if want and want.strip().lower()[:10] in lab[:60]:
                return i
        return len(order) + rows.index(row)
    scored = sorted(rows, key=rank)
    if scored == rows:
        return tex, []
    return (tex[:m.start()] + "% @skills\n" + "\\\\\n".join(scored) + "\n% @endskills\n"
            + tex[m.end():]), strip_latex(scored[0]).split()[:2]


def plan_drop_order(tex: str, sacrifice: list[str], focus: str,
                    keywords: set[str] | None = None) -> list[dict]:
    """L'ordre de sacrifice voulu par le modèle, complété par l'ordre déterministe.

    Un identifiant inconnu est ignoré et un bloc `keep` reste intouchable : un plan mal formé
    ne peut donc que réordonner, jamais faire disparaître le flagship ni inventer un bloc. Ce
    qu'il ne cite pas garde l'ordre calculé par le code, donc la liste reste complète même si
    le modèle n'en nomme qu'un.
    """
    fallback = drop_order(tex, focus, keywords)
    by_id = {b["id"]: b for b in fallback}
    out = [by_id[i] for i in (sacrifice or []) if i in by_id]
    out += [b for b in fallback if b not in out]
    return out


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

# ---------------------------------------------------------------------------
# Alternance rhythm — ALWAYS on the CV
# ---------------------------------------------------------------------------
# Zineb's standing instruction, 2026-09-19: "please indicate the votre rythme
# d'alternance (which is three days at university, two at company), always do in the cv".
# Postings ask for it outright — Crédit Agricole CIB réf. 2026-110677 says "indiquez dans le
# titre de votre CV votre rythme d'alternance et votre formation" — and a recruiter who cannot
# see the rhythm cannot check it against the team's own needs, so the application stalls on a
# question that never had to be asked. Appended to the FINAL subtitle, after any --subtitle
# override, so neither a focus preset nor a per-employer override can silently drop it.
# The base .tex header offers all three contract types at once. On an ALTERNANCE application that
# reads as shopping around, and CLAUDE.md's contract-ask rule already says never to list all three —
# the agent leads with whatever fits the posting. Stripped for --contract alternance (the default),
# which also buys back a line of header and, at 1pt over, a whole project block.
# Zineb's call, 2026-09-19.
CONTRACT_MENU_FR = r"$\cdot$ CDI, CDD ou alternance"
CONTRACT_MENU_EN = r"$\cdot$ Permanent, fixed-term or apprenticeship"

# ⚠ THE SECOND HALF OF THE RHYTHM IS A SELLING POINT, AND IT WAS MISSING (2026-09-20). The
# official fiche de formation (`Fiche_Formation_M12 MLSD_20260116.pdf`) states the M1 rhythm as
# "3 jours en formation / 2 jours en entreprise JUSQU'À MARS", then "TEMPS COMPLET EN ENTREPRISE
# À PARTIR D'AVRIL". The CV only ever said the 3j/2j part, which understates what the employer
# actually gets: a full-time engineer for the back half of the year. Recruiters weigh exactly
# that when they compare an alternant against an intern.
RHYTHM_FR = r"3j université / 2j entreprise, puis temps plein en entreprise dès avril"
RHYTHM_EN = r"3 days university / 2 days on site, then full-time on site from April"

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
    headline: str | None = None,
    subtitle: str | None = None,
    contract: str = "alternance",
    offer_text: str | None = None,
    plan: dict | None = None,
) -> Path:
    """
    Compile an adapted CV PDF.

    Parameters
    ----------
    lang    : 'fr' or 'en'
    focus   : one of ai | backend | mlops | data | fullstack  (auto-detected from role if None)
    role    : raw role title (used for focus detection if focus is None)
    company : company name (currently informational; reserved for future personalisation)
    headline: overrides the focus preset's role line, to match a posting's OWN title
    subtitle: overrides the focus preset's italic tagline
    contract: "alternance" (default) drops the "CDI, CDD ou alternance" menu from the header;
              "any" keeps it, for a CDI/CDD application where staying open is worth the line
    offer_text: the posting's own words. Drives WHICH projects survive and which skill rows lead,
              so the CV is selected for this employer rather than for a five-way focus bucket.
              Selection only — nothing is ever added to match a posting.

    Returns
    -------
    Path to the compiled PDF.
    """
    lang = (lang or "fr").lower()[:2]
    if focus is None:
        focus = _detect_focus(role or "")

    profiles = FOCUS_FR if lang == "fr" else FOCUS_EN
    profile = dict(profiles.get(focus, profiles["ai"]))
    # A focus preset names a JOB FAMILY; a posting names a JOB. They are often not the same word,
    # and the headline is the first line any screener reads. Printemps advertises "Data Scientist"
    # while --focus data renders "DATA ENGINEER & IA" — a different family, on an application
    # where the title is the strongest keyword there is. The override exists so a CV can carry the
    # posting's own words without inventing a focus preset per employer. The BLOCK SELECTION still
    # follows --focus: this changes what the CV is called, never what it claims.
    if not headline and (plan or {}).get("headline"):
        headline = clean_headline(plan["headline"])
    if not subtitle and (plan or {}).get("subtitle"):
        subtitle = plan["subtitle"]
    if headline:
        profile["headline"] = headline
    if subtitle:
        profile["subtitle"] = subtitle

    # The rhythm goes on every CV — see RHYTHM_FR above. Checked by content rather than by
    # equality so an explicit --subtitle that already spells it out is not made to say it twice.
    if "3j" not in profile["subtitle"] and "3 days" not in profile["subtitle"]:
        profile["subtitle"] += (r" {\color{gold}$\cdot$} "
                                + (RHYTHM_FR if lang == "fr" else RHYTHM_EN))

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

    # 3. Contract menu — see CONTRACT_MENU_FR above.
    if (contract or "alternance").lower() != "any":
        menu = CONTRACT_MENU_FR if lang == "fr" else CONTRACT_MENU_EN
        if menu in tex:
            tex = tex.replace(" " + menu, "", 1) if (" " + menu) in tex else tex.replace(menu, "", 1)
        else:
            print("[cv_builder] WARNING: contract menu not found in the base .tex — "
                  "the header may still offer all three contract types.", file=sys.stderr)

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

    # Fit the page. The INVARIANT is unchanged — content is never dropped while spacing alone
    # would do — but the search order is inverted, because the old one was quadratic. It swept
    # all five FIT_STEPS before dropping anything, then swept them all again after each drop:
    # ~25 tectonic runs, 2min30 for the single-column CV. Every one of those sweeps was already
    # doomed whenever the overflow exceeded what spacing can recover.
    #
    # Instead: compile ONCE at the tightest spacing and drop only while even that does not fit
    # (so a block is dropped strictly later than before, never earlier), then walk the spacing
    # back out to the LOOSEST setting that still fits, so the CV is not needlessly crammed.
    # ~7 runs for the same result. This matters off the Mac: /daily-agent rebuilds the CV for
    # every follow-up attachment on a 1GB e2-micro.
    kw = offer_keywords(offer_text or "")
    plan = plan or {}
    lead = []
    if plan.get("skills_order"):
        tex, lead = apply_skill_order(tex, plan["skills_order"])
        if lead:
            print(f"[cv_builder] skills ordered by the plan — leading with {' '.join(lead)}")
    elif kw:
        tex, lead = reorder_skills(tex, kw)
        if lead:
            print(f"[cv_builder] skills reordered for this offer — leading with {' '.join(lead)}")
    if plan.get("bullets"):
        tex, refus = apply_bullets(tex, plan["bullets"])
        n = len(plan["bullets"]) - len(refus)
        print(f"[cv_builder] {n} bullet group(s) rewritten for this posting")
        for r in refus:
            print(f"[cv_builder] bullets REFUSED — {r}")
    if plan.get("profil"):
        tex, why = apply_profil(tex, plan["profil"])
        print("[cv_builder] profile rewritten for this posting" if not why
              else f"[cv_builder] proposed profile REFUSED ({why}) — original kept")

    source, dropped = tex, []
    tightest = FIT_STEPS[-1]

    overflow, _ = _compile(source, tightest)
    while overflow is not None and overflow > 0:
        nxt = next((b for b in plan_drop_order(source, plan.get("sacrifice") or [], focus, kw)
                    if b["id"] not in dropped), None)
        if nxt is None:
            tmp_tex.unlink(missing_ok=True)
            compiled.unlink(missing_ok=True)
            raise RuntimeError(
                f"CV still overflows by {overflow:.0f}pt (~{overflow / 28.45:.1f}cm) with every "
                f"droppable block removed and spacing at {tightest:.2f}.\n"
                "The main column is a fixed-height minipage, so the excess is drawn BELOW the page "
                "edge and is invisible — still in the PDF text layer, which is why this went "
                "unnoticed while every attached CV was missing its last section.\n"
                "Refusing to ship a truncated CV: shorten a bullet in the .tex and rebuild."
            )
        print(f"[cv_builder] still {overflow:.0f}pt over — dropping '{nxt['id']}' "
              f"(least relevant to --focus {focus})", file=sys.stderr)
        dropped.append(nxt["id"])
        source = strip_block(source, nxt["id"])
        overflow, _ = _compile(source, tightest)

    # It fits at the tightest spacing. Give the page its air back: the loosest step that holds.
    chosen = tightest
    for fit in FIT_STEPS:                      # loosest first
        if fit == tightest:
            break
        over, _ = _compile(source, fit)
        if over is None or over <= 0:
            chosen = fit
            break
    if chosen != tightest:                     # the winning run was not the last compiled
        _compile(source, chosen)

    if dropped:
        print(f"[cv_builder] omitted for --focus {focus}: {', '.join(dropped)} "
              f"(would not fit on one page)")
    if chosen != FIT_STEPS[0]:
        print(f"[cv_builder] tightened spacing to {chosen:.2f} to fit the page")

    tmp_tex.unlink(missing_ok=True)

    # Rename to final path
    final = DOCUMENTS_DIR / f"CV_Zineb_Meftah_{'FR' if lang == 'fr' else 'EN'}_custom.pdf"
    shutil.move(str(compiled), str(final))

    print(f"[cv_builder] compiled → {final}")
    return final


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _plan(args) -> dict:
    """Le plan du modèle, s'il y en a un. Illisible ou absent -> comportement déterministe."""
    path = getattr(args, "plan", None)
    if not path:
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")) or {}
    except Exception as exc:
        print(f"[cv_builder] plan ignoré ({exc}) — sélection déterministe")
        return {}


def _offer_text(args) -> str:
    """Posting text from --offer (a file) or --offer-text (inline). Empty if neither."""
    if getattr(args, "offer", ""):
        pth = _P(args.offer) if "_P" in globals() else Path(args.offer)
        return pth.read_text(encoding="utf-8", errors="replace")
    return getattr(args, "offer_text", "") or ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build role-adapted CV PDF from LaTeX source")
    parser.add_argument("--lang", default="fr", choices=["fr", "en"])
    parser.add_argument("--focus", choices=["ai", "backend", "mlops", "data", "fullstack"],
                        help="Role focus (auto-detected from --role if omitted)")
    parser.add_argument("--role", default="", help="Raw role title (used for focus detection)")
    parser.add_argument("--company", default="", help="Company name (informational)")
    parser.add_argument("--headline", default="",
                        help="Override the role line, e.g. \"DATA SCIENTIST & IA\" — use the "
                             "posting's own job title; block selection still follows --focus")
    parser.add_argument("--subtitle", default="", help="Override the italic tagline")
    parser.add_argument("--plan", default="", metavar="PATH",
                        help="JSON produit par le modèle : en-tête, paragraphe de profil, ordre "
                             "des compétences et ordre de sacrifice des projets. Il ne peut que "
                             "CHOISIR et ORDONNER — un terme absent de son dossier est refusé")
    parser.add_argument("--offer", default="", metavar="PATH",
                        help="file containing the posting's text — selects the projects and "
                             "skill rows that match what THIS employer asked for")
    parser.add_argument("--offer-text", default="", metavar="TEXT",
                        help="the posting's text inline, instead of --offer")
    parser.add_argument("--contract", default="alternance", choices=["alternance", "any"],
                        help="alternance (default) drops the 'CDI, CDD ou alternance' menu from "
                             "the header; 'any' keeps it for a CDI/CDD application")
    args = parser.parse_args(argv)

    try:
        path = build(lang=args.lang, focus=args.focus, role=args.role, company=args.company,
                     headline=args.headline or None, subtitle=args.subtitle or None,
                     contract=args.contract, offer_text=_offer_text(args),
                     plan=_plan(args))
        print(f"✅  {path}")
        return 0
    except Exception as e:
        print(f"❌  {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
