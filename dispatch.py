#!/usr/bin/env python3
"""Send what last night's Claude run prepared. PURE PYTHON — spends no Claude quota.

This is the daytime half of the night-prep / day-send split (see outbox.py for why). It runs from
cron at the sending times, reads today's outbox, and hands each due message to `smtp_send.py`
unchanged. It makes no decisions about content and never calls an LLM, so Zineb's Claude allowance
is untouched while she works.

WHAT IT DOES NOT DO — deliberately:
  • It does not re-check whether a message is a good idea. `smtp_send.py` owns that, and still runs
    every gate AT SEND TIME: daily cap, duplicate guard, bounce blocklist, verification, the
    mailbox-evidence gate, and email_lint. Those fire hours after drafting, which is exactly what
    makes queueing safe — a lead that bounced, replied or got blocklisted overnight is refused now.
  • It NEVER retries. `smtp_send` exits 0 with "⚠ DELIVERED but …" on stderr when the mail went out
    and only the bookkeeping failed; treating that as a failure would double-send. Exit 0 is sent,
    full stop.
  • It does not stop on a refusal. A refused cold email is expected (an address goes bad, the cap
    is reached); the rest of the day's queue must still go out, so each item is independent.

    python dispatch.py              # dry run — show what would go now
    python dispatch.py --send
    python dispatch.py --send --all # ignore send_after (catch-up after an outage)
"""
from __future__ import annotations

import argparse

import config
import datetime as _dt
import subprocess
import sys
from pathlib import Path

import outbox

_ROOT = Path(__file__).parent


def _send_one(item: dict, really: bool) -> tuple[bool, str]:
    """Invoke smtp_send for one queued item. Returns (sent, detail)."""
    body = _ROOT / item["body_file"]
    if not body.exists():
        return False, f"draft file missing: {item['body_file']}"

    cmd = [sys.executable, str(_ROOT / "smtp_send.py"),
           "--to", item["to"], "--subject", item["subject"],
           "--body-file", str(body), "--kind", item["kind"]]
    for flag, key in (("--company", "company"), ("--role", "role"), ("--strategy", "strategy")):
        if item.get(key):
            cmd += [flag, str(item[key])]
    # Her application email may carry the CV AND the letter: one --attach per file.
    for piece in outbox.attachments_of(item):
        cmd += ["--attach", str(_ROOT / piece)]
    if really:
        cmd.append("--send")

    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_ROOT))
    tail = (r.stderr or r.stdout or "").strip().splitlines()
    detail = tail[-1][:300] if tail else ""
    # Exit 0 == delivered, even with a "DELIVERED but bookkeeping failed" warning on stderr.
    # Anything else is a refusal by one of the five gates or a transport failure.
    return r.returncode == 0, detail


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Send tonight's prepared outbox (no Claude)")
    ap.add_argument("--send", action="store_true", help="actually transmit (default: dry run)")
    ap.add_argument("--all", action="store_true",
                    help="ignore send_after — catch up everything still queued today")
    ap.add_argument("--day", help="YYYY-MM-DD (default: today)")
    a = ap.parse_args(argv)

    # ⚠ HER APPLICATIONS FROM A MORNING THAT DID NOT HAPPEN ARE CARRIED, NOT EXPIRED (2026-10-29).
    #   Done BEFORE the autopilot gate below, which looks at today's queue only: an email she
    #   approved for yesterday would otherwise be invisible to it, then expired as a stale draft.
    carried = []
    if a.send and not a.day:
        carried = outbox.carry_forward_applications()
        for it, de, vers in carried:
            print(f"[dispatch] candidature du {de} rattrapée → {vers} : {it['company']} → {it['to']}")
        _rattrape_le_suivi()

    # ── LE PILOTE AUTOMATIQUE EST ÉTEINT (2026-10-08, sa décision) ──────────────────────────
    # On s'arrête ICI plutôt que de laisser `smtp_send` refuser chaque message un par un : un
    # run qui parcourt dix brouillons pour récolter dix refus remplit le journal de bruit et
    # ressemble à une panne. Le refus doit se lire en une ligne, et dire comment rallumer.
    # ⚠ LES CANDIDATURES QU'ELLE A APPROUVÉES PASSENT (2026-10-08). Le pilote éteint interdit le
    #   démarchage, pas ses propres envois : un `application` dans la file est un email qu'elle a
    #   fait chercher, relu, modifié et validé par un clic. Elle a demandé qu'il partent à 7h le
    #   lendemain, donc quelque chose doit bien les transmettre. On ne s'arrête que s'il n'y a
    #   QUE du démarchage en attente.
    en_file = [i for i in (outbox.load(a.day) or []) if i.get("status") == "queued"]
    candidatures = [i for i in en_file if i.get("kind") == "application"]
    if a.send and not config.AUTOPILOT and not candidatures:
        print("[dispatch] pilote automatique ÉTEINT — rien n'est envoyé.\n"
              "           Sa décision du 2026-10-08 : plus un email vers une entreprise sans son\n"
              "           clic. Les candidatures partent par le bouton de l'interface, qui lui\n"
              "           montre le destinataire et le texte avant l'envoi.\n"
              "           Pour rallumer le démarchage : OUTREACH_AUTOPILOT=1 dans l'environnement.")
        _report_to_her([], carried)
        return 0
    if a.send and not config.AUTOPILOT:
        print(f"[dispatch] pilote éteint, mais {len(candidatures)} candidature(s) approuvée(s) "
              f"par elle attendent : seules celles-là partent.")

    # Yesterday's unsent drafts are never carried forward: they open on a posting that is another
    # day older, against a cap that already reset.
    expired = outbox.expire_stale()
    if expired:
        print(f"[dispatch] expired {expired} unsent draft(s) from previous days")

    items = (outbox.load(a.day) if a.all else outbox.due(day=a.day))
    if a.all:
        items = [i for i in items if i.get("status") == "queued"]
    # ⚠ HER EMAILS LEAVE AT 7:00 PARIS, IN EVERY SEASON (2026-10-30). The cron fires at 05:00 and
    #   06:00 UTC so that one pass is 7:00 Paris both in summer (UTC+2) and winter (UTC+1) — but
    #   her items carry send_after "00:00" (the VM's own zone is unknown), so since the clocks
    #   changed on 2026-10-25 the 05:00 UTC pass sent them at SIX. The hour is read in Paris here.
    if not a.all and a.send and _heure_paris() < _HEURE_CANDIDATURES:
        retenus = [i for i in items if i.get("kind") == "application"]
        if retenus:
            print(f"[dispatch] {len(retenus)} candidature(s) retenue(s) jusqu'à "
                  f"{_HEURE_CANDIDATURES}h Paris (il est {_heure_paris()}h)")
        items = [i for i in items if i.get("kind") != "application"]
    if not items:
        s = outbox.summary(a.day)
        print(f"[dispatch] nothing due — {s['queued']} still queued, "
              f"{s['sent']} sent, {s['refused']} refused today")
        if a.send:
            _report_to_her([], carried)
        return 0

    # The best recipient of every offer first: if the day runs short of slots, each offer she
    # approved has still been announced to someone (see outbox.spread).
    items = sorted(items, key=lambda i: (i.get("kind") != "application",
                                         int(i.get("rank") or 1), i.get("id") or ""))
    sent = refused = 0
    rapport: list[tuple[str, dict, str]] = []
    for it in items:
        label = f"{it['id']} {it['kind']:<9} {(it['company'] or '?')[:26]}"
        if not a.send:
            print(f"[dry-run] would send  {label}  → {it['to']}")
            continue
        ok, detail = _send_one(it, really=True)
        if ok:
            outbox.mark(it["id"], "sent", day=a.day)
            sent += 1
            print(f"✓ sent    {label}")
            if detail.startswith("⚠"):
                # Delivered; only the bookkeeping failed. Recorded, never retried.
                print(f"          ↳ {detail}")
            rapport.append(("sent", it, ""))
        elif it.get("kind") == "application" and _CAP_REFUSAL in detail:
            # ⚠ A FULL DAY POSTPONES HER EMAIL, IT DOES NOT DELETE IT. On 2026-10-19 this branch
            #   did not exist: 21 emails she had approved were marked `refused` and never retried.
            jour = outbox.next_working_day(_dt.date.fromisoformat(a.day) if a.day
                                           else _dt.date.today())
            outbox.defer(it["id"], jour, day=a.day, note="plafond du jour atteint")
            print(f"⏭ reporté {label} → {jour.isoformat()} (plafond du jour)")
            rapport.append(("deferred", it, jour.isoformat()))
        else:
            outbox.mark(it["id"], "refused", note=detail, day=a.day)
            refused += 1
            print(f"✗ refused {label}\n          ↳ {detail}")
            rapport.append(("refused", it, detail))

    if a.send:
        s = outbox.summary(a.day)
        print(f"[dispatch] {sent} sent, {refused} refused this pass; "
              f"{s['queued']} still queued for later today")
        _report_to_her(rapport, carried)
    return 0


_CAP_REFUSAL = "daily send cap"
_HEURE_CANDIDATURES = 7


def _rattrape_le_suivi(jours: int = 45) -> int:
    """Every application email SENT in the last weeks gets its row in the tracker (2026-10-30).

    A reply is matched to a row by the sender's address; her application emails had none (see
    tracker.ensure_logged_application), so an answer from Veolia, Solocal or Qare to the emails
    of 2026-10-19 would have reached nobody. Runs on the VM, where contacts.xlsx lives — a row
    added on the Mac is discarded at the next pull. Idempotent, never blocks the dispatch.
    """
    try:
        import tracker
    except Exception:
        return 0
    limite = _dt.date.today() - _dt.timedelta(days=jours)
    ajoutes = 0
    for f in sorted(outbox._DIR.glob("*.json")) if outbox._DIR.is_dir() else []:
        try:
            jour = _dt.date.fromisoformat(f.stem)
        except ValueError:
            continue
        if jour < limite:
            continue
        for it in outbox.load(jour):
            if it.get("kind") != "application" or it.get("status") != "sent":
                continue
            try:
                if tracker.ensure_logged_application(it.get("company") or "", it.get("role") or "",
                                                     it.get("to") or "", it.get("subject") or "",
                                                     jour):
                    ajoutes += 1
                    print(f"[dispatch] suivi rattrapé : {it.get('company')} → {it.get('to')}")
            except Exception as exc:
                print(f"[dispatch] suivi non rattrapé pour {it.get('to')} : {type(exc).__name__}")
    return ajoutes


def _heure_paris() -> int:
    """The hour in Paris, whatever zone the VM runs in. Unknown zone data → let it through."""
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("Europe/Paris")).hour
    except Exception:
        return 23


def _report_to_her(rapport: list[tuple[str, dict, str]],
                   carried: list[tuple[dict, str, str]]) -> None:
    """One email to HER after a pass that touched her applications: what left, what did not, why.

    ⚠ THE SILENCE WAS THE BUG (2026-10-29). Thirty-seven emails approved on 2026-10-16: ten
      left, twenty-seven were refused at 7am — the cap, and a linter misreading « dès avril » —
      and nothing told her. The interface said « en file pour lundi 7h », which was true, and she
      believed MBDA, Zeendoc and VINCI Concessions had heard from her. Internal alert, so it is
      never counted, logged or footered, and it only goes out when there is something to say.
    """
    lignes = [(etat, it, info) for etat, it, info in rapport if it.get("kind") == "application"]
    if not lignes and not carried:
        return
    to = getattr(config, "INTERNAL_ALERT_EMAIL", None) or getattr(config, "EMAIL_ADDRESS", None)
    if not to:
        return
    parts = []
    envoyes = [it for e, it, _ in lignes if e == "sent"]
    if envoyes:
        parts.append(f"✓ {len(envoyes)} email(s) envoyé(s) :")
        parts += [f"   · {it['company']} — {it['to']}" for it in envoyes]
    refuses = [(it, info) for e, it, info in lignes if e == "refused"]
    if refuses:
        parts.append(f"\n✗ {len(refuses)} refusé(s) — à reprendre depuis l'interface :")
        parts += [f"   · {it['company']} — {it['to']}\n     raison : {info.strip()[:240]}"
                  for it, info in refuses]
    reportes = [(it, info) for e, it, info in lignes if e == "deferred"]
    if reportes:
        parts.append(f"\n⏭ {len(reportes)} reporté(s) au jour ouvré suivant (plafond de "
                     f"{config.APPLICATION_CAP} par jour) :")
        parts += [f"   · {it['company']} — {it['to']} → {jour}" for it, jour in reportes]
    if carried:
        parts.append(f"\n↻ {len(carried)} rattrapé(s) d'un matin manqué :")
        parts += [f"   · {it['company']} — {it['to']} (prévu le {de})" for it, de, _ in carried]
    sujet = (f"Candidatures du matin : {len(envoyes)} envoyée(s)"
             + (f", {len(refuses)} refusée(s)" if refuses else "")
             + (f", {len(reportes)} reportée(s)" if reportes else ""))
    corps = "\n".join(parts) + ("\n\nDétail et reprise : onglet de l'offre, « Prévenir "
                                "l'employeur » → historique des envois.")
    try:
        cmd = [sys.executable, str(_ROOT / "smtp_send.py"), "--to", to, "--subject", sujet,
               "--body", corps, "--kind", "alert", "--send"]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_ROOT), timeout=120)
        print(f"[dispatch] rapport envoyé à {to}" if r.returncode == 0 else
              f"[dispatch] rapport NON envoyé : {(r.stderr or r.stdout or '').strip()[-200:]}")
    except Exception as exc:
        print(f"[dispatch] rapport impossible : {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
