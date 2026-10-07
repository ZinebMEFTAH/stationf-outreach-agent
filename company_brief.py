"""Ce que fait VRAIMENT l'employeur, lu sur son site — pour que la lettre n'ait pas à répéter
l'annonce.

SON INSTRUCTION (2026-10-07) : « for each offer a search on the company should be made and
provided to the model... plus it is useless to just retell the offer in the motivation letter ».
Elle a raison : les quatre formes de lettre ouvraient sur « un fait précis tiré de leur annonce »,
donc la première phrase lui racontait ce qu'elle avait écrit elle-même. C'est la phrase la plus
faible de chaque lettre.

PYTHON CHERCHE, LE MODÈLE LIT. On ne demande pas au modèle de chercher : ça coûte un appel de plus
par lettre, c'est lent, et un modèle qui cherche librement est exactement la façon dont un fait
inventé finit dans une lettre qu'elle signe. Même règle que partout ici.

⚠ CE MODULE A ÉTÉ MESURÉ AVANT D'ÊTRE BRANCHÉ, et la mesure a changé le dessin deux fois.
  Sur cinq entreprises réelles (ChapsVision, Softeam, Codoc, VO2 Group, Flowt) :
    · 2 sans domaine trouvé, 1 injoignable (HTTPError)
    · 1 correcte et utile — ChapsVision, qui ressort le nom de son produit, « Argonos »
    · 1 CARRÉMENT FAUSSE : « Softeam » a résolu vers softeamitalia.com, un distributeur italien
      de machines à croquettes. company_resolver sert d'ordinaire à devin er un domaine
      d'E-MAIL, où une erreur rebondit sans conséquence et où la vérification l'attrape ; ici
      une erreur s'IMPRIME dans la lettre. Un fait sur la mauvaise entreprise est bien pire que
      pas de fait du tout.
  D'où la corroboration ci-dessous. Et d'où le fait que ce module soit OPTIONNEL par
  construction : il ne rend quelque chose que quand il est sûr, et le prompt sait quoi faire de
  son silence.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).parent
CACHE = ROOT / "cache" / "company_brief.json"
TTL = 30 * 24 * 3600          # un site d'entreprise ne change pas d'un mois sur l'autre

# LE SEUIL, MESURÉ — pas choisi. Part du vocabulaire de la PAGE que l'annonce confirme :
#   Docaposte 29,1 % · ChapsVision 15,2 % · Capgemini 6,4 % · Converteo 5,4 %  (toutes correctes)
#   Softeam Italia 0,9 %                                                       (fausse)
# 3 % laisse un facteur ~2 sous la plus faible correcte et ~3 au-dessus de la fausse.
MIN_RECOUVREMENT = 0.03

_VIDE = re.compile(
    r"^(le|la|les|un|une|des|du|de|et|ou|en|au|aux|pour|par|sur|avec|dans|son|ses|leur|nos|notre|"
    r"vous|nous|est|sont|the|and|for|with|you|your|our|this|that|from|are|will|all|plus|tout|"
    r"tous|toute|toutes|cette|qui|que|dont|cookies|cookie|accepter|refuser|menu|accueil|"
    r"contact|retour|aper[çc]u|voir|lire|savoir|suite|site|page|www|https|http)$")

# LA BRODERIE COMMERCIALE, qui est aussi inutile que de citer l'annonce — et c'est ce que rend
# une page d'accueil, la page la plus marketing d'un site. Mesuré : Capgemini a ressorti
# « leader, discover, find, digital », Converteo « cabinet, conseil, consultants », Docaposte
# « accompagner, acteur, améliorer, bout, chaîne ». Aucune de ces listes ne donne de quoi écrire
# une première phrase. Elles sont retirées des FAITS ; le texte brut reste disponible à côté.
_BRODERIE = re.compile(
    r"\b(leader|acteur|majeur|incontournable|innovation|innovante?s?|excellence|expertises?|"
    r"accompagner|accompagnement|performance|synergie|ambition|passion|humaine?s?|confiance|"
    r"engagement|transformation|strat[ée]giques?|solutions?|cabinet|conseil|consultants?|"
    r"savoir-faire|sur-mesure|cl[ée] en main|bout en bout|[ée]cosyst[èe]me|"
    r"discover|find out|learn more|our (?:people|values|mission)|world-class|cutting.edge)\b",
    re.I)


def _mots(t: str) -> set[str]:
    out = set()
    for m in re.findall(r"[a-zà-ÿ][a-zà-ÿ0-9'’-]{3,}", (t or "").lower()):
        m = m.strip("'’-")
        if len(m) >= 4 and not _VIDE.match(m):
            out.add(m)
    return out


def _tokens_nom(company: str) -> set[str]:
    """Les mots du nom de l'entreprise — ils ne peuvent JAMAIS servir de corroboration.

    Softeam Italia partageait avec l'annonce EXACTEMENT un mot : « softeam ». Si le nom est tout
    ce qui coïncide, la page peut tout aussi bien appartenir à un homonyme sur un autre continent.
    """
    brut = re.sub(r"\b(groupe?|group|sas|sarl|sa|france|technologies?|digital|consulting)\b", " ",
                  (company or "").lower())
    return {m for m in re.findall(r"[a-zà-ÿ0-9]{3,}", brut)}


def _charge(url: str) -> str:
    import descriptions as d
    return " ".join(d._page_text(d._get(url)).split())


def _cache_lire(cle: str) -> dict | None:
    try:
        tout = json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None
    e = tout.get(cle)
    if isinstance(e, dict) and time.time() - e.get("ts", 0) < TTL:
        return e
    return None


def _cache_ecrire(cle: str, valeur: dict) -> None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        try:
            tout = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            tout = {}
        tout[cle] = dict(valeur, ts=time.time())
        CACHE.write_text(json.dumps(tout, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass          # un défaut d'écriture de cache ne doit jamais empêcher une candidature


def corroborates(page: str, posting: str, company: str) -> tuple[bool, float, list[str]]:
    """La page parle-t-elle de la MÊME entreprise que l'annonce ?

    Renvoie (verdict, part, mots communs hors nom). DEUX conditions, la seconde étant celle que
    le cas Softeam a rendue nécessaire : la page s'appelait « SofTeam – distributore di
    innovazione », donc vérifier le NOM la validait. Ce qui la démasque, c'est qu'elle ne
    partageait rien d'autre.
    """
    mp = _mots(page)
    if not mp:
        return False, 0.0, []
    communs = mp & _mots(posting)
    hors_nom = sorted(communs - _tokens_nom(company))
    part = len(communs) / len(mp)
    return (part >= MIN_RECOUVREMENT and len(hors_nom) >= 3), part, hors_nom


# LES APPELS À L'ACTION ET LA NAVIGATION, retirés AVANT de découper en phrases : ils se collent
# au début et à la fin des vraies phrases et les rendent inutilisables. Mesuré sur Capgemini, dont
# la page d'accueil ne rend QUE ça (« Discover more --> », « Find out more --> ») et sur Docaposte,
# où chaque fait concret était précédé d'un « En savoir plus » de la carte précédente.
_NAVIGATION = re.compile(
    r"-->|→|\b(en savoir plus|discover more|find out more|learn more|read more|voir plus|"
    r"tout voir|retour aper[çc]u|nous contacter|postuler|d[ée]couvrir|t[ée]l[ée]charger)\b",
    re.I)
# Les listes d'articles de blog passaient le filtre : elles croisent le vocabulaire de l'annonce
# et ne sont pas de la broderie, mais « Date de publication 06.10.2026 - Temps de lecture 9 min »
# ne dit rien de ce que fait l'entreprise.
_LISTING = re.compile(r"temps de lecture|date de publication|min(?:ute)?s? de lecture", re.I)
_CHIFFRE = re.compile(r"\d|\b(premier|1\s*er|leader mondial)\b", re.I)


def _faits(page: str, posting: str, company: str) -> list[str]:
    """Les phrases de la page qui disent quelque chose de CONCRET.

    Une phrase est gardée si elle croise le vocabulaire de l'annonce ET ne tient pas que de la
    broderie. On garde des PHRASES et non des mots-clés : « Argonos » seul ne permet pas d'écrire,
    la phrase qui l'entoure si.

    ⚠ LES PHRASES CHIFFRÉES PASSENT DEVANT, parce que ce sont les seules qui font une bonne
      première phrase de lettre. Mesuré sur Docaposte : la page rend « 1er opérateur de données de
      santé avec 45 millions de dossiers patients » — vérifiable, spécifique, impossible à
      confondre avec une autre entreprise — à côté de « une entreprise portée par ses talents »,
      qui ne dit rien. Sans tri, les deux arrivaient dans le même ordre que la page.
    """
    utiles = _mots(posting) - _tokens_nom(company)
    gardees = []
    for phrase in re.split(r"(?<=[.!?])\s+|\s{2,}|•", page):
        p = _NAVIGATION.sub(" ", phrase)
        p = re.sub(r"\s+", " ", p).strip(" •\t·-")
        if not (40 <= len(p) <= 260) or p.count(" ") < 5:
            continue
        if _LISTING.search(p):
            continue
        if len(_mots(p) & utiles) < 2:
            continue
        # Une phrase entièrement faite de broderie n'apporte rien ; une phrase concrète qui
        # contient AU PASSAGE le mot « solutions » reste bonne, d'où le ratio et non le veto.
        mots_p = _mots(p)
        if mots_p and len(_BRODERIE.findall(p)) / max(1, len(mots_p)) > 0.15:
            continue
        gardees.append(p)
    # dédoublonnage : une page répète ses accroches d'une carte à l'autre
    vues, uniques = set(), []
    for p in gardees:
        cle = " ".join(sorted(_mots(p)))[:120]
        if cle not in vues:
            vues.add(cle)
            uniques.append(p)
    uniques.sort(key=lambda p: (0 if _CHIFFRE.search(p) else 1, -len(_mots(p) & utiles)))
    return uniques[:5]


def research(company: str, posting: str = "", use_cache: bool = True) -> dict:
    """Ce qu'on sait de l'employeur, ou {} si on n'en sait rien de sûr.

    {} EST UNE RÉPONSE NORMALE, et c'est le cas le plus fréquent (mesuré : 1 sur 5 rend quelque
    chose d'utilisable). Le prompt de la lettre doit donc savoir quoi faire de ce silence — et
    surtout ne pas retomber sur une paraphrase de l'annonce, qui est le défaut d'origine.
    """
    if not (company or "").strip():
        return {}
    cle = re.sub(r"\s+", " ", company.strip().lower())
    if use_cache:
        e = _cache_lire(cle)
        if e is not None:
            return {k: v for k, v in e.items() if k != "ts"}

    res: dict = {}
    try:
        import company_resolver
        dom = company_resolver.resolve_domain(company)
    except Exception:
        dom = None
    if dom:
        try:
            page = _charge("https://" + dom)
        except Exception:
            page = ""
        if page:
            ok, part, communs = corroborates(page, posting, company)
            if ok:
                faits = _faits(page, posting, company)
                if faits:
                    res = {"domain": dom, "overlap": round(part, 3), "facts": faits}
            else:
                # On garde la trace du REFUS : sans elle, on refait le même appel réseau tous
                # les jours pour rien, et on ne peut pas expliquer pourquoi la lettre n'a pas
                # de fait sur l'entreprise.
                res = {"domain": dom, "overlap": round(part, 3), "facts": [],
                       "refused": "le site ne corrobore pas l'annonce"}
    if use_cache:
        _cache_ecrire(cle, res)
    return res


def brief(company: str, posting: str = "") -> str:
    """Le bloc prêt à coller dans un prompt, ou "" quand on ne sait rien de sûr."""
    r = research(company, posting)
    faits = r.get("facts") or []
    if not faits:
        return ""
    lignes = "\n".join(f"  · {f}" for f in faits)
    return (f"CE QUE {company.upper()} DIT DE LUI-MÊME SUR SON PROPRE SITE ({r['domain']}) — lu "
            f"automatiquement, et confirmé comme étant bien cette entreprise :\n{lignes}\n")


def main(argv: list[str] | None = None) -> int:
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python company_brief.py \"ENTREPRISE\" [offers/annonce.txt]")
        return 2
    company = argv[0]
    posting = Path(argv[1]).read_text(encoding="utf-8") if len(argv) > 1 else ""
    r = research(company, posting, use_cache=False)
    if not r:
        print(f"{company} : aucun domaine fiable trouvé — la lettre n'ouvrira pas sur eux.")
        return 0
    print(f"{company} -> {r.get('domain')}  recouvrement {r.get('overlap')}")
    if r.get("refused"):
        print(f"  REFUSÉ : {r['refused']}")
    for f in r.get("facts") or []:
        print(f"  · {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
