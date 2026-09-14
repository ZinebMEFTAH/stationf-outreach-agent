"""LinkedIn outreach budget — the second channel has hard ceilings, and they are NOT the email ones.

LinkedIn is the agent's spam-immune second touch on the same decision-makers it emails (see
/linkedin-draft and the /daily-agent double-tap). Nothing is ever sent automatically — LinkedIn
bans automation — so the agent DRAFTS and Zineb sends by hand. That made it feel free, and it was
treated as free: 13 notes drafted in July, **93 in August**, 31 by mid-September, against no cap
of any kind. Email has five refusals in code; this channel had none.

It is not free. Zineb is on **Premium Career billed annually** (14,87 €/mo), and that plan has two
different ceilings that are ~20x apart — which is exactly why they must not be conflated:

  • CONNECTION-REQUEST NOTES (the <=300-char box on an invite). The famous "5 personalised notes a
    month" cap is the FREE-account restriction; Premium has no monthly cap on these. What binds is
    the INVITATION limit: ~100 invites per ROLLING 7 days, and Premium does NOT raise it — LinkedIn
    applies the same ceiling to Basic and Premium. So this budget is WEEKLY, not monthly.

  • INMAIL (message someone you are not connected to, no invite needed, has a subject line). This is
    a hard MONTHLY allowance: Premium Career = 5 credits, accruing to at most 3x the cap (15).
    93 drafts a month against 5 credits is not a throttling problem, it is a RANKING problem —
    when you get five, the choice of WHICH five people is the entire value of the channel.

Two buckets, two windows, counted separately. A rolling 7-day window is used for invites because
that is how LinkedIn itself measures (from your first invite of the window, not a fixed Monday).

Architecture: raw I/O only, per CLAUDE.md — this module COUNTS and reports what is left. The skills
(Claude) decide who deserves a scarce InMail credit. Source of truth is the `Agent (LinkedIn):`
lines already written into the Conversation Log by tracker.note_linkedin_draft, so there is no new
state file to drift and no change to the frozen 6-column schema.

Drafts are counted, not sends: a draft is an INTENT to spend, and the agent can only control what
it produces. That errs toward under-spending a scarce credit, which is the safe direction.

    python linkedin_budget.py            # what is left today, and why
"""
from __future__ import annotations

import datetime as _dt
import re

import config

# A LinkedIn log line, as written by tracker.note_linkedin_draft. The method suffix is OPTIONAL:
# lines written before methods were tracked carry no method and count as invites (the default,
# and the conservative reading — it never silently spends an InMail credit retroactively).
_LINE = re.compile(
    r"\[(\d{4}-\d{2}-\d{2})\]\s*Agent \(LinkedIn\):\s*(?P<rest>[^\n\[]*)",
    re.IGNORECASE,
)


def _method_of(rest: str) -> str:
    """'inmail' or 'invite' — from the log line's text. Unlabelled lines are invites."""
    return "inmail" if "inmail" in (rest or "").lower() else "invite"


def touches(df=None) -> list[tuple[_dt.date, str]]:
    """Every LinkedIn draft on record as (date, method), oldest first.

    Unparseable dates are skipped rather than guessed — a bad line must not be able to consume
    someone's budget, and the whole point of the module is to not over-report spend.
    """
    import tracker
    if df is None:
        df = tracker.load()
    out: list[tuple[_dt.date, str]] = []
    for log in df["Conversation Log"].fillna("").astype(str):
        if "(linkedin)" not in log.lower():
            continue
        for m in _LINE.finditer(log):
            try:
                d = _dt.date.fromisoformat(m.group(1))
            except ValueError:
                continue
            out.append((d, _method_of(m.group("rest"))))
    out.sort(key=lambda t: t[0])
    return out


def counts(today: _dt.date | None = None, df=None) -> dict:
    """Spend in each bucket's own window: invites over a rolling 7 days, InMail over the month."""
    d = today or _dt.date.today()
    window_start = d - _dt.timedelta(days=6)          # rolling 7 days, today included
    rows = touches(df)
    invites_week = sum(1 for t, m in rows if m == "invite" and window_start <= t <= d)
    inmail_month = sum(1 for t, m in rows if m == "inmail" and t.year == d.year and t.month == d.month)
    return {
        "date": d.isoformat(),
        "invite_window_start": window_start.isoformat(),
        "invites_last_7d": invites_week,
        "inmails_this_month": inmail_month,
        "drafts_total": len(rows),
    }


def allowance(today: _dt.date | None = None, df=None) -> dict:
    """How many of each the agent may still draft. Never negative.

    `invite_per_day` is the weekly headroom spread over the working days left in the window, so
    the channel keeps a rhythm instead of burning the week's allowance on Monday — the same
    reasoning as config.verification_paced_cap() for Hunter, and the same reason: a mailbox (or
    profile) that bursts and then goes silent reads as automation, which is what gets accounts
    restricted.
    """
    d = today or _dt.date.today()
    c = counts(d, df)
    invites_left = max(0, config.LINKEDIN_WEEKLY_INVITE_CAP - c["invites_last_7d"])
    inmail_left = max(0, config.LINKEDIN_INMAIL_CREDITS - c["inmails_this_month"])
    # Working days remaining in the rolling window, today included (min 1 so we never divide by 0).
    days = max(1, sum(1 for i in range(7) if (d + _dt.timedelta(days=i)).weekday() < 5))
    return {
        **c,
        "invite_cap_week": config.LINKEDIN_WEEKLY_INVITE_CAP,
        "invites_left_week": invites_left,
        "invite_per_day": max(0, invites_left // days),
        "inmail_cap_month": config.LINKEDIN_INMAIL_CREDITS,
        "inmails_left_month": inmail_left,
    }


def may_draft(method: str = "invite", today: _dt.date | None = None, df=None) -> tuple[bool, str]:
    """(allowed, reason) for one more draft of `method`. The reason is meant to be shown."""
    a = allowance(today, df)
    if _method_of(method) == "inmail":
        if a["inmails_left_month"] <= 0:
            return False, (f"InMail credits spent for {a['date'][:7]} "
                           f"({a['inmails_this_month']}/{a['inmail_cap_month']}) — "
                           "use a connection note instead; credits renew next month")
        return True, f"{a['inmails_left_month']} of {a['inmail_cap_month']} InMail credits left this month"
    if a["invites_left_week"] <= 0:
        return False, (f"invite budget spent for the rolling week "
                       f"({a['invites_last_7d']}/{a['invite_cap_week']} since {a['invite_window_start']})")
    return True, f"{a['invites_left_week']} of {a['invite_cap_week']} invites left this rolling week"


if __name__ == "__main__":
    import json
    import sys
    a = allowance()
    if "--json" in sys.argv:
        print(json.dumps(a, indent=2))
        sys.exit(0)
    print(f"LinkedIn budget — {a['date']}  (Premium Career: unlimited notes, 5 InMail/mo, ~100 invites/week)")
    print(f"  invites   {a['invites_last_7d']:>3}/{a['invite_cap_week']} used in the rolling week "
          f"(since {a['invite_window_start']})  →  {a['invites_left_week']} left, "
          f"~{a['invite_per_day']}/working day")
    print(f"  InMail    {a['inmails_this_month']:>3}/{a['inmail_cap_month']} credits used this month"
          f"                     →  {a['inmails_left_month']} left")
    print(f"  ({a['drafts_total']} LinkedIn drafts on record in total)")
