"""JobTeaser — la plateforme d'alternance et de stage des écoles françaises.

POURQUOI ELLE REVIENT. Le dépôt l'avait écartée deux fois : « JobTeaser 403 » en HTTP simple
(2026-09-20), puis « rend bien au navigateur, mais renvoie du bruit allemand et danois sans ses
vrais filtres ». Les deux constats étaient exacts au moment de la mesure, et les deux sont
FAUX AUJOURD'HUI (2026-09-28, remesuré) :

  · la recherche répond 200 en HTTP simple avec un en-tête de navigateur ordinaire ;
  · les cartes sont rendues CÔTÉ SERVEUR — employeur, intitulé, contrat et lieu sont dans le
    HTML, pas dans un appel ultérieur ;
  · et `?q=` suffit à cadrer les résultats, sans les filtres exotiques qu'on croyait requis.

D'où la leçon, la même que pour AXA : un verdict « illisible » porte une DATE. Une plateforme
refusée il y a huit jours mérite une remesure avant d'être refusée pour toujours.

CE QU'ELLE APPORTE QUE LES AUTRES N'ONT PAS. C'est la plateforme que les ÉCOLES utilisent, donc
ses offres sont écrites pour des alternants et des stagiaires — là où France Travail et l'APEC
mélangent tous les niveaux. Première mesure : la toute première carte rendue pour « alternance
data » était une alternance CHANEL de 12 mois.

⚠ AUCUNE CLÉ, AUCUN COMPTE. Si la recherche se met à répondre 403, c'est que le mur est revenu :
  le module le dit et rend une liste vide, il n'essaie pas de contourner.

    python jobteaser.py           # ce qu'elle trouve en ce moment
"""
from __future__ import annotations

import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import jobsource as js
import source_lab as _sl

NAME = "jobteaser"
BASE = "https://www.jobteaser.com"
SEARCH = f"{BASE}/fr/job-offers"

_UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# CONTRAT D'ABORD, comme sur Adzuna et HelloWork : l'alternance est la rareté, pas le métier.
ALTERNANCE_QUERIES: dict[str, str] = {
    "ai": "alternance intelligence artificielle",
    "data": "alternance data",
    "backend": "alternance développeur",
    "mlops": "alternance devops",
}
QUERIES: dict[str, str] = {
    "ai": "machine learning",
    "data": "data engineer",
    "backend": "développeur backend",
}

_CARTE = re.compile(r'data-testid="jobad-card"')
_LIEN = re.compile(r'href="(/fr/job-offers/[^"]+)"')
_TITRE = re.compile(r"<h[23][^>]*>(.*?)</h[23]>", re.S)
_BALISES = re.compile(r"<[^>]+>")


def _texte(html: str) -> str:
    return " ".join(_BALISES.sub(" ", html or "").split())


def _champ(carte: str, testid: str) -> str:
    """Le PREMIER vrai morceau de texte de ce champ.

    Deux pièges, tous deux payés sur la première version :
      · le repère est un ATTRIBUT, donc couper juste après lui fait commencer la fenêtre au
        milieu de la balise ouvrante — le dépouillement rendait alors les attributs suivants
        comme du texte (« class="sk-Text sk-Typogr » en guise de nom d'employeur) ;
      · une fenêtre de taille fixe déborde sur la suite de la carte — le nom de l'employeur
        y figure DEUX FOIS, d'où « OPmobility OPmobility <a class=… ». Et le lieu comme le
        contrat sont précédés d'une icône SVG dont le contenu n'est pas du texte.
    On avance donc balise par balise et on rend le premier nœud de texte exploitable.
    """
    m = re.search(rf'data-testid="{re.escape(testid)}"', carte)
    if not m:
        return ""
    i = carte.find(">", m.end())
    if i < 0:
        return ""
    # Assez large pour contenir l'icône SVG en ENTIER : tronquée, sa dernière balise reste
    # ouverte, le découpage ne la reconnaît plus et son tracé ressort comme du texte
    # (« <path clip-rule="evenodd" » en guise de lieu). La garde ci-dessous rattrape le reste.
    fenetre = carte[i + 1:i + 3000]
    for morceau in re.split(r"<[^>]*>", fenetre):
        t = " ".join(morceau.split())
        if len(t) < 2 or not re.search(r"[A-Za-zÀ-ÿ0-9]", t):
            continue
        if any(ch in t for ch in "<=\""):      # reste de balise, jamais du texte lisible
            continue
        return _decode(t)[:70].strip()
    return ""


def _decode(t: str) -> str:
    import html
    return html.unescape(t)


_dernier = [0.0]


def _pace(secondes: float = 1.5) -> None:
    """Espacer les requêtes. Mesuré le 2026-09-28 : enchaînées, la PREMIÈRE requête d'une
    session repart en 403 alors que les suivantes passent — une limitation de débit, pas un mur.
    Même remède que pour LinkedIn dans descriptions._pace."""
    import time
    ecart = time.time() - _dernier[0]
    if ecart < secondes:
        time.sleep(secondes - ecart)
    _dernier[0] = time.time()


def _fetch(query: str, page: int) -> str:
    params = {"q": query}
    if page:
        params["page"] = str(page + 1)
    url = f"{SEARCH}?{urllib.parse.urlencode(params)}"
    _pace()
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _cartes(html: str) -> list[str]:
    morceaux = _CARTE.split(html)[1:]
    return [m[:8000] for m in morceaux]


# Le lieu est rendu « Ville, Pays » sur chaque carte, donc le pays est LISIBLE et n'a pas à
# être deviné. JobTeaser est européenne : sans ce filtre la recherche « alternance data » rend
# des Duales Studium à Cologne et Schopfloch. Ils ne sont pas seulement inutiles — leur ville
# étant inconnue du classifieur de lieux, ils ressortent « lieu inconnu », donc NEUTRES, et se
# glissent au milieu des offres franciliennes au lieu d'être écartés.
_HORS_FRANCE = re.compile(
    r",\s*(germany|deutschland|spain|espa|italy|italia|netherlands|belgi|poland|polska|"
    r"portugal|sweden|denmark|norway|finland|austria|switzerland|suisse|ireland|"
    r"united kingdom|czech|roman|hungar|greece|luxembourg)\b", re.I)


def _en_france(lieu: str) -> bool:
    """Lieu vide → on garde : l'absence n'est pas une preuve, et le télétravail n'a pas de ville."""
    return not _HORS_FRANCE.search(lieu or "")


def _meta(carte: str) -> dict:
    contrat = _champ(carte, "jobad-card-contract").lower()
    return {
        "contract": ("alternance" if ("alternance" in contrat or "apprentissage" in contrat)
                     else "internship" if ("stage" in contrat or "internship" in contrat)
                     else ""),
    }


def discover(page=None, max_pages: int | None = None) -> list[js.JobListing]:
    """Les offres JobTeaser qui correspondent à ses métiers. HTTP pur, aucun navigateur.

    `page` (une page Playwright) n'est pas utilisée. Tolérante aux pannes : une requête qui
    échoue est signalée et sautée, jamais propagée.
    """
    pages_par_requete = max_pages if max_pages is not None else 2
    listings: list[js.JobListing] = []
    vus: set[str] = set()

    for categorie, requete in _sl.plan(NAME, list(ALTERNANCE_QUERIES.items())
                                       + list(QUERIES.items())):
        for n in range(pages_par_requete):
            try:
                html = _fetch(requete, n)
            except urllib.error.HTTPError as e:
                print(f"[jobteaser]   HTTP {e.code} sur {requete!r}"
                      + (" — le mur anti-robot est revenu, source ignorée"
                         if e.code in (403, 429) else ""))
                break
            except Exception as e:  # noqa: BLE001
                print(f"[jobteaser]   erreur: {type(e).__name__}: {e}")
                break

            cartes = _cartes(html)
            if not cartes:
                break
            ajoutes = 0
            for c in cartes:
                employeur = _champ(c, "jobad-card-company-name")
                titres = _TITRE.findall(c)
                titre = _decode(_texte(titres[0])) if titres else ""
                if not employeur or not titre:
                    continue
                cle = f"{employeur.lower()}|{titre.lower()}"
                if cle in vus:
                    continue
                if not js.matches_target_role(titre) or js.excluded_role(titre):
                    continue
                lieu = _champ(c, "jobad-card-location")
                if not _en_france(lieu):
                    continue
                vus.add(cle)
                lien = _LIEN.search(c)
                listings.append(js.JobListing(
                    company=employeur, role=titre,
                    job_url=(BASE + lien.group(1)) if lien else SEARCH,
                    location=lieu,
                    category=categorie, source=NAME, meta=_meta(c)))
                ajoutes += 1
            print(f"[jobteaser]   query={requete!r} page {n + 1}: +{ajoutes} match(es)")
            if len(cartes) < 5:          # dernière page servie
                break
    return listings


if __name__ == "__main__":
    rows = discover(max_pages=int(sys.argv[1]) if len(sys.argv) > 1 else 2)
    print(f"\n{len(rows)} offre(s) correspondant à ses métiers sur JobTeaser :\n")
    for r in rows:
        m = r.meta or {}
        print(f"  {r.company[:24]:26} {r.role[:46]:48} {m.get('contract') or '—':12} "
              f"{(r.location or '')[:26]}")
