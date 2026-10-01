"""Lead location sidecar — WHERE a posting actually is, so outreach stops chasing unreachable jobs.

Six of the boards publish a location on every offer (`jobsource.JobListing.location`), and the
scraper threw it away at insert: contacts.xlsx has a frozen 6-column schema with nowhere to put it,
so 1,768 Pending leads carry no geography at all. The digest enforces a hard Île-de-France + ~1h
commuter-ring gate before showing Zineb anything; the OUTREACH half enforced nothing, so the two
halves of the same system disagreed about which jobs she can take.

What that costs, concretely: EKTOR is a 30-person ESN in DIJON. The agent cold-emailed it, spent a
Hunter verification (the binding constraint, ~3 sends a day), drafted a LinkedIn note, and earned a
warm reply inviting an application — for an alternance she would have to move to Dijon to take,
while her M1 is at Université Paris Cité. That is the scarcest resource the system has, spent on a
job that cannot be accepted.

`config.classify_location()` already existed but reads the ROLE TITLE, and titles rarely name a
city: 931 of 940 ranked leads classified as "" and nothing used the result for scoring anyway.

Architecture: raw I/O only, same shape as lead_age.py — a committed sidecar keyed company+role,
normalised so a re-scrape that reformats a title does not mint a new entry. Absence is ALWAYS
neutral: a lead with no recorded location is never penalised, because the 1,768 rows already in the
queue have none and there is no way to recover it (unlike lead_age, which git history could
backfill — no commit ever stored a location). Only newly scraped leads carry one.

    python lead_location.py stats
    python lead_location.py check "EKTOR" "Alternant Data & IA Builder (H/F)"
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_PATH = Path(__file__).parent / "cache" / "lead_locations.json"

# Île-de-France: the 8 departments, plus Paris arrondissement codes (75001-75020).
_IDF_DEPTS = {"75", "77", "78", "91", "92", "93", "94", "95"}

# SA ZONE RÉELLE N'EST PAS « L'ÎLE-DE-FRANCE OU RIEN » (2026-10-01, son instruction : « Paris
# ou l'Île-de-France ou un peu plus loin ou Chartres ou entre Paris et Chartres ou un peu plus
# loin que Chartres, l'important c'est pas trooop loin de Paris »). ELLE HABITE L'EURE-ET-LOIR,
# donc le couloir Paris–Chartres et ce qui le prolonge sont ATTEIGNABLES, alors que le code
# départemental 28 tombait dans le même panier que Marseille.
# Les départements qui TOUCHENT l'Île-de-France, plus le sien. Un code suffit : il n'y a pas
# besoin de connaître la commune.
_RING_DEPTS = {
    "28",        # Eure-et-Loir — Chartres, chez elle, et tout le couloir jusqu'à Rambouillet
    "27",        # Eure — Évreux, Vernon
    "60",        # Oise — Creil, Compiègne, Beauvais
    "45",        # Loiret — Orléans, Montargis
    "02",        # Aisne — Soissons, Château-Thierry
    "89",        # Yonne — Sens, Joigny
    "76",        # Seine-Maritime — Rouen (1 h 15 de train)
}

# Communes hors Île-de-France que les boards écrivent SANS code postal, donc qu'aucun scan de
# département ne peut rattraper. Deux groupes, et le second est nouveau : le couloir
# Paris–Chartres et l'Eure-et-Loir autour, « un peu plus loin que Chartres » comprise.
# Volontairement littérale : une couronne devinée vaut moins que pas de couronne.
_RING = {
    # la couronne ~1 h au nord et à l'est
    "creil", "compiegne", "beauvais", "evreux", "vernon", "rouen", "amiens",
    "sens", "montargis", "soissons", "chateau-thierry", "orleans",
    # le couloir Paris–Chartres, puis l'Eure-et-Loir — SA zone
    "chartres", "dreux", "luce", "lucé", "mainvilliers", "luisant", "le coudray",
    "barjouville", "epernon", "épernon", "maintenon", "gallardon", "auneau",
    "courville-sur-eure", "saint-georges-sur-eure", "thivars", "jouy",
    # « un peu plus loin que Chartres »
    "nogent-le-rotrou", "chateaudun", "châteaudun", "authon-du-perche", "la loupe",
    "illiers-combray", "bonneval", "voves", "janville", "brou", "senonches",
    "anet", "nogent-le-roi", "vernouillet",
}

# Les RÉGIONS dont AUCUNE partie n'est atteignable — ni de Paris, ni de Chartres. C'est le
# champ le plus payant et il était jeté : les boards écrivent la région sur presque chaque
# ligne, et sans elle « Rhône, Auvergne-Rhône-Alpes » et « Isère, Auvergne-Rhône-Alpes »
# ressortaient « lieu inconnu », donc NEUTRES — EDF Lyon notée 100 et Capgemini CHERBOURG
# notée 100 dans son onglet « ★ À postuler » le 2026-10-01. Même leçon que partout ailleurs
# ici : le board avait déjà répondu à la question, on ne lisait pas sa réponse.
# N'y figurent QUE les régions sans aucune partie atteignable. Centre-Val de Loire (Chartres),
# Normandie (Évreux, Vernon), Hauts-de-France (Creil, Compiègne), Bourgogne (Sens) et Grand Est
# en sont volontairement ABSENTES : elles contiennent du proche ET du lointain, donc la région
# seule n'y décide rien, et un faux « hors zone » supprimerait une offre qu'elle pouvait prendre.
_FAR_REGIONS = {
    "auvergne-rhone-alpes", "auvergne-rhône-alpes",
    "nouvelle-aquitaine", "new aquitaine",
    "occitanie",
    "provence-alpes-cote d'azur", "provence-alpes-côte d'azur", "provence alpes cote d'azur",
    "bretagne", "brittany",
    "pays de la loire", "loire region",
    "corse", "corsica",
    "guadeloupe", "martinique", "guyane", "la reunion", "la réunion", "mayotte",
}

# Major French cities that are unambiguously outside the commuter ring. A bare city name with no
# postal code is common on job boards, and without this "Bordeaux" classified as unknown, i.e.
# neutral. Only cities whose name cannot plausibly refer to an Île-de-France commune are listed —
# a false "far" would bury a workable lead, so the list stays conservative and explicit.
_FAR_CITIES = {
    "lyon", "marseille", "bordeaux", "toulouse", "lille", "nantes", "nice", "strasbourg",
    "rennes", "montpellier", "dijon", "grenoble", "brest", "angers", "clermont-ferrand",
    "le mans", "aix-en-provence", "tours", "limoges", "besancon", "besançon", "metz", "nancy",
    "perpignan", "caen", "avignon", "nimes", "nîmes", "saint-etienne", "saint-étienne",
    "toulon", "chalon-sur-saone", "chalon-sur-saône", "lons-le-saunier", "pau", "bayonne",
    # Mesurées dans sa propre liste le 2026-10-01, toutes rendues « lieu inconnu » donc neutres.
    "cherbourg", "le havre", "dieppe", "thionville", "muret", "annecy", "saint-priest",
    "chambery", "chambéry", "valence", "mulhouse", "colmar", "troyes", "poitiers",
    "la rochelle", "vannes", "lorient", "quimper", "saint-nazaire", "niort", "agen",
    "albi", "carcassonne", "beziers", "béziers", "arles", "antibes", "cannes", "ajaccio",
    "bastia", "calais", "dunkerque", "roubaix", "tourcoing", "valenciennes", "arras",
    "lens", "bethune", "béthune", "belfort", "montbeliard", "montbéliard", "macon", "mâcon",
    "bourg-en-bresse", "vichy", "moulins", "nevers", "bourges", "chateauroux", "châteauroux",
    "angouleme", "angoulême", "brive-la-gaillarde", "perigueux", "périgueux",
    "mont-de-marsan", "dax", "tarbes", "rodez", "cahors", "montauban", "narbonne",
    "sete", "sète", "martigues", "aubagne", "gap", "draguignan", "frejus", "fréjus", "grasse",
    "biarritz", "anglet", "la roche-sur-yon", "cholet", "laval", "alencon", "alençon",
    "saint-malo", "saint-brieuc", "chateaubriant", "bergerac", "libourne", "pessac", "talence",
    "begles", "bègles", "merignac", "mérignac", "villeurbanne", "venissieux", "vénissieux",
    "bron", "ecully", "écully", "oyonnax", "roanne", "le puy-en-velay", "aurillac",
}

_REMOTE = re.compile(r"\b(t[ée]l[ée]travail|remote|100\s*%\s*distanciel|full\s*remote|à distance)\b", re.I)


def key(company: str, role: str) -> str:
    """Stable key, identical in spirit to lead_age.key so the two sidecars stay aligned."""
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (s or "").strip().lower()).strip()
    return f"{norm(company)}|{norm(role)}"


def load() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(d: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def record(company: str, role: str, location: str | None) -> bool:
    """Store the board's own location string. FIRST WRITE WINS, like lead_age.

    Returns True if this was new. An empty location writes nothing: "unknown" and "recorded as
    nothing" must stay distinguishable, or a board that omits the field would look like a verdict.
    """
    loc = (location or "").strip()
    if not loc:
        return False
    d = load()
    k = key(company, role)
    if k in d:
        return False
    d[k] = loc[:120]
    save(d)
    return True


def get(company: str, role: str) -> str | None:
    return load().get(key(company, role))


def _mots(ensemble: set[str]) -> re.Pattern:
    """Correspondance par MOT ENTIER, jamais par sous-chaîne.

    `"pau" in "saint-paul"` est vrai, donc toute commune nommée Saint-Paul était déclarée
    « hors zone » — et un faux « hors zone » supprime une offre qu'elle pouvait prendre, en
    silence. Même piège avec « sens » et « tours ». Le trait d'union compte comme une frontière
    de mot, donc « nogent-le-rotrou » ne peut pas être confondu avec Nogent-sur-Marne (94).
    """
    corps = "|".join(sorted((re.escape(t) for t in ensemble), key=len, reverse=True))
    # LE GROUPE N'EST PAS DÉCORATIF. Sans `(?:…)`, « | » ayant la précédence la plus faible, les
    # deux limites ne s'appliquent qu'au PREMIER et au DERNIER terme : tout le milieu de la liste
    # redevient une recherche de sous-chaîne, et « pau » mordait dans « Saint-Paul » exactement
    # comme avant. Attrapé par le test, pas à la lecture.
    return re.compile(rf"(?<![a-z\u00e0-\u00ff])(?:{corps})(?![a-z\u00e0-\u00ff])", re.I)


_RING_RE = _mots(_RING)
_FAR_RE = _mots(_FAR_CITIES)
_FAR_REGION_RE = _mots(_FAR_REGIONS)
_IDF_NAME_RE = re.compile(r"(?<![a-z])(paris|[iî]le[- ]de[- ]france)(?![a-z])", re.I)


def _par_nom(texte: str) -> str:
    """Le verdict lisible dans les NOMS de `texte`, sans jamais regarder un nombre.

    Séparé exprès du scan de codes : appliqué à un INTITULÉ de poste, le scan de départements
    lirait « Alternance Data 2026 » comme le département 20 (la Corse) et la déclarerait hors
    zone. C'est la famille de bugs que ce dépôt paie en boucle (`_NOMBRE`, « agent de maîtrise »,
    « apprentissage automatique ») : un chiffre dans une phrase n'est pas une donnée structurée.
    """
    if _IDF_NAME_RE.search(texte):
        return "idf"
    if _RING_RE.search(texte):
        return "ring"          # Chartres avant la région : le couloir l'emporte sur le panier
    if _FAR_RE.search(texte) or _FAR_REGION_RE.search(texte):
        return "far"
    return "unknown"


def classify(location: str | None, role: str | None = None) -> str:
    """'idf' | 'ring' | 'remote' | 'far' | 'unknown' — how reachable the posting is from home.

    'remote' wins over any place name: a remote role in Lyon is workable from Île-de-France, and
    that is the whole point of checking. 'unknown' is returned for anything unrecognised, and
    callers must treat it as neutral — never as 'far'.

    `role` est un SECOND TÉMOIN, lu seulement quand le lieu ne dit rien. Les boards publient
    souvent une région ou un bassin d'emploi inexploitable là où l'intitulé nomme la ville :
    « Alternance Chargé(e) de Projet IA — Grenoble (F/H) » avec pour lieu « Isère,
    Auvergne-Rhône-Alpes ». Il ne peut que trancher un « unknown », jamais contredire un verdict.
    """
    s = (location or "").strip()
    if not s:
        return _par_nom(role) if role else "unknown"
    if _REMOTE.search(s):
        return "remote"
    low = s.lower()
    # "PARIS 14" IS THE 14th ARRONDISSEMENT, NOT THE CALVADOS. The department scan below reads
    # any isolated two-digit number as a department code, so every arrondissement from the 10th
    # to the 20th — none of which is an Île-de-France department number — was classified 'far'.
    # That is Station F (13th), and the 11th, 12th, 15th, 17th, 18th, 19th and 20th with it:
    # "Paris 14 - Ile-de-France - France" came back 'far' while literally saying Île-de-France
    # twice. Found 2026-09-28 on a real Docaposte row in the web UI's store.
    # The NAME is stronger evidence than a bare number sitting next to it, so it is read first.
    # (The single-digit arrondissements were safe by accident: the pattern needs two digits.)
    if _IDF_NAME_RE.search(low):
        return "idf"
    # Le nom de la ville ou du département passe AVANT le code, pour la même raison : « Chartres
    # — 28 » et « Chartres, Centre-Val de Loire » doivent rendre le même verdict.
    if _RING_RE.search(low):
        return "ring"
    # A French postal/department code anywhere in the string ("75 - PARIS", "Lyon - 69", "69003").
    codes = re.findall(r"\b(\d{2})\d{0,3}\b", s)
    if codes:
        if any(c in _IDF_DEPTS for c in codes):
            return "idf"
        # SON DÉPARTEMENT ET CEUX QUI TOUCHENT L'ÎLE-DE-FRANCE sont atteignables. Avant, « 28 »
        # (l'Eure-et-Loir, où elle habite) n'étant pas francilien, toute offre à Châteaudun ou
        # à Épernon tombait « hors zone » à −40, à égalité avec Marseille.
        if any(c in _RING_DEPTS for c in codes):
            return "ring"
        # A code we recognised as a real department, and it is not Île-de-France.
        # 97x / 98x (outre-mer) sortaient de la fourchette "01".."95", donc restaient NEUTRES :
        # une alternance à La Réunion n'était pas pénalisée.
        if any(c.isdigit() and ("01" <= c <= "95" or c in ("97", "98")) for c in codes):
            return "far"
    if _FAR_RE.search(low) or _FAR_REGION_RE.search(low):
        return "far"
    # Le lieu n'a rien dit : l'intitulé peut encore nommer la ville.
    return _par_nom(role) if role else "unknown"


def reachability(company: str, role: str) -> str:
    """classify() for a stored lead. 'unknown' when nothing was recorded — always neutral."""
    return classify(get(company, role), role)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "check" and len(args) >= 3:
        loc = get(args[1], args[2])
        print(f"{args[1]} · {args[2]}\n  location: {loc or '(not recorded)'}\n  → {classify(loc)}")
    else:
        import collections
        d = load()
        c = collections.Counter(classify(v) for v in d.values())
        print(f"{len(d)} leads with a recorded location")
        for k, n in c.most_common():
            print(f"  {k:8} {n:>5}")
        if not d:
            print("  (empty — locations are recorded from the next /scrape onward;\n"
                  "   existing rows cannot be backfilled, no commit ever stored one)")
