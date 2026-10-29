"""À QUI écrire pour une offre donnée, et avec quelle preuve.

SON INSTRUCTION (2026-10-08) : « each time i am doing an offer i have a button there after
creating the pack to send an email, when i click on it the system will search for the recruiter of
that offer or responsible in that company, find their emails and send them an email to inform them
of my candidature and show them my motivation ».

CE MODULE NE DEVINE JAMAIS UNE ADRESSE EN SILENCE. Il rend des candidats AVEC leur preuve, et
l'interface les montre avant tout envoi. Le refus est une réponse normale et fréquente.

⚠ LE DANGER N'EST PAS DE NE RIEN TROUVER, C'EST D'ÉCRIRE À UN INCONNU. `company_resolver` sert
  d'ordinaire à deviner un domaine pour du démarchage, où une erreur rebondit sans conséquence et
  où la vérification l'attrape. Ici l'email part en son nom à une entreprise chez qui elle vient de
  candidater : se tromper d'entreprise est pire que de ne pas écrire. Mesuré le 2026-10-07,
  « Softeam » résout vers softeamitalia.com, un distributeur italien de machines à croquettes dont
  la page s'intitule « SofTeam – distributore di innovazione » — donc vérifier le NOM ne suffit
  pas. Le domaine doit CORROBORER l'annonce, et c'est `company_brief.corroborates` qui le dit.

⚠ CE MODULE NE DÉCIDE PAS DE L'ENVOI. Les cinq refus de `smtp_send` (plafond, doublon, liste de
  rebonds, vérification, preuve de boîte) restent les juges. On expose ce qu'ILS vont dire, pour
  qu'elle le voie avant de cliquer, au lieu de le redécouvrir après.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Les boîtes génériques à essayer, DANS CET ORDRE : chaque essai dépense une vérification Hunter
# (~100 par mois), donc l'ordre est le budget. Recrutement d'abord parce que c'est le service
# concerné ; `contact` en dernier parce qu'il arrive au standard.
GENERIQUES = ("recrutement", "recrutements", "rh", "jobs", "careers", "recruitment", "talent",
              "candidature", "emploi", "contact")

# Ce qui PROUVE que la boîte existe, par opposition à « le domaine répond » — même vocabulaire que
# smtp_send, volontairement : si les deux divergeaient, l'aperçu mentirait sur ce que fera l'envoi.
FORT = {"smtp_ok", "api_valid"}
FAIBLE = {"api_risky", "catch_all"}
# ⚠ UNE ADRESSE QUE L'EMPLOYEUR PUBLIE LUI-MÊME est une preuve en soi (2026-10-29). L'annonce
#   Flowt écrit son adresse jobs@ ; le code ne lisait pas les adresses de l'annonce, devinait
#   `recrutement@`, `recrutements@`, `rh@` — trois vérifications — et s'arrêtait AVANT `jobs@`.
#   Elle n'est pas vérifiée ici (aucun quota dépensé) : smtp_send la vérifie à l'envoi.
PUBLIEE = {"publiée dans l'annonce", "publiée sur son site"}
# Ce qui dit « cette adresse n'existe pas » : jamais cochée d'office.
MORTE = {"api_invalid", "unverifiable"}


@dataclass
class Piste:
    address: str
    name: str = ""
    title: str = ""
    source: str = ""
    verification: str = ""
    generic: bool = False
    blocked: str = ""
    notes: list[str] = field(default_factory=list)
    fonction: int = 0
    confidence: int | None = None

    @property
    def confirmed(self) -> bool:
        return self.verification in FORT or self.verification in PUBLIEE

    @property
    def sendable(self) -> bool:
        """Ce que la porte de smtp_send dira. On ne la double pas, on la cite."""
        if self.blocked:
            return False
        if self.confirmed:
            return True
        # Une générique peut partir sur une preuve faible (un catch-all ne peut pas rebondir
        # durement) ; une adresse PERSONNELLE devinée, non.
        return self.generic and self.verification in FAIBLE

    @property
    def suggested(self) -> bool:
        """Cochée d'office dans l'interface. Elle décoche ; on ne coche pas une adresse morte.

        ⚠ LE 2026-10-19, « TOUTES LES ADRESSES » COMPRENAIT LES MORTES : l'interface mettait en
          file chaque piste non bloquée, y compris une adresse personnelle chez Qare que Hunter
          avait déclarée invalide — refusée à 7h, et une place du jour perdue avec elle.
        """
        if self.blocked or self.verification in MORTE:
            return False
        if self.sendable:
            return True
        # Une personne de l'annuaire non encore vérifiée : cochée si Hunter y croit ET si sa
        # fonction a un rapport avec le poste. Un « Software Engineer » au hasard ne l'est pas.
        return (self.confidence or 0) >= 85 and self.fonction >= 60


# À QUI ÉCRIT-ON POUR UNE CANDIDATURE ? Pas au même qu'un démarchage. `contact_finder.score_title`
# existe mais il est réglé pour vendre à une startup — CTO 100, Talent Acquisition 30, et ZÉRO
# pour « TechLead » comme pour « Responsable RH ». Ici l'ordre est inverse : la personne qui
# RECRUTE d'abord, puis celle qui encadrerait le poste, et jamais le marketing ni le design.
# Mesuré sur chapsvision.fr, Hunter connaît 10 personnes dont un TechLead, un Cloud Engineer et
# un Graphic Design Manager : sans ce tri, rien ne distingue les trois.
_FONCTIONS = (
    (100, ("talent acquisition", "recrutement", "recruteur", "recruteuse", "recruiter",
           "responsable rh", "drh", "ressources humaines", "human resources", "people partner",
           "talent manager", "chargé de recrutement", "chargée de recrutement", "hrbp")),
    (80, ("responsable formation", "alternance", "apprentissage", "campus manager",
          "relations écoles", "university relations", "early careers")),
    (70, ("cto", "chief technology", "vp engineering", "head of engineering", "head of data",
          "head of ai", "directeur technique", "directrice technique")),
    (60, ("techlead", "tech lead", "lead developer", "lead data", "engineering manager",
          "responsable technique", "architecte")),
    (40, ("data scientist", "data engineer", "machine learning", "ml engineer", "ia ", " ai ",
          "backend", "devops", "mlops", "software engineer", "développeur", "developpeur",
          "ingénieur", "ingenieur")),
    (20, ("ceo", "cofounder", "co-founder", "fondateur", "fondatrice", "président",
          "directeur général", "directrice générale")),
)
# Jamais ces fonctions : elles n'ont aucun pouvoir sur une candidature technique et écrire à la
# mauvaise personne coûte la seule impression qu'elle fera.
_HORS_SUJET = ("marketing", "communication", "graphic", "design", "sales", "commercial",
               "account executive", "business development", "webmaster", "presales",
               "avant-vente", "comptab", "finance", "juridique", "legal", "office manager")


def score_fonction(position: str, department: str = "") -> int:
    t = f"{position} {department}".lower()
    if any(x in t for x in _HORS_SUJET):
        return -1
    for pts, mots in _FONCTIONS:
        if any(m in t for m in mots):
            return pts
    return 5          # une personne sans fonction connue vaut mieux qu'une boîte générique


def _domaine_corrobore(company: str, posting: str) -> tuple[str, str]:
    """(domaine, raison). Domaine vide = on n'écrira pas, et la raison le dit.

    ⚠ PLUSIEURS CANDIDATS, PAS UN SEUL (2026-10-16, sa remarque : « he is not finding in many of
      the offers »). Mesuré sur ses 101 employeurs actifs : 40 sans domaine, parce que seul
      Clearbit était interrogé et qu'un échec y restait gravé pour toujours. `candidates_for`
      ajoute le domaine écrit dans l'annonce et une recherche web du site officiel ; chacun
      passe ENSUITE la corroboration ici. La règle de fond ne change pas : un domaine qui ne
      correspond pas n'est jamais pris.
    ⚠ LE MEILLEUR, PLUS LE PREMIER (2026-10-29). Le premier candidat corroboré gagnait, et la
      corroboration seule se trompe dans les deux sens — mesuré sur ses 44 dernières offres :
        · « BE CLOUD » -> cloud.fr, une AUTRE entreprise, passée à 3 % de vocabulaire commun
          (« cloud », « microsoft »… les mots du poste, pas ceux de l'employeur), et la même page
          tombait à 1 % le lendemain : le verdict dépendait du jour ;
        · « Technip Energies » -> cybernetix.fr, une filiale, prise avant technipenergies.com ;
        · Shiseido, STATION F et MBDA REFUSÉS sur leur propre domaine, parce qu'une page
          d'accueil qui vend des crèmes ne parle pas de tableaux de bord.
      Chaque candidat reçoit donc une CLASSE (`_evalue`) et la meilleure l'emporte.
    """
    try:
        import company_resolver
    except Exception as exc:
        return "", f"résolution impossible ({type(exc).__name__})"
    refus: list[str] = []
    essayes: set[str] = set()
    meilleur = (0, "", "")                   # (classe, domaine, raison)

    def essaie(nom: str, etape: str, n: int, suffixe: str = "") -> None:
        nonlocal meilleur
        try:
            cands = company_resolver.candidates_for(nom, posting, web=(etape == "web"),
                                                    cheap=(etape == "rapide"))
        except Exception:
            cands = []
        cands = [(d, s) for d, s in cands if d not in essayes]
        essayes.update(d for d, _ in cands)
        if not posting:
            # Sans annonce on ne peut rien corroborer : on le DIT, et on préfère un domaine qui
            # porte le nom à un domaine qui ne le porte pas.
            for dom, src in sorted(cands, key=lambda ds: not company_resolver.name_matches(ds[0],
                                                                                        nom)):
                if meilleur[0] < 1:
                    meilleur = (1, dom, f"domaine non corroboré (pas de texte d'annonce à comparer "
                                        f"; source : {src})")
            return
        # Ceux qui portent le nom d'abord : à classe égale, ils l'emportent.
        cands.sort(key=lambda ds: not company_resolver.name_matches(ds[0], nom))
        for dom, src in cands[:n]:
            classe, raison = _evalue(nom, posting, dom, src)
            if classe == 0:
                refus.append(raison)
            elif classe > meilleur[0]:
                meilleur = (classe, dom, raison + suffixe)
            if meilleur[0] >= 4:
                return

    # LES SOURCES RAPIDES D'ABORD (annonce, nom, Clearbit), la recherche web ensuite et
    # seulement si elles n'ont rien donné de sûr : ~25 s par entreprise sinon, à chaque clic.
    essaie(company, "rapide", 4)
    if meilleur[0] < 3:
        essaie(company, "web", 4)
    # ⚠ LE BOARD AFFICHE PARFOIS LA RAISON SOCIALE, L'ANNONCE NOMME LA MARQUE (2026-10-16) :
    #   « IGE » contre « Rejoignez Etap, une société du groupe Schneider Electric ». Quand le nom
    #   du board n'a rien donné de sûr, on essaie les employeurs que l'annonce nomme — la marque
    #   d'abord, le groupe ensuite — et ils passent la MÊME évaluation.
    if posting and meilleur[0] < 3:
        for autre, nature in company_resolver.employer_names(posting, company):
            suffixe = (f" — employeur nommé dans l'annonce : « {autre} »"
                       + (" (groupe)" if nature == "groupe" else ""))
            for etape in ("rapide", "web"):
                if meilleur[0] >= 3:
                    break
                essaie(autre, etape, 3, suffixe)
    if meilleur[0]:
        return meilleur[1], meilleur[2]
    if not essayes:
        return "", "aucun domaine trouvé pour ce nom d'entreprise (annuaire, annonce et web)"
    return "", (f"aucun candidat ne correspond à cette annonce : {', '.join(refus[:4])} — "
                f"probablement une autre entreprise du même nom. On n'écrit pas.")


# Au-dessus, une page qui ne porte pas le nom de l'entreprise parle quand même manifestement de
# son métier (cybernetix.fr, filiale de Technip Energies : 10 %). cloud.fr, l'homonyme de
# « BE CLOUD », oscillait entre 1 et 3 %.
_FORT_RECOUVREMENT = 0.08


def _evalue(company: str, posting: str, dom: str, src: str) -> tuple[int, str]:
    """(classe, raison) d'UN domaine candidat. 0 = refusé (et la raison dit pourquoi).

      4  la page corrobore l'annonce ET le domaine porte le nom (ou l'annonce l'écrit)
      3  le domaine EST le nom (shiseido.com) et la page ne contredit pas — elle parle juste
         d'autre chose que du poste
      2  la page corrobore fortement l'annonce sans porter le nom (une filiale, une marque)
      1  le site ne répond pas, mais le domaine porte le nom : rien ne le dément
    """
    import company_brief
    import company_resolver as cr
    import descriptions
    nomme = cr.name_matches(dom, company) or src == "écrit dans l'annonce"
    exact = cr.strong_name_match(dom, company)
    try:
        page = " ".join(descriptions._page_text(descriptions._get("https://" + dom)).split())
        ok, part, communs = company_brief.corroborates(page, posting, company)
    except Exception as exc:
        return ((1, f"domaine non corroboré (site injoignable : {type(exc).__name__} ; source : "
                    f"{src})") if nomme else (0, f"{dom} (injoignable, ne porte pas le nom)"))
    # ⚠ UNE PAGE VIDE NE DÉMENT RIEN : thalesgroup.com rend une coquille JavaScript, 0,0 %
    #   de vocabulaire commun faute de texte, et Thales était refusé comme « une autre
    #   entreprise du même nom ». Moins de 150 mots lisibles = non concluant, pas un refus.
    if not ok and len(page.split()) < 150:
        return ((1, f"domaine non corroboré (site injoignable : page sans texte lisible ; source "
                    f": {src})") if nomme else (0, f"{dom} (page illisible, ne porte pas le nom)"))
    if not ok:
        # ⚠ UN SITE EN ANGLAIS NE PARTAGE PAS LE VOCABULAIRE D'UNE ANNONCE EN FRANÇAIS
        #   (2026-10-16) : etap.com rendait 1,3 % et Etap était refusé, alors que etap.com/fr
        #   en rend 6,8 %. Avant de refuser, on lit la version française du site.
        for chemin in ("/fr", "/fr/", "/fr-fr"):
            try:
                fr = " ".join(descriptions._page_text(
                    descriptions._get("https://" + dom + chemin)).split())
            except Exception:
                continue
            ok_fr, part_fr, communs_fr = company_brief.corroborates(fr, posting, company)
            if ok_fr:
                ok, part, communs = True, part_fr, communs_fr
                break
    if ok and nomme:
        return 4, f"{dom} corroboré par l'annonce ({part:.0%} de vocabulaire commun ; {src})"
    if exact and part >= 0.01 and len(communs) >= 2:
        return 3, (f"{dom} porte exactement le nom de l'entreprise (sa page d'accueil parle peu "
                   f"du poste : {part:.1%} de vocabulaire commun ; {src})")
    if ok and part >= _FORT_RECOUVREMENT:
        return 2, (f"{dom} corroboré par l'annonce ({part:.0%} de vocabulaire commun ; {src}) — "
                   f"le domaine ne porte pas le nom : filiale ou marque ?")
    return 0, f"{dom} ({part:.1%}{'' if nomme else ', ne porte pas le nom'})"


def _verifie(address: str) -> str:
    try:
        import email_verify
        ok, tier, _ = email_verify.verify(address)
        return tier or ("ok" if ok else "unverifiable")
    except Exception as exc:
        return f"verif_indisponible:{type(exc).__name__}"


def _bloque(address: str) -> str:
    try:
        import bounce_guard
        bloque, pourquoi = bounce_guard.is_blocked(address)
    except Exception:
        return ""          # un défaut de lecture de la liste ne doit pas bloquer une candidature
    return (pourquoi or "cette adresse a déjà rebondi") if bloque else ""


# COMBIEN DE PERSONNES A-T-ELLE DÉJÀ PRÉVENUES POUR CETTE OFFRE ? (2026-10-08, sa question :
# « the system to how many people sends the mail ? for the same offer »).
# UNE SEULE PAR CLIC — mais rien ne l'empêchait d'en viser une deuxième par les « autres pistes »,
# et rien ne le lui DISAIT. Le garde anti-doublon de smtp_send est indexé sur l'ADRESSE : un
# second destinataire chez le même employeur passe sans un mot. Le risque est précis — trois
# personnes d'une même équipe qui reçoivent des messages voisins et se parlent, c'est de
# l'arrosage, soit l'inverse du but.
def deja_prevenus(company: str, role: str = "") -> dict:
    """{'count': n, 'who': [adresses]} — ce qui est déjà parti pour cette offre."""
    out = {"count": 0, "who": []}
    try:
        import tracker
        df = tracker.load()
    except Exception:
        return out
    c, r = (company or "").strip().lower(), (role or "").strip().lower()
    for _, row in df.iterrows():
        if (str(row.get("Company") or "").strip().lower() != c):
            continue
        # Le rôle départage : un gros employeur a plusieurs offres, et prévenir quelqu'un pour
        # l'une n'a rien à voir avec l'autre.
        if r and str(row.get("Role") or "").strip().lower() != r:
            continue
        log = str(row.get("Conversation Log") or "")
        envois = len(re.findall(r"\bAgent\s*:", log))
        if envois:
            out["count"] += envois
            adresse = str(row.get("Contact Email") or "").strip()
            if adresse:
                out["who"].append(adresse)
    return out


# ── LES ADRESSES QUE L'EMPLOYEUR PUBLIE LUI-MÊME (2026-10-29) ─────────────────────────────────
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,10}", re.I)
# Jamais un destinataire de candidature, même publié : la protection des données, les robots.
_LOCAUX_EXCLUS = re.compile(r"^(dpo|rgpd|gdpr|privacy|donnees|données|dataprotection|cnil|"
                            r"no-?reply|do-?not-?reply|ne-?pas-?repondre|unsubscribe|desinscription|"
                            r"webmaster|postmaster|abuse|newsletter|presse|press|media|invoice|"
                            r"factur\w*|compta\w*|sav|support|service-?client\w*)$", re.I)
# Une adresse d'un autre domaine n'est retenue que si la phrase autour dit d'y CANDIDATER.
_CONTEXTE_CANDIDATURE = re.compile(r"candidat|\bcv\b|postul|envoy|adress|lettre de motivation|"
                                   r"apply|send (us )?your|resume|recrutement|recruitment", re.I)


def emails_published(texte: str, company: str, domaine: str = "") -> list[str]:
    """Les adresses écrites dans un texte (l'annonce, une page du site) qu'on peut lui écrire.

    Gardées : celles du domaine de l'entreprise (ou d'un domaine qui porte son nom), et celles
    qu'une phrase présente comme l'adresse de candidature. Jamais les plateformes (HelloWork
    signe chaque page de son adresse dpo@), jamais la protection des données ni les robots.
    """
    import company_resolver as cr
    vus: list[str] = []
    t = re.sub(r"\s*(\[at\]|\(at\)|\[arobase\]|\(arobase\))\s*", "@", texte or "", flags=re.I)
    for m in _EMAIL.finditer(t):
        adresse = m.group(0).strip(".").lower()
        local, _, dom = adresse.partition("@")
        if not dom or "." not in dom or adresse in vus:
            continue
        if cr._ANNUAIRES.search(dom) or _LOCAUX_EXCLUS.match(local):
            continue
        if re.search(r"\.(png|jpe?g|gif|svg|webp)$", dom):        # une image « logo@2x »
            continue
        sien = (domaine and (dom == domaine or dom.endswith("." + domaine))) or \
            cr.name_matches(dom, company)
        autour = t[max(0, m.start() - 160): m.end() + 40]
        if sien or _CONTEXTE_CANDIDATURE.search(autour):
            vus.append(adresse)
    return vus


# Les pages où une entreprise française écrit ses adresses : la page carrières, le contact, et
# les mentions légales, que la loi l'oblige à publier.
_PAGES_SITE = ("/recrutement", "/carrieres", "/careers", "/nous-rejoindre", "/contact",
               "/nous-contacter", "/mentions-legales")


def _emails_du_site(domaine: str, company: str) -> list[str]:
    """Gratuit (aucun quota), plus lent : ~1 s par page. Seulement quand le reste n'a rien donné."""
    import descriptions
    out: list[str] = []
    for chemin in ("",) + _PAGES_SITE:
        try:
            html = descriptions._get(f"https://{domaine}{chemin}")
        except Exception:
            continue
        for a in emails_published(html, company, domaine):
            if a not in out and (a.endswith("@" + domaine) or a.endswith("." + domaine)):
                out.append(a)
        if len(out) >= 3:
            break
    return out


def _hunter_par_nom(company: str, dom: str, compte: dict | None, posting: str,
                    notes: list[str]) -> tuple[str, list[dict]]:
    """(domaine des adresses, personnes) quand Hunter connaît l'entreprise SOUS UN AUTRE DOMAINE.

    ⚠ LE SITE N'EST PAS TOUJOURS LE DOMAINE DES ADRESSES (2026-10-29). Groupe Pasteur
      Mutualité : site groupepasteurmutualite.fr, Hunter n'y connaît personne — il en connaît 63,
      toutes en @gpm.fr. Le code concluait « personne », devinait `recrutement@`, `rh@`… sur le
      mauvais domaine, et rien ne partait. Le compte par NOM est gratuit ; la recherche ne se fait
      que s'il annonce nettement plus de monde qu'au domaine du site.
    ⚠ ET LE DOMAINE RENDU EST VÉRIFIÉ comme n'importe quel autre : Hunter résout un nom comme tout
      le monde, et un homonyme reste possible. Il doit porter le nom, ou l'organisation de Hunter
      doit être celle de l'annonce, ou sa page doit corroborer l'annonce.
    """
    import company_resolver as cr
    import email_verify
    ici = (compte or {}).get("total") or 0
    if dom and ici >= 5:
        return "", []
    par_nom = email_verify.hunter_count_company(company)
    if not par_nom or par_nom["total"] <= max(ici * 3, 0) or par_nom["total"] == 0:
        return "", []
    d = par_nom.get("department") or {}
    services = ",".join(x for x in ("hr", "it") if d.get(x)) if par_nom["total"] > 10 else ""
    hdom, org, gens = email_verify.hunter_people_company(company, 10, services)
    if services and not any((g.get("type") or "") != "generic" for g in gens):
        hdom2, org2, gens2 = email_verify.hunter_people_company(company, 10)
        hdom, org, gens = (hdom2 or hdom), (org2 or org), gens2
    if not hdom or hdom == dom:
        return "", []
    meme_nom = cr._norm(org) == cr._norm(company) if org else False
    preuve = ""
    if posting:
        # La même évaluation que pour le site : un nom court (« Buddy ») a des homonymes
        # partout, et c'est l'annonce qui départage.
        classe, raison = _evalue(company, posting, hdom, "Hunter, depuis le nom de l'entreprise")
        if classe >= 2 or (classe == 1 and meme_nom):
            preuve = raison
    elif cr.name_matches(hdom, company) and meme_nom:
        preuve = (f"NON corroboré faute de texte d'annonce : le domaine porte le nom et Hunter "
                  f"l'attribue à « {org} » — vérifie avant d'envoyer")
    if not preuve:
        notes.append(f"Hunter associe « {company} » à {hdom}, mais rien ne confirme que c'est la "
                     f"même entreprise — ignoré")
        return "", []
    notes.append(f"adresses de l'entreprise sur {hdom} et non sur {dom or 'son site'} — Hunter y "
                 f"connaît {par_nom['total']} personne(s) ({preuve})")
    return hdom, gens


def find_for_offer(company: str, role: str = "", posting: str = "", job_url: str = "",
                   use_browser: bool = True, budget: int = 3) -> dict:
    """Les pistes pour écrire à cet employeur, la meilleure d'abord.

    `budget` plafonne les VÉRIFICATIONS dépensées (Hunter donne ~100 par mois et chaque essai en
    consomme une). Trois suffisent : une personne nommée plus deux boîtes génériques.
    """
    out: dict = {"company": company, "role": role, "domain": "", "why": "",
                 "candidates": [], "best": None, "notes": []}
    dom, raison = _domaine_corrobore(company, posting)
    out["domain"], out["why"] = dom, raison

    pistes: list[Piste] = []
    depenses = 0

    # 0 — CE QUE L'ANNONCE DIT ELLE-MÊME. « Envoyez votre CV à jobs@… » vaut mieux que
    #     toutes les déductions : c'est l'employeur qui désigne la boîte. Lue même sans domaine.
    for adresse in emails_published(posting, company, dom):
        local = adresse.split("@")[0]
        p = Piste(address=adresse, source="écrite dans l'annonce",
                  verification="publiée dans l'annonce",
                  generic=local in GENERIQUES or not re.search(r"[a-z]{2,}[._-][a-z]{2,}", local))
        p.blocked = _bloque(adresse)
        pistes.append(p)
    if not dom and not pistes:
        return out

    # 1 — QUI TRAVAILLE LÀ, demandé à Hunter. Une seule requête rend les adresses qu'il connaît
    #     AVEC prénom, nom et fonction — là où deviner `recrutement@` coûte une vérification par
    #     essai et échoue le plus souvent (mesuré sur ChapsVision : les trois génériques tentées
    #     sont déclarées undeliverable). On trie par fonction, on ne vérifie que la MEILLEURE :
    #     c'est une vérification pour une candidature, pas trois pour rien.
    # ⚠ D'ABORD COMPTER, GRATUITEMENT (2026-10-16). Le plan gratuit rend 10 personnes par
    #   recherche : chez un grand employeur ce sont 10 personnes AU HASARD, souvent de la vente,
    #   et la recherche finissait sur « aucune dont la fonction ait un rapport ». `hunter_count`
    #   ne coûte rien et dit qui est connu, par service : on ne dépense la recherche que s'il y a
    #   quelqu'un, et on la FILTRE sur les services qui recrutent ou encadreraient le poste.
    gens: list[dict] = []
    compte = None
    try:
        import email_verify
        compte = email_verify.hunter_count(dom) if dom else None
        if compte is not None and compte["total"] == 0:
            out["notes"].append(f"Hunter ne connaît personne sur {dom} — aucune recherche dépensée")
        elif dom:
            services = ""
            if compte and compte["total"] > 10:
                d = compte.get("department") or {}
                # RH (qui recrute) et informatique (qui encadrerait le poste) d'abord. Mesuré sur
                # Mobilize FS : avec « management » dans le filtre, les 10 places sont parties à
                # des directeurs FINANCIERS pour un poste de Data Analyst.
                services = ",".join(x for x in ("hr", "it") if d.get(x)) or \
                           ",".join(x for x in ("management", "executive") if d.get(x))
            gens = email_verify.hunter_people(dom, limit=10, department=services)
            if services and not any((g.get("type") or "") != "generic" for g in gens):
                gens = email_verify.hunter_people(dom, limit=10)     # le filtre n'a rien rendu
        out["hunter_known"] = (compte or {}).get("total")
        # Le domaine du site n'est pas forcément celui des adresses (gpm.fr) — et sans domaine
        # du tout, Hunter peut connaître l'entreprise par son nom (MSC). Pas quand l'annonce a
        # déjà donné son adresse : une recherche coûte 1 sur 50 par mois (Flowt : dépensée pour
        # trouver un homonyme, flowtnow.com, alors que son adresse jobs@ était sous nos yeux).
        if not any(x.sendable for x in pistes):
            hdom, hgens = _hunter_par_nom(company, dom, compte, posting, out["notes"])
            if hdom:
                dom, gens = hdom, hgens
                out["domain"] = hdom
                out["hunter_known"] = len(hgens)
        if (out.get("hunter_known") or 0) > 300:
            out["notes"].append("grand groupe : l'annuaire mélange tous les pays — vérifie sur "
                                "LinkedIn que la personne choisie travaille en France")
    except Exception as exc:
        out["notes"].append(f"annuaire du domaine indisponible : {type(exc).__name__}")
    if not dom and not pistes:
        return out
    classes = sorted(((score_fonction(g["position"], g["department"]), g) for g in gens
                      if (g.get("type") or "personal") != "generic"),
                     key=lambda sg: -sg[0])
    out["people_found"] = len(gens)
    for note, g in classes[:1]:
        if note <= 0:
            out["notes"].append(f"{len(gens)} personne(s) connue(s) du domaine, mais aucune dont "
                                f"la fonction ait un rapport avec le recrutement ou la technique.")
            break
        p = Piste(address=g["address"], name=g["name"], title=g["position"],
                  source=f"annuaire du domaine (fonction notée {note}/100)", generic=False,
                  fonction=note, confidence=g.get("confidence"))
        p.blocked = _bloque(p.address)
        if not p.blocked:
            p.verification = _verifie(p.address)
            depenses += 1
        if g.get("confidence") is not None:
            p.notes.append(f"Hunter donne cette adresse à {g['confidence']}% de confiance")
        pistes.append(p)
    # Les autres personnes utiles sont RENDUES sans être vérifiées : elle peut en choisir une
    # autre dans l'interface, et on vérifiera à ce moment-là plutôt que maintenant.
    for note, g in classes[1:4]:
        if note <= 0:
            break
        p = Piste(address=g["address"], name=g["name"], title=g["position"],
                  source=f"annuaire du domaine (fonction notée {note}/100)",
                  verification="non vérifiée", generic=False, fonction=note,
                  confidence=g.get("confidence"),
                  notes=["non vérifiée — elle sera contrôlée à l'envoi"])
        p.blocked = _bloque(p.address)
        pistes.append(p)

    # 2 — LE NAVIGATEUR, seulement si l'annuaire n'a rien donné d'envoyable. Il lit la page de
    #     l'offre et les pages équipe ; c'est lent, et Hunter répond mieux la plupart du temps.
    if dom and use_browser and not any(x.sendable for x in pistes) and depenses < budget:
        try:
            import contact_finder
            trouve = contact_finder.find_contact(company, domain=dom, job_url=job_url or None)
        except Exception as exc:
            trouve = None
            out["notes"].append(f"lecture de la page de l'offre impossible : {exc}")
        if trouve is not None and getattr(trouve, "email", None) \
                and not any(x.address == trouve.email for x in pistes):
            p = Piste(address=trouve.email, name=trouve.full_name, title=trouve.title or "",
                      source=trouve.source or "page de l'offre / équipe", generic=False)
            p.blocked = _bloque(p.address)
            if not p.blocked:
                p.verification = _verifie(p.address)
                depenses += 1
            pistes.append(p)

    # 3 — LES ADRESSES QUE LE SITE PUBLIE (page carrières, contact, mentions légales). Gratuit,
    #     un peu lent : seulement quand rien d'envoyable n'a été trouvé, et AVANT de deviner.
    if dom and not any(x.sendable for x in pistes):
        for adresse in _emails_du_site(dom, company):
            if any(x.address == adresse for x in pistes):
                continue
            local = adresse.split("@")[0]
            p = Piste(address=adresse, source="publiée sur le site de l'entreprise",
                      verification="publiée sur son site", generic=local in GENERIQUES or
                      not re.search(r"[a-z]{2,}[._-][a-z]{2,}", local))
            p.blocked = _bloque(adresse)
            pistes.append(p)

    # 4 — LES BOÎTES GÉNÉRIQUES, en dernier recours. Écrire à `contact@` vaut moins qu'écrire à
    #     quelqu'un, et chaque essai coûte une vérification.
    # ⚠ CELLES QUE HUNTER CONNAÎT PASSENT AVANT CELLES QU'ON DEVINE (2026-10-16). L'annuaire
    #   rendait parfois un vrai `recrutement@` et le code le JETAIT (« type generic ») pour
    #   ensuite DEVINER `recrutement@`, `rh@`… — une vérification par essai, souvent pour rien.
    connues = [g["address"].split("@")[0].lower() for g in gens
               if (g.get("type") or "") == "generic" and g.get("address", "").endswith("@" + dom)]
    connues.sort(key=lambda l: (l not in GENERIQUES, GENERIQUES.index(l) if l in GENERIQUES else 99))
    for local in ([*connues, *[l for l in GENERIQUES if l not in connues]] if dom else []):
        if depenses >= budget or any(x.sendable for x in pistes):
            break
        adresse = f"{local}@{dom}"
        if any(x.address == adresse for x in pistes):
            continue
        p = Piste(address=adresse, generic=True,
                  source=("boîte générique connue de Hunter" if local in connues
                          else "boîte générique du domaine (devinée)"))
        p.blocked = _bloque(adresse)
        if p.blocked:
            pistes.append(p)
            continue
        p.verification = _verifie(adresse)
        depenses += 1
        pistes.append(p)

    # Marquer ce qui est DÉJÀ parti : à cette adresse, et pour cette offre.
    deja = deja_prevenus(company, role)
    out["already"] = deja
    for x in pistes:
        try:
            import tracker
            if tracker.address_has_delivered_mail(x.address):
                x.notes.append("⚠ cette adresse a déjà reçu un email de ta part")
        except Exception:
            pass

    # La personne qui RECRUTE d'abord, puis l'adresse que l'employeur publie, puis les autres.
    pistes.sort(key=lambda x: (not x.sendable, not (x.fonction >= 80 and x.confirmed),
                               x.verification not in PUBLIEE, x.generic, not x.confirmed))
    out["candidates"] = [vars(x) | {"sendable": x.sendable, "confirmed": x.confirmed,
                                    "suggested": x.suggested} for x in pistes]
    out["best"] = out["candidates"][0] if pistes and pistes[0].sendable else None
    out["verifications_spent"] = depenses
    if not out["best"]:
        out["notes"].append("aucune adresse avec assez de preuve : mieux vaut LinkedIn que "
                            "d'écrire à une boîte dont rien ne dit qu'elle existe.")
    return out


def main(argv=None) -> int:
    import json
    import sys
    from pathlib import Path
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print('usage: python recruiter.py "ENTREPRISE" [offers/annonce.txt] [url de l\'offre]')
        return 2
    company = argv[0]
    posting = Path(argv[1]).read_text(encoding="utf-8") if len(argv) > 1 else ""
    url = argv[2] if len(argv) > 2 else ""
    r = find_for_offer(company, posting=posting, job_url=url)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
