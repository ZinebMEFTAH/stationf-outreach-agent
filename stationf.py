"""Le job board de Station F — les startups du campus, en Python pur.

POURQUOI CE FICHIER, ALORS QUE LE DÉPÔT S'APPELLE stationf_agent. Station F était la source
d'origine, lue par `scraper.py` AU NAVIGATEUR (Playwright), pour y récupérer aussi un contact
nommé par entreprise. Cette voie-là sert toujours l'outreach — mais elle demande un navigateur,
donc l'interface web ne l'a jamais proposée : ses dix sources n'incluaient pas celle qui donne
son nom au dépôt.

CE QU'A MONTRÉ L'OBSERVATION DU RÉSEAU (2026-09-28). La page ne contient aucune offre dans son
HTML (33 Ko, zéro lien), donc elle est rendue côté client — et c'est pour ça qu'on la croyait
réservée au navigateur. Mais en regardant CE QUE LA PAGE APPELLE elle-même, comme le dépôt l'a
fait pour AXA, on trouve un index Algolia public :

    app    CSEKHVMS53                       ← LE MÊME que celui de wttj.py
    index  wk_cms_jobs_production_careers   ← l'index « careers », pas celui de WTTJ
    clé    une clé RESTREINTE, qui se décode en filters=website.reference:station-f-job-board

Autrement dit le board de Station F tourne sur le Welcome Kit de Welcome to the Jungle, et la
clé publique de la page le limite d'office aux startups du campus. Aucun navigateur n'est
nécessaire, aucune inscription, et les champs sont les mêmes que ceux que `wttj.py` sait déjà
lire — y compris `experience_level_minimum`, que le score de l'interface utilise depuis
aujourd'hui pour distinguer un employeur qui lira une débutante d'un qui ne la lira pas.

⚠ LA CLÉ EST CELLE DE LA PAGE, donc publique, mais elle peut être renouvelée. Si les requêtes
  se mettent à répondre 403, il suffit de relire la clé dans le réseau de jobs.stationf.co et
  de la poser dans STATIONF_ALGOLIA_KEY — la même convention que WTTJ_ALGOLIA_KEY.

    python stationf.py            # ce que le board propose en ce moment
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import jobsource as js
import source_lab as _sl

NAME = "stationf"
BASE = "https://jobs.stationf.co"

ALGOLIA_APP = os.getenv("STATIONF_ALGOLIA_APP", "CSEKHVMS53")
# Clé RESTREINTE lue dans la page : elle porte son propre filtre sur le board de Station F.
ALGOLIA_KEY = os.getenv(
    "STATIONF_ALGOLIA_KEY",
    "ZTQzYjA0MGViZWQ5YmU0YWRkMjQ0ODhlYmFiOGNiOTU1MmVmMmExZDFkMDI2MjNmMGExNTA1"
    "OTdlMjM4ZDlhN2ZpbHRlcnM9d2Vic2l0ZS5yZWZlcmVuY2UlM0FzdGF0aW9uLWYtam9iLWJvYXJk")
ALGOLIA_INDEX = os.getenv("STATIONF_ALGOLIA_INDEX", "wk_cms_jobs_production_careers")
HITS_PER_PAGE = 20

# Le campus est petit — 577 offres tous métiers, 33 en alternance le jour de l'ajout — donc les
# requêtes restent larges : c'est `matches_target_role` qui filtre, et une requête trop étroite
# sur un corpus de cette taille ne rend plus rien du tout.
ALTERNANCE_QUERIES: dict[str, str] = {
    "ai": "alternance data",
    "backend": "alternance développeur",
    "data": "apprentissage data",
}
QUERIES: dict[str, str] = {
    "ai": "machine learning",
    "backend": "software engineer",
    "data": "data",
}


def _algolia_query(query: str, page: int, hits_per_page: int = HITS_PER_PAGE) -> dict:
    url = (f"https://{ALGOLIA_APP.lower()}-dsn.algolia.net/1/indexes/"
           f"{urllib.parse.quote(ALGOLIA_INDEX)}/query"
           f"?x-algolia-application-id={ALGOLIA_APP}"
           f"&x-algolia-api-key={ALGOLIA_KEY}")
    body = json.dumps({"query": query, "hitsPerPage": hits_per_page, "page": page}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _job_url(hit: dict) -> str:
    """Le lien public de l'offre sur le board du campus.

    L'index porte `reference` (l'identifiant de l'offre) et le slug de l'organisation ; le board
    sert ses fiches sous /companies/<org>/jobs/<slug>. À défaut d'un slug exploitable on renvoie
    la recherche du board plutôt qu'un lien inventé : une URL fausse coûte un clic mort, et le
    dépôt a déjà payé pour des liens devinés.
    """
    org = (hit.get("organization") or {}).get("slug") or ""
    slug = hit.get("slug") or hit.get("reference") or ""
    if org and slug:
        return f"{BASE}/companies/{org}/jobs/{slug}"
    ref = hit.get("reference") or hit.get("objectID") or ""
    return f"{BASE}/search?q={urllib.parse.quote(str(ref))}" if ref else f"{BASE}/search"


def _meta(hit: dict) -> dict:
    """Les champs structurés de l'index — identiques à ceux de WTTJ, même Welcome Kit.

    `experience_level_minimum` est en ANNÉES ; il est ramené sur l'alphabet D/S/E que France
    Travail publie, pour que le score n'ait pas de cas particulier par board.
    """
    yrs = hit.get("experience_level_minimum")
    exp = ""
    if isinstance(yrs, int):
        exp = "D" if yrs <= 0 else ("S" if yrs <= 2 else "E")
    ct = (hit.get("contract_type") or "").upper()
    return {
        "contract": ("alternance" if ct in ("APPRENTICESHIP", "APPRENTICESHIP_CONTRACT",
                                            "PROFESSIONAL_TRAINING_CONTRACT")
                     else "internship" if ct == "INTERNSHIP" else ""),
        "posted": (hit.get("published_at") or "")[:10],
        "experience": exp,
    }


def _location_of(hit: dict) -> str:
    offices = hit.get("offices") or []
    if not offices:
        return ""
    o = offices[0]
    return " - ".join(p for p in (str(o.get(k) or "").strip()
                                  for k in ("city", "state", "country")) if p)


def _is_france(hit: dict) -> bool:
    """Une startup du campus peut recruter à l'étranger. Lieu inconnu → on garde (neutre)."""
    offices = hit.get("offices") or []
    return True if not offices else any(o.get("country_code") == "FR" for o in offices)


def discover(page=None, max_pages: int | None = None) -> list[js.JobListing]:
    """Les offres du board Station F qui correspondent à ses métiers. HTTP pur.

    `page` (une page Playwright) n'est pas utilisée : contrairement à `scraper.py`, cette voie
    ne rend rien. Tolérante aux pannes — une requête qui échoue est signalée et sautée.
    """
    pages_per_query = max_pages if max_pages is not None else 2
    listings: list[js.JobListing] = []
    seen: set[str] = set()

    for category, query in _sl.plan(NAME, list(ALTERNANCE_QUERIES.items())
                                    + list(QUERIES.items())):
        for n in range(pages_per_query):
            try:
                data = _algolia_query(query, n)
            except urllib.error.HTTPError as e:
                print(f"[stationf]   algolia HTTP {e.code} "
                      f"(la clé publique a pu être renouvelée — relis-la dans le réseau de "
                      f"{BASE} et pose-la dans STATIONF_ALGOLIA_KEY)")
                break
            except Exception as e:  # noqa: BLE001
                print(f"[stationf]   algolia error: {type(e).__name__}: {e}")
                break

            hits = data.get("hits") or []
            if not hits:
                break
            added = 0
            for h in hits:
                org = (h.get("organization") or {}).get("name") or ""
                title = h.get("name") or ""
                if not org or not title:
                    continue
                key = f"{org.lower()}|{title.lower()}"
                if key in seen:
                    continue
                if not js.matches_target_role(title) or js.excluded_role(title):
                    continue
                if not _is_france(h):
                    continue
                seen.add(key)
                listings.append(js.JobListing(
                    company=org, role=title, job_url=_job_url(h),
                    location=_location_of(h), category=category, source=NAME,
                    meta=_meta(h)))
                added += 1
            print(f"[stationf]   query={query!r} page {n + 1}: +{added} match(es)")
            if n + 1 >= (data.get("nbPages") or 1):
                break
    return listings


if __name__ == "__main__":
    rows = discover(max_pages=int(sys.argv[1]) if len(sys.argv) > 1 else 2)
    print(f"\n{len(rows)} offre(s) correspondant à ses métiers sur le board Station F :\n")
    for r in rows:
        m = r.meta or {}
        print(f"  {r.company[:26]:28} {r.role[:44]:46} {m.get('contract') or '—':12} "
              f"{r.location[:24]}")
