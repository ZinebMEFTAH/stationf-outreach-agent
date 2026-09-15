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
import re
from pathlib import Path

_DIR = Path(__file__).parent / "outbox"

VALID_STATUS = ("queued", "sent", "refused", "expired")


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
          role: str = "", strategy: str = "", attach: str | None = None,
          send_after: str = "09:00", day: _dt.date | str | None = None,
          note: str = "") -> str:
    """Add one prepared message. Returns its id.

    `send_after` is a local HH:MM — the dispatcher will not send before it. Spreading the day's
    messages across several times is deliberate: ten emails leaving in one burst at 09:00 looks
    less like a person than the same ten spread over the morning, and mailbox providers score
    that. Refuses a body file that does not exist, because a queued message whose draft is missing
    would fail every morning forever.
    """
    if kind not in ("cold", "followup", "reply"):
        raise ValueError(f"kind must be cold/followup/reply, not {kind!r}")
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


if __name__ == "__main__":
    import sys
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
