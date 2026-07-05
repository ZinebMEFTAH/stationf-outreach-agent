"""dashboard.py — collect the full pipeline state and render a self-contained HTML dashboard.

`python dashboard.py`          → writes dashboard.html (live snapshot, embeds the data as JSON)
`python dashboard.py --json`   → prints the data blob only (for debugging)

The HTML is fully self-contained (inline CSS/JS, data baked in, no external requests) so it can be
published as a Claude Artifact or opened directly. Re-run to refresh. The design lives in
dashboard_template.html with a `/*__DATA__*/` placeholder this script replaces.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import config
import tracker
import learning
import warm_network
import school_partners
from smtp_send import today_send_counts

_DIR = Path(__file__).resolve().parent


def collect() -> dict:
    df = tracker.load()
    f = tracker.funnel()
    e = tracker.enrichment_stats()
    counts = today_send_counts()
    cold_done, warm_done = counts.get("cold", 0), counts.get("warm", 0)
    cold_cap = config.effective_cold_cap()

    overdue = tracker.overdue_followups()
    stalled = tracker.stalled_conversations(days=5)
    replied = df[df["Status"].astype(str).str.strip() == "Replied"]

    rec = tracker.recommend_strategy_order()
    ls = learning.reply_stats()

    pending = df[df["Status"].astype(str).str.strip() == "Pending"]
    pool_partners = sorted({str(r["Company"]) for _, r in pending.iterrows()
                            if school_partners.is_partner(str(r["Company"]))})

    leads = tracker.rank_pending_leads(limit=10)

    cutoff = str(date.today() - timedelta(days=7))
    recent = (df[df["Last Interaction Date"].fillna("").astype(str) >= cutoff]
              .sort_values("Last Interaction Date", ascending=False))

    def _lead_flags(l):
        fl = []
        if "★★ WARM" in l.get("reasons", ""): fl.append("warm")
        if l.get("school_partner"): fl.append("school")
        if "★ alternance posting" in l.get("reasons", ""): fl.append("alternance")
        if l.get("likely_big_corp"): fl.append("bigcorp")
        return fl

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "weeks_to_start": config.weeks_until_alternance(),
        "funnel": f,
        "enrichment": e,
        "caps": {"cold_done": cold_done, "cold_cap": cold_cap, "cold_ceiling": config.COLD_CAP,
                 "warm_done": warm_done, "warm_cap": config.WARM_CAP, "daily_cap": config.DAILY_CAP,
                 "ramped": cold_cap != config.COLD_CAP},
        "attention": {
            "replies": [{"company": str(r["Company"]), "contact": str(r["Contact Email"])}
                        for _, r in replied.iterrows()],
            "stalled": [{"company": str(r["Company"]), "idle": r["biz_days_idle"],
                         "last": (r.get("last_reply") or "")[:80]} for r in stalled],
            "overdue": [{"company": str(r["Company"]), "fu": r.get("followup_number", 1),
                         "days": r["biz_days_waiting"], "email": str(r["Contact Email"])[:48]}
                        for r in overdue],
        },
        "warm_network": [{"person": c.get("person"), "company": c.get("company"),
                          "relationship": c.get("relationship", "")} for c in warm_network.load()],
        "school_partners": {"known": len(school_partners.load()), "in_pool": pool_partners},
        "strategy": {"phase": rec["phase"], "recommend": rec.get("recommend"),
                     "ranked": [r for r in rec["ranked"] if r["sent"]]},
        "learning": {"phase": ls_phase(ls), "base": ls["base"], "insights": learning.insights(),
                     "dims": _top_dims(ls)},
        "top_leads": [{"score": l["score"], "company": str(l["Company"]),
                       "role": str(l["Role"]), "flags": _lead_flags(l)} for l in leads],
        "recent": [{"date": str(r["Last Interaction Date"]), "status": str(r["Status"]),
                    "company": str(r["Company"]), "role": str(r["Role"])[:44]}
                   for _, r in recent.head(10).iterrows()],
    }


def ls_phase(ls: dict) -> str:
    return "exploit" if learning.insights() else "explore"


def _top_dims(ls: dict) -> dict:
    out = {}
    for dim in ("company_type", "contract_intent", "role_fit"):
        bs = ls["dimensions"].get(dim, {})
        ranked = sorted(bs.items(), key=lambda kv: kv[1]["sent"], reverse=True)
        out[dim] = [{"name": n, "sent": b["sent"], "replied": b["replied"], "rate": b["rate"]}
                    for n, b in ranked if not n.startswith("(")][:4]
    return out


def render() -> Path:
    data = collect()
    tpl = (_DIR / "dashboard_template.html").read_text(encoding="utf-8")
    html = tpl.replace("/*__DATA__*/{}", json.dumps(data, ensure_ascii=False, default=str))
    out = _DIR / "dashboard.html"
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    import sys
    if "--json" in sys.argv:
        print(json.dumps(collect(), ensure_ascii=False, indent=2, default=str))
    else:
        p = render()
        print(f"[dashboard] wrote {p}")
