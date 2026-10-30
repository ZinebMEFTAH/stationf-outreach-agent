"""Outbox — what Claude prepared last night for Python to send today.

WHY THIS EXISTS. The `claude` CLI on the VM authenticates with Zineb's own Claude *subscription*
token, so every agent run spends from the same rolling 5-hour allowance she uses to work. The old
schedule ran heavy Claude jobs at 09:00, 14:00 and 19:00 Paris — squarely inside her working day —
so the agent and its owner competed for one quota and she repeatedly found herself locked out.

The fix is to separate THINKING from SENDING:

  • At night (22:00-03:00 Paris) Claude does all the reasoning at once — sync the inbox, rank the
    queue, research each company, write the emails, lint them — and queues finished messages here.
  • During the day, `dispatch.py` sends them at the scheduled times in PURE PYTHON. No Claude, no
    tokens, nothing taken from her allowance.

The last night run finishes before 03:00, so by 08:00 the rolling 5-hour window has fully elapsed
and she starts her day with the whole allowance.

WHAT THIS FILE IS NOT. It is not a second send path. Every queued item is sent through
`smtp_send.py` exactly as the skill would have sent it, so all five refusals (daily cap, duplicate
guard, bounce blocklist, verification, mailbox-evidence gate) plus the linter still run AT SEND
TIME, hours after drafting — which is the point: a lead whose state changed overnight is refused
then, not sent blindly because a draft existed.

Queued ≠ promised. Drafts are deliberately over-provisioned (see PREP_OVERSHOOT in the night-prep
skill): verification is the binding constraint and some addresses only fail at send time, so the
night queues more than the day can send and the dispatcher stops when the cap is reached. Anything
left over simply expires — a stale draft is never carried into another day, because its opening
line was written about a posting that is another day older.

One JSON file per day under outbox/, mirroring drafts/, so a day's plan can be read, audited and
diffed after the fact.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path

# OUTBOX_DIR redirige la file : le bac à sable de l'interface (webui/sandbox.sh) la pose dans son
# dossier jetable. Sans ça, un clic sur « Envoyer » pendant un TEST écrivait dans la vraie file,
# que l'interface pousse sur GitHub et que la VM envoie à 7h (constaté le 2026-10-15).
_DIR = Path(os.environ.get("OUTBOX_DIR") or Path(__file__).parent / "outbox")

VALID_STATUS = ("queued", "sent", "refused", "expired", "deferred")


def working_day(d: _dt.date) -> _dt.date:
    """`d`, or the Monday after when it falls on a weekend. Her rule of 2026-10-16: an
    application read on Saturday sits under twenty others by Monday."""
    while d.weekday() >= 5:
        d += _dt.timedelta(days=1)
    return d


def next_working_day(d: _dt.date) -> _dt.date:
    return working_day(d + _dt.timedelta(days=1))


def attachments_of(item: dict) -> list[str]:
    """`attach` was one path or None; the application email carries the CV AND the letter."""
    a = item.get("attach")
    if not a:
        return []
    return [a] if isinstance(a, str) else [x for x in a if x]


def spread(items: list[dict], first_day: _dt.date, cap: int,
           taken: dict[str, int] | None = None) -> list[tuple[_dt.date, dict]]:
    """Give each application a working day on or after `first_day`, at most `cap` per day.

    ⚠ THE BEST RECIPIENT OF EVERY OFFER GOES FIRST (2026-10-29). On 2026-10-19 she had approved
      37 emails for ten offers; they were sent in click order, so Veolia, Solocal and Qare took
      the ten slots of the day and MBDA, Zeendoc, VINCI Concessions, Elior and LiveMentor got
      NOTHING — their emails were refused by the cap and never retried. Sorted by `rank` (1 =
      the recipient she saw first for that offer), every offer is announced to its best contact
      on the first day, and the second and third contacts follow on the next working days.
    `taken` = slots already used per ISO day (items that are not part of this plan).
    """
    used = dict(taken or {})
    order = sorted(enumerate(items), key=lambda ix: (int(ix[1].get("rank") or 1),
                                                     str(ix[1].get("queued_at") or ""), ix[0]))
    out: list[tuple[_dt.date, dict]] = []
    day = working_day(first_day)
    for _, it in order:
        while used.get(day.isoformat(), 0) >= max(1, cap):
            day = next_working_day(day)
        used[day.isoformat()] = used.get(day.isoformat(), 0) + 1
        out.append((day, it))
    return out


def defer(item_id: str, to_day: _dt.date, day: _dt.date | str | None = None,
          note: str = "") -> str | None:
    """Move one queued item to another day. Returns its id there, or None if not found.

    The original stays where it was planned, marked `deferred`, so the day still tells what
    happened to it; a copy is queued on `to_day`. Used by dispatch when the daily cap refuses an
    application she approved: refusing it for good is how 21 of her emails disappeared on
    2026-10-19.
    """
    items = load(day)
    src = next((it for it in items if it.get("id") == item_id), None)
    if src is None:
        return None
    copy = {k: v for k, v in src.items() if k not in ("settled_at",)}
    dest = load(to_day)
    copy.update(id=f"{len(dest) + 1:02d}", status="queued",
                note=note or src.get("note") or "",
                deferred_from=(day if isinstance(day, str) else
                               (day or _dt.date.today()).isoformat()))
    dest.append(copy)
    save(dest, to_day)
    src["status"] = "deferred"
    src["note"] = f"reporté au {to_day.isoformat()}" + (f" — {note}" if note else "")
    src["settled_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    save(items, day)
    return copy["id"]


def _path(day: _dt.date | str | None = None) -> Path:
    d = day or _dt.date.today()
    if isinstance(d, _dt.date):
        d = d.isoformat()
    return _DIR / f"{d}.json"


def load(day: _dt.date | str | None = None) -> list[dict]:
    """Everything queued for `day`, in queue order. Missing file → empty, never an error."""
    try:
        data = json.loads(_path(day).read_text(encoding="utf-8"))
        return data.get("items", []) if isinstance(data, dict) else []
    except Exception:
        return []


def save(items: list[dict], day: _dt.date | str | None = None) -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    d = day or _dt.date.today()
    if isinstance(d, _dt.date):
        d = d.isoformat()
    _path(d).write_text(
        json.dumps({"date": d, "items": items}, ensure_ascii=False, indent=1),
        encoding="utf-8")


def queue(*, kind: str, to: str, subject: str, body_file: str, company: str = "",
          role: str = "", strategy: str = "", attach: str | list[str] | None = None,
          send_after: str = "09:00", day: _dt.date | str | None = None,
          note: str = "", **extra) -> str:
    """Add one prepared message. Returns its id.

    `send_after` is a local HH:MM — the dispatcher will not send before it. Spreading the day's
    messages across several times is deliberate: ten emails leaving in one burst at 09:00 looks
    less like a person than the same ten spread over the morning, and mailbox providers score
    that. Refuses a body file that does not exist, because a queued message whose draft is missing
    would fail every morning forever.
    """
    # `application` : son bouton (2026-10-08). Elle a cherché le destinataire, relu et modifié le
    # texte, puis demandé un départ à 7h le lendemain — donc cela passe par la file comme le
    # reste, et `dispatch` l'autorise même quand le pilote automatique est éteint.
    if kind not in ("cold", "followup", "reply", "application"):
        raise ValueError(f"kind must be cold/followup/reply/application, not {kind!r}")
    if not re.fullmatch(r"[0-2]\d:[0-5]\d", send_after or ""):
        raise ValueError(f"send_after must be HH:MM, not {send_after!r}")
    if not (Path(__file__).parent / body_file).exists() and not Path(body_file).exists():
        raise FileNotFoundError(f"body file not found: {body_file}")
    if not (to or "").strip() or not (subject or "").strip():
        raise ValueError("a queued message needs both a recipient and a subject")

    items = load(day)
    item = {
        "id": f"{len(items) + 1:02d}",
        "kind": kind, "to": to.strip(), "subject": subject.strip(),
        "body_file": body_file, "company": company, "role": role,
        "strategy": strategy, "attach": attach, "send_after": send_after,
        "status": "queued", "note": note,
        "queued_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    # rank / offer / uid for her application emails: which offer an item announces, and the
    # order of its recipients, so a day short of slots serves every offer's best contact first.
    item.update({k: v for k, v in extra.items() if v is not None})
    items.append(item)
    save(items, day)
    return item["id"]


def due(now: _dt.datetime | None = None, day: _dt.date | str | None = None) -> list[dict]:
    """Queued items whose send_after has passed, in queue order."""
    n = now or _dt.datetime.now()
    out = []
    for it in load(day):
        if it.get("status") != "queued":
            continue
        try:
            h, m = (int(x) for x in it["send_after"].split(":"))
        except Exception:
            h, m = 0, 0
        if (n.hour, n.minute) >= (h, m):
            out.append(it)
    return out


def mark(item_id: str, status: str, note: str = "", day: _dt.date | str | None = None) -> bool:
    """Record the outcome of one item. Returns True if it was found."""
    if status not in VALID_STATUS:
        raise ValueError(f"status must be one of {VALID_STATUS}")
    items = load(day)
    for it in items:
        if it["id"] == item_id:
            it["status"] = status
            if note:
                it["note"] = note
            it["settled_at"] = _dt.datetime.now().isoformat(timespec="seconds")
            save(items, day)
            return True
    return False


def summary(day: _dt.date | str | None = None) -> dict:
    items = load(day)
    out = {"total": len(items)}
    for st in VALID_STATUS:
        out[st] = sum(1 for i in items if i.get("status") == st)
    out["cold_queued"] = sum(1 for i in items if i.get("status") == "queued" and i["kind"] == "cold")
    out["followup_queued"] = sum(1 for i in items
                                 if i.get("status") == "queued" and i["kind"] == "followup")
    return out


# How long an application she approved may wait for a morning that did not happen (VM stopped,
# a push that never arrived) before it is given up. Past that, « je viens de déposer mon
# dossier » is no longer true, and she is told rather than the email going out a week late.
APPLICATION_GRACE_DAYS = 5


def carry_forward_applications(today: _dt.date | None = None) -> list[tuple[dict, str, str]]:
    """Her approved applications still queued on a PAST day move to today's working day.

    `expire_stale` was written for drafts the agent wrote at night, where a day-old draft opens
    on a staler posting and the lead is simply re-drafted. Her application emails are not
    re-drafted by anyone: expiring one is losing it. Returns [(item, from_day, to_day)].
    """
    t = today or _dt.date.today()
    target = working_day(t)
    moved: list[tuple[dict, str, str]] = []
    if not _DIR.is_dir():
        return moved
    for f in sorted(_DIR.glob("*.json")):
        try:
            d = _dt.date.fromisoformat(f.stem)
        except ValueError:
            continue
        if d >= t or (t - d).days > APPLICATION_GRACE_DAYS:
            continue
        for it in load(d):
            if it.get("status") == "queued" and it.get("kind") == "application":
                if defer(it["id"], target, day=d, note="matin manqué, rattrapé") is not None:
                    moved.append((it, d.isoformat(), target.isoformat()))
    return moved


def expire_stale(before: _dt.date | None = None) -> int:
    """Mark still-queued items from previous days as expired. Returns how many.

    A draft opens on "votre offre publiée la semaine dernière" and names a posting by age; carrying
    it forward would send yesterday's letter about a job that is another day older, with a cap that
    was already spent. Expiring is the honest outcome — the lead returns to the queue and is
    re-drafted fresh tonight.
    """
    cutoff = before or _dt.date.today()
    n = 0
    if not _DIR.is_dir():
        return 0
    for f in sorted(_DIR.glob("*.json")):
        try:
            d = _dt.date.fromisoformat(f.stem)
        except ValueError:
            continue
        if d >= cutoff:
            continue
        items = load(d)
        changed = False
        for it in items:
            if it.get("status") == "queued":
                it["status"] = "expired"
                it["note"] = "not sent on its day — re-drafted fresh rather than carried forward"
                changed = True
                n += 1
        if changed:
            save(items, d)
    return n


# ── MERGING TWO EDITED VERSIONS OF A DAY FILE (2026-10-30) ──────────────────────────────────────
# The VM and the Mac both write these files: the VM marks what it sent and defers what the cap
# refused into TOMORROW's file; the Mac appends her newly approved emails to the same file. A
# line merge of two lists appended at the same place is a conflict, and the VM's `-X ours` then
# keeps only its own lines: her emails vanish without a trace. Merged email by email instead.
_RANG_STATUT = {"queued": 0, "deferred": 1, "expired": 1, "refused": 2, "sent": 3}


def _cle(it: dict) -> str:
    return it.get("uid") or f"{it.get('to')}|{it.get('subject')}|{it.get('body_file')}"


def merge_items(base: list[dict], ours: list[dict], theirs: list[dict]) -> list[dict]:
    """Three-way merge of one day's emails. An email both sides kept is merged on its status (a
    settled one — sent, refused — beats one still queued); one side's additions are kept; one
    side's deletion stands unless the other side has since SETTLED that email."""
    b = {_cle(x): x for x in base}
    o = {_cle(x): x for x in ours}
    t = {_cle(x): x for x in theirs}
    out: list[dict] = []
    for k in list(o) + [k for k in t if k not in o]:
        mine, autre, avant = o.get(k), t.get(k), b.get(k)
        if mine is not None and autre is not None:
            ro = _RANG_STATUT.get(mine.get("status"), 0)
            rt = _RANG_STATUT.get(autre.get("status"), 0)
            out.append(dict(autre if rt > ro else mine))
        elif mine is not None:
            # absent from theirs: added by us, or deleted by them (kept if we settled it since)
            if avant is None or mine != avant:
                out.append(dict(mine))
        elif autre is not None:
            if avant is None or autre != avant:
                out.append(dict(autre))
    for i, it in enumerate(out, 1):
        it["id"] = f"{i:02d}"
    return out


def merge_files(base: str, ours: str, theirs: str) -> int:
    """`git config merge.outbox.driver "python outbox.py merge %O %A %B"` — writes the result
    into `ours` (%A). Exit 0 = merged cleanly; anything unreadable falls back to a conflict."""
    def lire(p: str) -> dict:
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8") or "{}")
            return d if isinstance(d, dict) else {}
        except Exception:
            return {"_illisible": True}
    db, do, dt = lire(base), lire(ours), lire(theirs)
    if do.get("_illisible") or dt.get("_illisible"):
        return 1
    items = merge_items(db.get("items") or [], do.get("items") or [], dt.get("items") or [])
    jour = do.get("date") or dt.get("date") or db.get("date")
    Path(ours).write_text(json.dumps({"date": jour, "items": items}, ensure_ascii=False,
                                     indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 5 and sys.argv[1] == "merge":
        raise SystemExit(merge_files(sys.argv[2], sys.argv[3], sys.argv[4]))
    day = sys.argv[1] if len(sys.argv) > 1 else None
    s = summary(day)
    print(f"outbox {day or _dt.date.today().isoformat()}: " +
          "  ".join(f"{k}={v}" for k, v in s.items()))
    for it in load(day):
        flag = {"queued": "·", "sent": "✓", "refused": "✗", "expired": "⋯"}.get(it["status"], "?")
        print(f"  {flag} {it['id']} {it['send_after']} {it['kind']:<9} "
              f"{(it['company'] or '')[:24]:<24} {it['subject'][:44]}")
        if it.get("note"):
            print(f"       ↳ {it['note'][:100]}")
