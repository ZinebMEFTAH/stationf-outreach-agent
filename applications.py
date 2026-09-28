"""Record an application the moment she says she has sent it — reliably.

WHY THIS IS CODE AND NOT A NOTE. `applications_log.md` is the single source of truth for what she
has already applied to: `brief.py` reads it to keep a posting out of the queue, and since
2026-09-20 `opportunities.py` reads it too, so the daily digest cannot re-offer a job she has
already sent a pack for. That happened — the first run of the rebuilt pipeline put GE
HealthCare's "Alternant·e DevOps / MLOps" back at ★100 two days after she applied to it.

So the log has a SHAPE, and the shape is load-bearing:

    | N | **Employer** | Role, city — terms | Channel | Pack |

`brief._applied()` parses exactly that: a numeric first cell, then the employer in bold. A row
typed slightly differently is not an error anyone sees — it simply fails to parse, and the
posting quietly comes back around. `log()` writes the row and then READS IT BACK through the real
parser, so a formatting slip fails loudly here instead of silently months later.

    python applications.py log "Groupe SII" "Alternance Ingénieur DevOps, Vélizy (78)" \\
        --channel HelloWork --pack "CV_..._FR_custom.pdf + groupe_sii_devops_LM.pdf"
    python applications.py list
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

# ── LE BAC À SABLE NE PROTÉGEAIT PAS CES DEUX FICHIERS (2026-09-28) ─────────────────────────
# Sa consigne permanente : rien de ce qui est lancé depuis le terminal ne doit toucher ses
# données. `webui/sandbox.sh` redirige le store des offres et le dossier des packs — mais ces
# deux chemins-ci étaient écrits EN DUR, donc marquer une candidature « envoyée » ou enregistrer
# une relance depuis le bac à sable écrivait dans ses vrais dossiers de candidature. Le défaut
# était latent ; la relance enregistrée (2026-09-28) en faisait un chemin d'écriture de plus.
# Sans variable d'environnement, le comportement est EXACTEMENT celui d'avant : la VM et son
# poste ne définissent rien, donc rien ne change pour eux.
LOG = Path(os.environ.get("APPLICATIONS_LOG") or (Path(__file__).parent / "applications_log.md"))


def _rows(text: str) -> list[tuple[str, str]]:
    """(employer, role) for every application row — the same parse brief.py performs."""
    return re.findall(r"^\|\s*\d+\s*\|\s*\*\*(.+?)\*\*\s*\|\s*([^|]*)\|", text, re.M)


def log(company: str, role: str, channel: str = "—", pack: str = "—",
        when: str | None = None) -> dict:
    """Append one application and PROVE it parses. Returns {'n', 'section', 'parsed'}.

    Raises if the row does not read back through the real parser — a silent failure here means
    the posting returns to the queue weeks later and she does the work twice.
    """
    text = LOG.read_text(encoding="utf-8") if LOG.exists() else "# Application log — Zineb Meftah\n"
    before = len(_rows(text))
    day = when or date.today().isoformat()

    # ALREADY THERE? The row is matched, not keyed, because the same posting gets written
    # twice with a slightly different tail — Green-Got as "— 24 mois (M1+M2)" and then as
    # ", **24 mois (M1+M2)**", Hymalaia with and without the salary. Two rows for one
    # application inflate the count, and applied_verdict may then recognise only one
    # wording, so the posting can come back around. Same two-factor test brief.py uses.
    try:
        import brief as _b
        want_c, want_r = _b._tokens(company), _b._tokens(role)
        for have_c, have_r in _rows(text):
            if not _b._same_employer(want_c, _b._tokens(have_c)):
                continue
            hr = _b._tokens(have_r)
            if want_r and len(want_r & hr) / max(1, len(want_r)) >= 0.6:
                return {"n": before, "section": "", "parsed": before,
                        "duplicate": f"{have_c} — {have_r.strip()}"}
    except Exception:
        pass

    header = f"## Session {day} — applications"
    table = ("| # | Employer | Role | Channel | Pack used |\n"
             "|---|---|---|---|---|\n")
    if header not in text:
        text = text.rstrip() + f"\n\n---\n\n{header}\n\n### ✅ Submitted\n\n{table}"

    # Number continues across the WHOLE file, so the numbering matches what she has really sent.
    row = (f"| {before + 1} | **{company.strip()}** | {role.strip()} | "
           f"{channel.strip()} | {pack.strip()} |\n")

    # Insert at the end of this session's table: the last consecutive table line after the header.
    head, _, tail = text.partition(header)
    lines = tail.splitlines(keepends=True)
    last = max((i for i, ln in enumerate(lines) if ln.lstrip().startswith("|")), default=len(lines) - 1)
    lines.insert(last + 1, row)
    text = head + header + "".join(lines)

    LOG.write_text(text, encoding="utf-8")

    parsed = _rows(LOG.read_text(encoding="utf-8"))
    if len(parsed) != before + 1:
        raise RuntimeError(f"row written but the parser does not see it "
                           f"({before} -> {len(parsed)}). The log shape has drifted; "
                           f"fix it before trusting the queue.")
    # ...and prove the QUEUE will actually recognise it, not merely that a row exists.
    try:
        import brief
        if brief.applied_verdict({"company": company, "role": role}, brief._applied()) != "exact":
            raise RuntimeError(f"logged, but brief.applied_verdict() still calls this posting new "
                               f"— it would be suggested again. Check the role wording.")
    except ImportError:
        pass

    # ET LA FICHE DE SUIVI, dans le même geste. Le journal et le sidecar étaient écrits par des
    # chemins différents : `log()` n'écrivait que la ligne Markdown, et la fiche n'existait que
    # pour ce que `backfill_dates()` avait semé une fois. Résultat mesuré le 2026-09-28 :
    # 35 candidatures dans le journal, 20 fiches — 15 candidatures (43 %) ABSENTES de l'onglet
    # « Mes candidatures ». Invisibles, donc jamais relancées, jamais rapprochées d'une réponse,
    # jamais comptées dans un résultat. Tout ce qu'elle a envoyé depuis l'interface était dans
    # ce trou. Une seule écriture pour les deux, sinon ils redivergent.
    try:
        d = _status_all()
        if not _find(d, company, role):
            d[_skey(company, role)] = {"company": company.strip(), "role": role.strip(),
                                       "applied": day, "state": "sent", "note": "",
                                       "changed": day}
            _STATUS.parent.mkdir(parents=True, exist_ok=True)
            _STATUS.write_text(json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True),
                               encoding="utf-8")
    except Exception as exc:          # la ligne du journal est écrite : ne jamais la perdre
        print(f"[applications] fiche de suivi non créée pour {company}: {exc}")
    return {"n": before + 1, "section": header, "parsed": len(parsed)}


# ─────────────────────────────────────────────────────────────────────────────
# WHAT HAPPENED AFTER SHE APPLIED — the loop nothing was closing.
#
# The pipeline optimises for FINDING and APPLYING. Twenty-one applications were sent before
# anything recorded what became of them: no replies tracked, no rejections, no interviews, and
# nothing to say "IBM was 5 days ago, chase it". `learning.py` does exactly this for the
# cold-email agent, and her own applications — the ones that actually decide whether she is
# hired — had none of it.
#
# ⚠ A SIDECAR, NOT A LOG REWRITE. applications_log.md is hand-maintained prose she reads, and
# its table shape is already load-bearing for the queue. Parsing outcomes back out of narrative
# would be fragile in both directions, so state lives in cache/application_status.json, keyed
# company|role the same way brief.py matches.
_STATUS = (Path(os.environ.get("WEBUI_STORE_DIR") or (Path(__file__).parent / "cache"))
           / "application_status.json")

# What can happen to an application. `sent` is the default; the rest she tells Claude.
STATES = ("sent", "replied", "interview", "rejected", "offer", "ghosted")

# Business days before a first follow-up. The outreach agent uses 4 for cold email; an
# application to a company that ASKED for candidates deserves a little longer before chasing.
FOLLOWUP_DAYS = 6

# ── LA BOUCLE DE RELANCE NE SE REFERMAIT PAS (2026-09-28) ───────────────────────────────────
# `due()` rendait tout ce qui était encore à `sent` passé FOLLOWUP_DAYS, compté depuis la date
# de CANDIDATURE. Or rien n'enregistrait qu'une relance avait été envoyée : l'interface rédige
# le message, elle le copie, elle l'envoie — et la fiche ne bouge pas. Donc la ligne redevient
# « à relancer » le lendemain, et pour toujours. Mesuré sur ses données : 19 des 20 candidatures
# affichées « à relancer » le même jour, sans aucun moyen de savoir lesquelles avaient déjà été
# relancées. Un compteur qui dit 19 tous les jours ne demande rien : c'est une liste de zéro.
#
# `followed_up` est une LISTE de dates. L'échéance se calcule depuis la DERNIÈRE touche, et les
# écarts s'allongent — même forme que `tracker.overdue_followups` côté outreach, pour que les
# deux moitiés du système se relancent de la même façon.
FOLLOWUP_GAP = (6, 8, 10)      # jours ouvrés avant la 1re, la 2e, la 3e relance
MAX_FOLLOWUPS = len(FOLLOWUP_GAP)


def note_followup(company: str, role: str, when: str | None = None) -> dict | None:
    """Enregistrer qu'une relance est PARTIE. Renvoie la fiche, ou None si elle est inconnue.

    Appelé quand elle confirme l'envoi, jamais quand le brouillon est rédigé : rédiger n'est pas
    envoyer, et une fiche marquée relancée alors qu'elle ne l'a pas été disparaîtrait de sa liste
    sans que personne n'ait écrit à personne.
    """
    d = _status_all()
    k = _find(d, company, role)
    if not k:
        return None
    e = d[k]
    jour = when or date.today().isoformat()
    touches = [t for t in (e.get("followed_up") or []) if isinstance(t, str)]
    if jour not in touches:                  # deux clics le même jour = une relance
        touches.append(jour)
    e["followed_up"] = sorted(touches)[-MAX_FOLLOWUPS:]
    e["changed"] = jour
    d[k] = e
    _STATUS.parent.mkdir(parents=True, exist_ok=True)
    _STATUS.write_text(json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True),
                       encoding="utf-8")
    return e


def followup_state(entry: dict, today: date | None = None) -> dict:
    """Où en est cette candidature dans sa séquence de relances. Pur calcul, rien n'est écrit.

    Renvoie : n (relances déjà envoyées), last_touch (date), business_days (depuis cette
    touche), due (relançable maintenant), wait (jours ouvrés restants), exhausted (séquence
    terminée — on cesse de la réclamer).
    """
    today = today or date.today()
    touches = [t for t in (entry.get("followed_up") or []) if isinstance(t, str)]
    n = len(touches)
    ref = touches[-1] if touches else entry.get("applied", "")
    try:
        depuis = date.fromisoformat(ref)
    except Exception:
        return {"n": n, "last_touch": ref, "business_days": 0, "due": False,
                "wait": 0, "exhausted": False}
    ouvres = _business_days(depuis, today)
    if n >= MAX_FOLLOWUPS:
        # ÉPUISÉE, PAS CLASSÉE. Après trois relances sans réponse, on arrête de la réclamer —
        # mais on ne décide pas à sa place que c'est mort : `ghosted` est un CONSTAT, et c'est
        # elle qui le pose. L'interface le lui propose en un clic.
        return {"n": n, "last_touch": ref, "business_days": ouvres, "due": False,
                "wait": 0, "exhausted": True}
    seuil = FOLLOWUP_GAP[n]
    return {"n": n, "last_touch": ref, "business_days": ouvres,
            "due": ouvres >= seuil, "wait": max(0, seuil - ouvres), "exhausted": False}


def _status_all() -> dict:
    try:
        return json.loads(_STATUS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _skey(company: str, role: str) -> str:
    import leadset as ls
    return f"{ls.norm_company(company)}|{ls.norm_role(role)}"


def _find(d: dict, company: str, role: str) -> str | None:
    """The existing entry this (company, role) refers to — matched, not keyed.

    ⚠ AN EXACT KEY IS NOT ENOUGH, and assuming it was created PHANTOM ROWS. The log stores the
    role as she wrote it ("Apprenti AI Engineer, Client Engineering, Bois-Colombes (92) — 24
    mois demandés") while she will say "IBM, AI Engineer, rejected". Keyed strictly, that writes
    a SECOND entry: the outcome is recorded on a row nobody reads, and the real application
    stays "sent" and keeps appearing in due() forever. Same two-factor idea brief.py uses —
    employer must match, role must overlap.
    """
    import brief
    want_c, want_r = brief._tokens(company), brief._tokens(role)
    best, best_score = None, 0.0
    for k, e in d.items():
        if not brief._same_employer(want_c, brief._tokens(e.get("company", ""))):
            continue
        have_r = brief._tokens(e.get("role", ""))
        # NORMALISÉ PAR LE PLUS PETIT DES DEUX, pas par la requête seule. Le score n'était
        # pas symétrique : « Apprenti AI Engineer » retrouvait bien la ligne longue du journal
        # (1/1), mais la ligne longue ne retrouvait PAS la fiche courte (1/6 = 0,17). En
        # remontant le seuil, le backfill a donc recréé une seconde fiche IBM à côté de celle
        # qui portait déjà son refus — IBM en double dans son onglet, et la copie « envoyée »
        # relancée alors qu'elle avait été refusée. La contenance règle les deux sens.
        score = (len(want_r & have_r) / max(1, min(len(want_r), len(have_r)))
                 if want_r and have_r else 1.0)
        if score > best_score:
            best, best_score = k, score
    # ⚠ LE SEUIL ÉTAIT EXACTEMENT LE POINT DE BASCULE, ET IL CONFONDAIT DEUX VRAIS POSTES.
    #   « Alternance Data Engineer » et « Alternance NLP Engineer » chez ChapsVision — deux
    #   candidatures distinctes, envoyées à une semaine d'écart — partagent le seul mot
    #   « engineer », soit 1 jeton sur 2 = 0,50, donc elles passaient pour la MÊME. Deux
    #   conséquences mesurées le 2026-09-28 : la seconde n'a jamais été créée (backfill la
    #   croyait déjà là), et le refus reçu pour l'une aurait été inscrit sur l'autre.
    #   Dans son domaine, deux intitulés partagent presque toujours « engineer », « data » ou
    #   « developpeur » : un seul mot commun ne peut pas valoir identité. Un intitulé court
    #   tapé à la main (« AI Engineer » pour une ligne longue) reste reconnu, puisqu'il y est
    #   contenu en entier — c'est le cas 1/1 = 1,0 que la docstring ci-dessus décrit.
    return best if best_score > 0.5 else None


def set_status(company: str, role: str, state: str, note: str = "") -> dict:
    """Record what happened. Unknown states are refused rather than silently stored."""
    if state not in STATES:
        raise ValueError(f"state must be one of {STATES}, got {state!r}")
    d = _status_all()
    k = _find(d, company, role) or _skey(company, role)
    e = d.get(k) or {"company": company, "role": role, "applied": date.today().isoformat()}
    e.update({"state": state, "note": note or e.get("note", ""),
              "changed": date.today().isoformat()})
    d[k] = e
    _STATUS.parent.mkdir(parents=True, exist_ok=True)
    _STATUS.write_text(json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True),
                       encoding="utf-8")
    return e


def _business_days(a: date, b: date) -> int:
    n, cur = 0, a
    while cur < b:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


def backfill_dates() -> int:
    """Seed the sidecar from the log's own dates, so history is not lost.

    Two date shapes exist in that file and both are read: the `### ✅ SUBMITTED YYYY-MM-DD`
    headers, and the `## Session YYYY-MM-DD` header that covers the table beneath it.
    """
    text = LOG.read_text(encoding="utf-8") if LOG.exists() else ""
    d = _status_all()
    # explicit per-application headers win
    dated = {}
    for m in re.finditer(r"^###[^\n]*?SUBMITTED\s+(\d{4}-\d{2}-\d{2})\s*[—-]\s*([^,\n]+)",
                         text, re.M | re.I):
        dated[m.group(2).strip().lower()] = m.group(1)
    # CHAQUE LIGNE PORTE LA DATE DE SA SESSION, pas celle de la première du fichier. L'ancienne
    # version prenait le premier « ## Session » du document comme repli pour TOUT : les
    # candidatures du 25 septembre auraient été datées du 18, soit une semaine trop vieilles —
    # donc « à relancer » dès leur apparition, sur des candidatures de trois jours.
    reperes = sorted(
        [(m.start(), m.group(1)) for m in
         re.finditer(r"^## Session (\d{4}-\d{2}-\d{2})", text, re.M)]
        + [(m.start(), m.group(1)) for m in
           re.finditer(r"^###[^\n]*?SUBMITTED\s+(\d{4}-\d{2}-\d{2})", text, re.M | re.I)])

    def _session_de(pos: int) -> str:
        courant = ""
        for debut, jour in reperes:
            if debut <= pos:
                courant = jour
            else:
                break
        return courant or date.today().isoformat()

    positions = [m.start() for m in
                 re.finditer(r"^\|\s*\d+\s*\|\s*\*\*(.+?)\*\*\s*\|\s*([^|]*)\|", text, re.M)]
    n = 0
    for (company, role), pos in zip(_rows(text), positions):
        k = _skey(company, role)
        if k in d or _find(d, company, role):
            continue
        fallback = _session_de(pos)
        when = next((v for name, v in dated.items() if name in company.lower()
                     or company.lower() in name), fallback)
        d[k] = {"company": company, "role": role.strip(), "applied": when,
                "state": "sent", "note": "", "changed": when}
        n += 1
    if n:
        _STATUS.parent.mkdir(parents=True, exist_ok=True)
        _STATUS.write_text(json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True),
                           encoding="utf-8")
    return n


def due(business_days: int | None = None) -> list[dict]:
    """Les candidatures à relancer MAINTENANT, la plus ancienne touche d'abord.

    Une candidature silencieuse est l'issue la plus fréquente et la moins chère à traiter : un
    mail court à une entreprise qui cherchait déjà quelqu'un. Le décompte part de la DERNIÈRE
    relance, pas de la candidature, et s'arrête après MAX_FOLLOWUPS — sans quoi la même ligne
    est réclamée tous les jours jusqu'à la fin des temps (voir FOLLOWUP_GAP ci-dessus).

    `business_days` force un seuil unique : gardé pour les appelants existants et pour pouvoir
    inspecter la liste à un autre horizon depuis la ligne de commande.
    """
    today = date.today()
    out = []
    for e in _status_all().values():
        if e.get("state") != "sent":
            continue
        st = followup_state(e, today)
        if business_days is not None:
            if st["n"] >= MAX_FOLLOWUPS or st["business_days"] < business_days:
                continue
        elif not st["due"]:
            continue
        out.append({**e, "business_days": st["business_days"], "followups": st["n"],
                    "last_touch": st["last_touch"]})
    return sorted(out, key=lambda x: x.get("last_touch") or x.get("applied", ""))


def exhausted() -> list[dict]:
    """Trois relances, aucune réponse. On cesse de les réclamer ; elle décide de les classer."""
    today = date.today()
    return [{**e, **followup_state(e, today)} for e in _status_all().values()
            if e.get("state") == "sent" and followup_state(e, today)["exhausted"]]


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "log":
        args = sys.argv[2:]
        kw = {}
        for flag in ("--channel", "--pack", "--when"):
            if flag in args:
                i = args.index(flag)
                kw[flag[2:]] = args[i + 1]
                del args[i:i + 2]
        got = log(args[0], args[1], **kw)
        print(f"✅ logged as #{got['n']} under {got['section']} "
              f"— the queue will not offer it again")
    elif len(sys.argv) >= 2 and sys.argv[1] == "due":
        backfill_dates()
        rows = due(int(sys.argv[2]) if len(sys.argv) > 2 else FOLLOWUP_DAYS)
        print(f"{len(rows)} application(s) silent long enough to chase:")
        for r in rows:
            print(f"   {r['business_days']:3}j  {r['company'][:26]:26} {r['role'][:44]}")
    elif len(sys.argv) >= 5 and sys.argv[1] == "status":
        print(set_status(sys.argv[2], sys.argv[3], sys.argv[4],
                         " ".join(sys.argv[5:]) if len(sys.argv) > 5 else ""))
    else:
        text = LOG.read_text(encoding="utf-8") if LOG.exists() else ""
        rows = _rows(text)
        print(f"{len(rows)} applications logged:")
        for i, (co, role) in enumerate(rows, 1):
            print(f"  {i:3}  {co[:28]:28} {role.strip()[:56]}")
