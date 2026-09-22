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
import re
import sys
from datetime import date, timedelta
from pathlib import Path

LOG = Path(__file__).parent / "applications_log.md"


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
_STATUS = Path(__file__).parent / "cache" / "application_status.json"

# What can happen to an application. `sent` is the default; the rest she tells Claude.
STATES = ("sent", "replied", "interview", "rejected", "offer", "ghosted")

# Business days before a first follow-up. The outreach agent uses 4 for cold email; an
# application to a company that ASKED for candidates deserves a little longer before chasing.
FOLLOWUP_DAYS = 6


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
        score = len(want_r & have_r) / max(1, len(want_r)) if want_r else 1.0
        if score > best_score:
            best, best_score = k, score
    return best if best_score >= 0.5 else None


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
    session = re.search(r"^## Session (\d{4}-\d{2}-\d{2})", text, re.M)
    fallback = session.group(1) if session else date.today().isoformat()
    n = 0
    for company, role in _rows(text):
        k = _skey(company, role)
        if k in d:
            continue
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


def due(business_days: int = FOLLOWUP_DAYS) -> list[dict]:
    """Applications still at `sent` that are old enough to chase, oldest first.

    A silent application is the commonest outcome and the cheapest to act on — one short mail to
    a company that already wanted candidates. Nothing in this repo was surfacing them.
    """
    today = date.today()
    out = []
    for e in _status_all().values():
        if e.get("state") != "sent":
            continue
        try:
            applied = date.fromisoformat(e.get("applied", ""))
        except Exception:
            continue
        age = _business_days(applied, today)
        if age >= business_days:
            out.append({**e, "business_days": age})
    return sorted(out, key=lambda x: x["applied"])


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
