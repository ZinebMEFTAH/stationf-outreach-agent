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

import re
import sys
from datetime import date
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
    else:
        text = LOG.read_text(encoding="utf-8") if LOG.exists() else ""
        rows = _rows(text)
        print(f"{len(rows)} applications logged:")
        for i, (co, role) in enumerate(rows, 1):
            print(f"  {i:3}  {co[:28]:28} {role.strip()[:56]}")
