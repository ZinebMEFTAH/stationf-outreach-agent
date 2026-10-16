"""Resolve a company *name* → its real email *domain*.

The discovery-only sources (Welcome to the Jungle, HelloWork) give us a company name
but hide the employer's website, so rows land with a guessed `contact@<slug>.com` that
is usually wrong. This module turns the name into the real domain using Clearbit's
**free, key-less** company autocomplete API, then sanity-checks it has MX records so we
never hand a dead domain to the email-derivation step.

Used by contact_finder (which auto-resolves when it has no domain) and callable directly:

    python company_resolver.py "Ippon Technologies"      # -> ippon.tech

Results are cached in cache/company_domains.json so repeat runs don't re-hit the API.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

CLEARBIT_URL = "https://autocomplete.clearbit.com/v1/companies/suggest?query="
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_CACHE_PATH = Path(__file__).resolve().parent / "cache" / "company_domains.json"

# TLD preference — France-relevant + common tech TLDs rank above foreign country TLDs,
# which helps avoid same-name companies in other countries (e.g. *.it / *.ru false hits).
_TLD_SCORE = {"fr": 16, "com": 13, "io": 9, "ai": 9, "co": 7, "tech": 7, "eu": 6, "net": 4}

# TLDs a French/EU employer plausibly uses (generic + fr/eu). Anything in _FOREIGN_TLDS
# is treated as a different-country same-name company and rejected — we'd rather resolve
# nothing than email the wrong company (the anti-bounce gate checks deliverability, not
# identity, so a wrong-but-live domain would slip through).
_GOOD_TLDS = {"com", "fr", "io", "ai", "co", "eu", "tech", "net", "org", "app", "dev", "xyz"}
_FOREIGN_TLDS = {"ru", "it", "kz", "ae", "qa", "kw", "cn", "in", "br", "de", "es", "pl",
                 "uk", "us", "ca", "jp", "kr", "tr", "nl", "be", "ch", "mx", "ar", "za",
                 "ng", "sa", "id", "vn", "th", "pt", "se", "no", "dk", "fi", "gr", "ro",
                 "lu", "at", "ie", "cz", "hu", "au", "nz", "sg", "hk", "tw", "ph", "my",
                 "il", "eg", "cl", "co.uk", "ua", "by", "lt", "lv", "ee", "sk", "si", "hr"}


def _norm(s: str) -> str:
    """Lowercase, drop accents/punctuation, collapse spaces, strip company suffixes."""
    s = (s or "").lower()
    s = (s.replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
           .replace("â", "a").replace("ç", "c").replace("ù", "u").replace("ô", "o")
           .replace("î", "i").replace("ï", "i").replace("û", "u"))
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(sas|sa|sarl|inc|ltd|llc|gmbh|group|groupe|technologies|technology|"
               r"solutions|consulting|labs|studio|france|paris)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _norm(s).replace(" ", ""))


_MISS_PATH = _CACHE_PATH.with_name("company_domains_miss.json")
_MISS_TTL_DAYS = 14
_LOCK = threading.Lock()


def _load_cache() -> dict:
    try:
        return json.loads(_CACHE_PATH.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    """⚠ ÉCRITURE ATOMIQUE (2026-10-16) : huit recherches en parallèle écrivaient le même fichier
    et l'ont laissé illisible (« Extra data ») — et un cache illisible se lit comme VIDE, donc
    chaque recherche repartait de zéro sans que rien ne le dise. Fichier temporaire + rename,
    sous verrou, et on fusionne avec ce qu'un autre a écrit entre-temps."""
    try:
        with _LOCK:
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            disque = _load_cache()
            disque.update(cache)
            tmp = _CACHE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(disque, indent=2, ensure_ascii=False))
            os.replace(tmp, _CACHE_PATH)
    except Exception:
        pass


def _miss_recent(key: str) -> bool:
    """Un échec n'est retenu que _MISS_TTL_DAYS jours.

    ⚠ AVANT LE 2026-10-16 UN ÉCHEC ÉTAIT GARDÉ POUR TOUJOURS : 701 noms sur 1 563 figés à « aucun
      domaine », dont Verisure, Ceva, Capgemini Sogeti, Jimini AI — que Clearbit connaît. Un jour
      où l'API répondait mal suffisait à condamner une entreprise. Les anciens `None` du cache
      principal, sans date, sont donc retentés une fois puis datés ici."""
    try:
        ts = json.loads(_MISS_PATH.read_text()).get(key)
    except Exception:
        return False
    return bool(ts) and (time.time() - ts) < _MISS_TTL_DAYS * 86400


def _note_miss(key: str) -> None:
    try:
        with _LOCK:
            try:
                d = json.loads(_MISS_PATH.read_text())
            except Exception:
                d = {}
            d[key] = time.time()
            tmp = _MISS_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False))
            os.replace(tmp, _MISS_PATH)
    except Exception:
        pass


def _clean_name(name: str) -> str:
    """Le nom tel qu'un annuaire le connaît : sans forme juridique, parenthèse ni « Siège de ».

    Clearbit ne rend RIEN pour « Verisure SAS » et rend verisure.fr pour « Verisure »."""
    s = re.sub(r"\([^)]*\)", " ", name or "")
    s = re.sub(r"(?i)^\s*si[eè]ge\s+(?:de\s+l'|de\s+la\s+|du\s+|de\s+)", "", s)
    s = re.sub(r"(?i)(?<![a-z0-9])(?:sasu|sas|sarl|s\.a\.|sa|b\.v\.|bv|gmbh|ltd|inc|llc|plc|se)"
               r"(?![a-z0-9])\.?", " ", s)
    return re.sub(r"\s+", " ", s).strip(" -,&")


def _clearbit(name: str) -> list[dict]:
    url = CLEARBIT_URL + urllib.parse.quote(name)
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.load(r)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _confident(name: str, cand: dict) -> bool:
    """True only when we're confident this domain is THIS company (precision over recall).

    Accept when the domain root equals the company slug (e.g. trustpair → trustpair.fr),
    or the autocomplete name matches exactly on a generic/FR TLD. Reject foreign-country
    TLDs outright — Clearbit often returns a same-name company in another country.
    """
    dom = (cand.get("domain") or "").lower()
    if not dom or "." not in dom:
        return False
    root = dom.split(".")[0]
    tld = dom.rsplit(".", 1)[-1]
    if tld in _FOREIGN_TLDS:
        return False
    if root.replace("-", "") == _slug(name):        # vinci-concessions.com = « VINCI Concessions »
        return True
    if _norm(cand.get("name", "")) == _norm(name) and tld in _GOOD_TLDS:
        return True
    return False


def _score(name: str, cand: dict) -> int:
    """Higher = more likely the same company. Combines name match + TLD preference."""
    n = _norm(name)
    cn = _norm(cand.get("name", ""))
    dom = (cand.get("domain") or "").lower()
    if not dom or "." not in dom:
        return -1
    root = dom.split(".")[0]
    tld = dom.rsplit(".", 1)[-1]
    s = 0
    if cn and cn == n:
        s += 100
    elif cn and (cn.startswith(n) or n.startswith(cn)):
        s += 55
    nt, ct = set(n.split()), set(cn.split())
    s += 12 * len(nt & ct)
    ns = _slug(name)
    root = root.replace("-", "")
    if ns and (ns in root or root in ns):
        s += 45
    s += _TLD_SCORE.get(tld, 0)
    return s


def resolve_domain(name: str, min_score: int = 25, use_cache: bool = True,
                   verify_mx: bool = True) -> str | None:
    """Return the best-guess real email domain for a company name, or None.

    Picks the highest-scoring Clearbit suggestion above `min_score`; if `verify_mx`,
    walks candidates in score order and returns the first whose domain has MX records.
    """
    if not name or not name.strip():
        return None
    key = _norm(name)
    cache = _load_cache() if use_cache else {}
    if use_cache and cache.get(key):
        return cache[key]
    if use_cache and key in cache and _miss_recent(key):
        return None

    # Precision gate first: keep only confident matches, then rank what survives.
    # Le nom BRUT puis le nom NETTOYÉ : « Verisure SAS » ne rend rien, « Verisure » si.
    confident = [c for c in _clearbit(name) if _confident(name, c)]
    propre = _clean_name(name)
    if not confident and propre and propre.lower() != name.strip().lower():
        confident = [c for c in _clearbit(propre) if _confident(propre, c)]
    ranked = sorted(((c, _score(name, c)) for c in confident),
                    key=lambda cs: cs[1], reverse=True)

    chosen: str | None = None
    if ranked:
        top_score = ranked[0][1]
        # The name match decides the winner; MX only disambiguates near-ties.
        near = [c for c, sc in ranked if sc >= top_score - 10]
        chosen = ranked[0][0]["domain"].lower()
        if verify_mx and len(near) > 1:
            try:
                from email_verify import check_mx
            except Exception:
                check_mx = None
            if check_mx is not None:
                for c in near:
                    if check_mx(c["domain"].lower())[0]:
                        chosen = c["domain"].lower()
                        break

    if use_cache:
        if chosen:
            _save_cache({key: chosen})
        else:
            _save_cache({key: None})
            _note_miss(key)
    return chosen


# ── AU-DELÀ DE CLEARBIT (2026-10-16) ───────────────────────────────────────────────────────
# Sa remarque : « he is not finding in many of the offers ». Mesuré sur ses 101 employeurs actifs :
# 40 SANS DOMAINE. Clearbit ne connaît pas la plupart des PME françaises (Nexpublica, Sterimed,
# Coriolis, la MAF…). Deux sources de plus, gratuites, dans cet ordre :
#   1. le domaine ÉCRIT DANS L'ANNONCE (une adresse « @verisure.fr », un « www.… ») ;
#   2. une RECHERCHE WEB (DuckDuckGo, puis Bing quand il se tait) — mesuré, elle rend maf.fr,
#      ceva.com, nexpublica.com, aglgroup.com, sterimedglobal.com, coriolis.com.
# ⚠ UNE RECHERCHE RENVOIE AUSSI N'IMPORTE QUOI (« W Conran Design » -> whatsapp.com). Un
#   candidat n'est gardé que si sa racine porte le NOM (le nom collé, un mot distinctif de 4
#   lettres ou plus, ou le sigle : maf = Mutuelle des Architectes Français), s'il a un serveur de
#   mail, et — dans recruiter — si son site CORROBORE l'annonce. Écrire au mauvais employeur
#   reste pire que ne rien trouver.
_ANNUAIRES = re.compile(
    r"(?:wikipedia|pappers|societe\.com|verif\.com|infogreffe|manageo|linkedin|indeed|"
    r"welcometothejungle|hellowork|glassdoor|facebook|instagram|youtube|twitter|x\.com|"
    r"tiktok|whatsapp|apec|francetravail|pole-emploi|jobteaser|adzuna|meteojob|lefigaro|"
    r"lesechos|boursorama|crunchbase|bing\.|google\.|duckduckgo|annuaire|kompass|"
    r"cnil\.fr|gouv\.fr|labonnealternance|monster|cadremploi|talent\.com|jooble|"
    r"studyrama|letudiant|onisep|zoominfo|dnb\.com|bloomberg|mappy|pagesjaunes)", re.I)
_MOTS_VIDES = {"de", "des", "du", "la", "le", "les", "l", "et", "en", "pour", "the", "of", "and",
               "groupe", "group", "france", "paris", "societe", "institut", "etudes", "services",
               "solutions", "international", "sas", "sa", "maintenance", "academy", "design",
               # Des mots de SECTEUR ou de LIEU : ils ne désignent aucune entreprise à eux seuls
               # (« mutuelle » validait harmonie-mutuelle.fr pour la MAF, « africa » africa.com).
               "africa", "europe", "global", "digital", "data", "tech", "consulting", "conseil",
               "mutuelle", "assurance", "assurances", "banque", "energie", "software",
               "logistics", "networks", "telecom", "systems", "systemes", "industrie", "sante",
               "animale", "infection", "control", "training", "safety", "media", "medias",
               "pages", "market", "abuse", "direct", "emploi", "recrutement", "jobs"}


_SUFFIXES_SIGLE = {"group", "groupe", "france", "fr", "sa", "sas", "corp", "global",
                   "international", "inc", "hq"}


def _racine(dom: str) -> str:
    """« eau.veolia.fr » -> « veolia » : le libellé juste avant le suffixe, sans tiret."""
    parts = dom.lower().removeprefix("www.").split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "gouv") and len(parts[-1]) == 2:
        parts = parts[:-1]
    return (parts[-2] if len(parts) >= 2 else parts[0]).replace("-", "")


def variants(name: str) -> list[str]:
    """« Pages Jaunes / Solocal » -> [le tout, « Pages Jaunes », « Solocal »] : un nom à
    séparateur désigne souvent deux noms d'une même entreprise (marque / société, groupe /
    filiale), et l'annuaire n'en connaît qu'un."""
    out = [name.strip()]
    for part in re.split(r"\s+[/|–-]\s+|\s*\|\s*|\s+/\s*|\s*/\s+", name or ""):
        part = part.strip()
        if len(part) >= 2 and part not in out:
            out.append(part)
    return out


def _etranger(dom: str) -> bool:
    return dom.rsplit(".", 1)[-1] in _FOREIGN_TLDS


def name_matches(dom: str, name: str) -> bool:
    """La racine du domaine porte-t-elle le nom de CETTE entreprise (ou d'une de ses parties) ?"""
    return any(_name_matches_one(dom, v) for v in variants(name))


def _name_matches_one(dom: str, name: str) -> bool:
    root = _racine(dom)
    if len(root) < 2:
        return False
    nom = _norm(_clean_name(name))
    colle = nom.replace(" ", "")
    if colle and len(colle) >= 2 and root.startswith(colle) and root[len(colle):] in _SUFFIXES_SIGLE:
        return True                                  # aglgroup.com = « AGL Groupe »
    if colle and (colle == root or (len(root) >= 4 and (colle.startswith(root) or colle in root)
                                     and min(len(root), len(colle)) / max(len(root), len(colle))
                                     > 0.6)):
        return True
    # ⚠ UN SEUL MOT DOIT FAIRE L'ESSENTIEL DE LA RACINE : sinon « mutuelle » valide
    #   harmonie-mutuelle.fr pour la MAF, « africa » africanews.com, et « ceva » cevalogistics.com
    #   — une AUTRE entreprise. Mesuré sur les faux positifs du 2026-10-16 : tous sous 0,6.
    # ⚠ ET C'EST LE PREMIER MOT DISTINCTIF, pas n'importe lequel : « VINCI Concessions » ne
    #   peut pas être concessions.com. Dans tous les cas justes mesurés (Veolia, Ceva, Sterimed,
    #   Coriolis, Mobilize) le domaine porte le PREMIER mot du nom.
    mots = [m for m in nom.split() if m not in _MOTS_VIDES][:1]
    if any(len(m) >= 4 and m in root and len(m) / len(root) > 0.6 for m in mots):
        return True
    if any(len(m) >= 4 and root.startswith(m) and root[len(m):] in _SUFFIXES_SIGLE for m in mots):
        return True                                  # sterimedglobal.com = « Sterimed … »
    sigle = "".join(m[0] for m in nom.split() if m not in {"de", "des", "du", "la", "le", "l", "et"})
    if len(sigle) < 2:
        return False
    # aglgroup.com = « Africa Global Logistics » : le sigle, plus un suffixe de groupe.
    return root == sigle or (root.startswith(sigle) and root[len(sigle):] in _SUFFIXES_SIGLE)


def posting_domains(name: str, posting: str) -> list[str]:
    """Les domaines écrits dans l'annonce qui portent le nom de l'entreprise."""
    vus = []
    for a, b in re.findall(r"(?:https?://)?(?:www\.)?((?:[a-z0-9-]+\.)+[a-z]{2,6})\b|"
                           r"@((?:[a-z0-9-]+\.)+[a-z]{2,6})\b", (posting or "").lower()):
        d = (a or b).strip(".")
        if d and not _ANNUAIRES.search(d) and name_matches(d, name) and d not in vus:
            vus.append(d)
    return vus


def _recherche(q: str) -> list[str]:
    """Les domaines des premiers résultats : DuckDuckGo, puis Bing s'il ne rend rien."""
    import html as _html
    hdr = {"User-Agent": _UA, "Accept-Language": "fr-FR,fr;q=0.9"}
    out: list[str] = []
    try:
        req = urllib.request.Request("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q),
                                     headers=hdr)
        h = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "replace")
        out = [urllib.parse.urlparse(urllib.parse.unquote(u)).netloc
               for u in re.findall(r'uddg=([^&"]+)', h)]
    except Exception:
        out = []
    if not out:
        try:
            req = urllib.request.Request(
                "https://www.bing.com/search?setlang=fr&cc=FR&q=" + urllib.parse.quote(q), headers=hdr)
            h = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "replace")
            # Bing enveloppe ses liens dans sa propre redirection : on lit l'adresse AFFICHÉE.
            for c in re.findall(r"<cite[^>]*>(.*?)</cite>", h, re.S):
                t = re.sub(r"<[^>]+>", "", _html.unescape(c)).strip()
                t = re.sub(r"^https?://", "", t).split("/")[0].split(" ")[0].split("›")[0]
                out.append(t.strip())
        except Exception:
            pass
    return [d.lower().removeprefix("www.") for d in out if d and "." in d][:8]


def web_domains(name: str) -> list[str]:
    """Les sites officiels probables d'après une recherche web, filtrés par le nom."""
    vus: list[str] = []
    trouves = _recherche(f"{_clean_name(name)} site officiel")
    if not any(name_matches(d, name) and not _ANNUAIRES.search(d) for d in trouves):
        time.sleep(1.0)
        # le nom seul, ou sa seconde partie (« SFR - Altice France » -> « Altice France »)
        autre = variants(name)[-1] if len(variants(name)) > 1 else name
        trouves += _recherche(_clean_name(autre))
    for d in trouves:
        if _ANNUAIRES.search(d) or _etranger(d) or not name_matches(d, name):
            continue
        # Le domaine d'e-mail est le domaine racine : eau.veolia.fr -> veolia.fr.
        parts = d.split(".")
        base = ".".join(parts[-3:]) if len(parts) >= 3 and parts[-2] in ("co", "com") else \
               ".".join(parts[-2:])
        if base not in vus:
            vus.append(base)
    return vus


# ── LE NOM QUE L'ANNONCE DONNE, QUAND LE BOARD EN AFFICHE UN AUTRE (2026-10-16) ─────────────
# « INFORMATIQUE GRAPHISME ENERGETIQUE – I.G.E. » sur France Travail / La Bonne Alternance, et
# l'annonce : « Rejoignez Etap, une société du groupe Schneider Electric ». Le board montre la
# RAISON SOCIALE ; l'annonce dit la MARQUE, celle qui a un site et des recruteurs. Pareil pour
# « W Conran Design » (une agence de Havas) et les offres publiées SOUS le nom d'OpenClassrooms
# pour le compte d'un employeur nommé dans le texte. Sa remarque : « it keeps doing me this ».
_NOM_PROPRE = r"([A-Z][\w&’'.-]*[A-Za-z0-9](?:[ \t]+(?:[A-Z][\w&’'.-]*[A-Za-z0-9]|&)){0,3})"
_PATRONS_EMPLOYEUR = [
    r"\bRejoi(?:ns|gnez)(?:-nous)?[ \t]+(?:l['’]équipe[ \t]+(?:de[ \t]+|d['’])?)?" + _NOM_PROPRE,
    r"\bChez[ \t]+" + _NOM_PROPRE + r"[ \t]*,",
    r"\b(?:À propos de|A propos de|About)[ \t]+" + _NOM_PROPRE,
    r"(?:^|[.!?][ \t]+)" + _NOM_PROPRE + r"[ \t]+(?:recrute|est (?:un|une|le|la|l['’])[ \t]|conçoit|"
    r"développe|accompagne|propose|édite)",
]
_PATRON_GROUPE = r"\b(?:du groupe|filiale (?:du groupe |de ))[ \t]*" + _NOM_PROPRE
_PAS_UN_NOM = {"nous", "vous", "notre", "votre", "le", "la", "les", "l", "alternance", "paris",
               "france", "au", "en", "dans", "rejoignez", "ce", "cette", "il", "elle", "on", "tu",
               "qui", "quel", "quelle", "pour", "description", "missions", "mission", "profil",
               "poste", "entreprise", "société", "groupe", "data", "ia", "contact", "politique",
               "mentions", "facebook", "linkedin", "cadremploi", "indeed", "l'équipe", "équipe",
               "votre", "nos", "ses", "leur", "un", "une", "des", "avec", "sur", "et"}


def employer_names(posting: str, board_name: str = "") -> list[tuple[str, str]]:
    """[(nom, nature)] des employeurs NOMMÉS dans l'annonce : « marque » puis « groupe »."""
    texte = (posting or "")[:4000]
    connu = _norm(_clean_name(board_name))
    out: list[tuple[str, str]] = []
    for patrons, nature in ((_PATRONS_EMPLOYEUR, "marque"), ([_PATRON_GROUPE], "groupe")):
        for p in patrons:
            for m in re.finditer(p, texte):
                n = m.group(1).strip(" .,'’-&")
                if len(n) < 2 or n.split()[0].lower().strip("'’") in _PAS_UN_NOM:
                    continue
                if _norm(n) == connu or any(_norm(n) == _norm(x) for x, _ in out):
                    continue
                out.append((n, nature))
    return out[:4]


def candidates_for(name: str, posting: str = "", mx: bool = True,
                   web: bool = True, cheap: bool = True) -> list[tuple[str, str]]:
    """[(domaine, source)] du plus sûr au moins sûr, dédoublonnés, chacun avec un MX.

    L'appelant (recruiter) les CORROBORE contre l'annonce un par un : cette fonction propose,
    elle ne décide pas."""
    out: list[tuple[str, str]] = []

    def ajoute(d: str, src: str) -> None:
        d = (d or "").lower().strip().removeprefix("www.")
        if d and "." in d and all(d != x for x, _ in out):
            out.append((d, src))

    if cheap:
        for d in posting_domains(name, posting):
            ajoute(d, "écrit dans l'annonce")
        brut = (name or "").strip().lower()
        if re.fullmatch(r"[a-z0-9-]+\.(?:fr|com|io|ai|eu|co|net|org|tech|app)", brut):
            ajoute(brut, "le nom de l'entreprise est un domaine")     # « Qare.fr »
        for v in variants(name)[:3]:
            try:
                ajoute(resolve_domain(v) or "", "annuaire Clearbit")
            except Exception:
                pass
        out = [(d, s) for d, s in out if s == "écrit dans l'annonce" or not _etranger(d)]
    # La recherche web est la source LENTE (deux moteurs, une pause) : recruiter ne la demande
    # que quand les sources rapides n'ont rien donné qui corresponde à l'annonce.
    if web:
        for d in web_domains(name):
            ajoute(d, "recherche web du site officiel")
    if not mx:
        return out
    try:
        from email_verify import check_mx
    except Exception:
        return out
    return [(d, s) for d, s in out if check_mx(d)[0]]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    name = argv[0]
    no_mx = "--no-mx" in argv
    dom = resolve_domain(name, verify_mx=not no_mx)
    if dom:
        print(dom)
        return 0
    print(f"(no domain resolved for {name!r})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
