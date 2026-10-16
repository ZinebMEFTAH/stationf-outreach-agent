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

    @property
    def confirmed(self) -> bool:
        return self.verification in FORT

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
      passe ENSUITE la corroboration ici, un par un — le premier qui correspond à l'annonce
      gagne. La règle de fond ne change pas : un domaine qui ne correspond pas n'est jamais pris.
    """
    try:
        import company_resolver
    except Exception as exc:
        return "", f"résolution impossible ({type(exc).__name__})"
    # LES SOURCES RAPIDES D'ABORD (annonce, nom, Clearbit), la recherche web ensuite et
    # seulement si elles n'ont rien donné de corroboré : ~25 s par entreprise sinon, à chaque clic.
    refus, injoignable, essayes = [], None, set()
    for etape in ("rapide", "web"):
        try:
            cands = company_resolver.candidates_for(company, posting, web=(etape == "web"),
                                                    cheap=(etape == "rapide"))
        except Exception:
            cands = []
        cands = [(d, s) for d, s in cands if d not in essayes]
        essayes |= {d for d, _ in cands}
        if not cands:
            continue
        if not posting:
            # Sans annonce on ne peut rien corroborer. On le DIT plutôt que de faire confiance.
            dom, src = cands[0]
            return dom, f"domaine non corroboré (pas de texte d'annonce à comparer ; source : {src})"
        trouve = _premier_corrobore(company, posting, cands[:4], refus)
        if trouve[0]:
            return trouve
        injoignable = injoignable or trouve[1]
    if not essayes:
        return "", "aucun domaine trouvé pour ce nom d'entreprise (annuaire, annonce et web)"
    # Un site qui ne répond pas n'a rien démenti : on garde le premier candidat injoignable, en
    # le disant — sauf si un AUTRE candidat a été positivement démenti avant lui.
    if injoignable:
        dom, src, err = injoignable
        return dom, f"domaine non corroboré (site injoignable : {err} ; source : {src})"
    return "", (f"aucun candidat ne correspond à cette annonce : {', '.join(refus)} — "
                f"probablement une autre entreprise du même nom. On n'écrit pas.")


def _premier_corrobore(company: str, posting: str, cands: list, refus: list):
    """(domaine, raison) du premier candidat que l'annonce corrobore, sinon ("", injoignable)."""
    import company_brief
    import descriptions
    injoignable = None
    for dom, src in cands:
        try:
            page = " ".join(descriptions._page_text(descriptions._get("https://" + dom)).split())
            ok, part, communs = company_brief.corroborates(page, posting, company)
        except Exception as exc:
            injoignable = injoignable or (dom, src, type(exc).__name__)
            continue
        # ⚠ UNE PAGE VIDE NE DÉMENT RIEN : thalesgroup.com rend une coquille JavaScript, 0,0 %
        #   de vocabulaire commun faute de texte, et Thales était refusé comme « une autre
        #   entreprise du même nom ». Moins de 150 mots lisibles = non concluant, pas un refus.
        if not ok and len(page.split()) < 150:
            injoignable = injoignable or (dom, src, "page sans texte lisible")
            continue
        if ok:
            return dom, f"{dom} corroboré par l'annonce ({part:.0%} de vocabulaire commun ; {src})"
        refus.append(f"{dom} ({part:.1%})")
    return "", injoignable


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
    if not dom:
        return out

    pistes: list[Piste] = []
    depenses = 0

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
    try:
        import email_verify
        compte = email_verify.hunter_count(dom)
        if compte is not None and compte["total"] == 0:
            gens = []
            out["notes"].append(f"Hunter ne connaît personne sur {dom} — aucune recherche dépensée")
        else:
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
    except Exception as exc:
        gens = []
        out["notes"].append(f"annuaire du domaine indisponible : {type(exc).__name__}")
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
                  source=f"annuaire du domaine (fonction notée {note}/100)", generic=False)
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
        pistes.append(Piste(address=g["address"], name=g["name"], title=g["position"],
                            source=f"annuaire du domaine (fonction notée {note}/100)",
                            verification="non vérifiée", generic=False,
                            notes=["non vérifiée — elle sera contrôlée si tu la choisis"]))

    # 2 — LE NAVIGATEUR, seulement si l'annuaire n'a rien donné d'envoyable. Il lit la page de
    #     l'offre et les pages équipe ; c'est lent, et Hunter répond mieux la plupart du temps.
    if use_browser and not any(x.sendable for x in pistes) and depenses < budget:
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

    # 3 — LES BOÎTES GÉNÉRIQUES, en dernier recours. Écrire à `contact@` vaut moins qu'écrire à
    #     quelqu'un, et chaque essai coûte une vérification.
    # ⚠ CELLES QUE HUNTER CONNAÎT PASSENT AVANT CELLES QU'ON DEVINE (2026-10-16). L'annuaire
    #   rendait parfois un vrai `recrutement@` et le code le JETAIT (« type generic ») pour
    #   ensuite DEVINER `recrutement@`, `rh@`… — une vérification par essai, souvent pour rien.
    connues = [g["address"].split("@")[0].lower() for g in gens
               if (g.get("type") or "") == "generic" and g.get("address", "").endswith("@" + dom)]
    connues.sort(key=lambda l: (l not in GENERIQUES, GENERIQUES.index(l) if l in GENERIQUES else 99))
    for local in [*connues, *[l for l in GENERIQUES if l not in connues]]:
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

    pistes.sort(key=lambda x: (not x.sendable, x.generic, not x.confirmed))
    out["candidates"] = [vars(x) | {"sendable": x.sendable, "confirmed": x.confirmed}
                         for x in pistes]
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
