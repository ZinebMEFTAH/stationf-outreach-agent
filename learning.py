"""learning.py — self-improving outreach analytics (WS4).

The strategy bandit (`tracker.recommend_strategy_order`) already learns which cold-email
*strategy* earns replies. This extends the same idea to the other ACTIONABLE dimensions of an
outreach attempt:

  - company-type      product startup vs ESN vs large employer   → feeds ranking / who to email
  - role-fit tier     AI/ML core vs backend vs data vs adjacent   → feeds ranking
  - contract-intent   alternance posting vs CDI reframe vs spec.  → feeds ranking
  - subject shape      has a question? / word-count bucket         → feeds subject writing
  - language           FR vs EN                                    → informational

Everything is derived from data already in `contacts.xlsx` — the row plus its Conversation Log
(the cold subject is the first `Agent:` line smtp_send writes) — so there is NO schema change.
Each bucket is scored by its 95% Wilson lower bound (confidence-adjusted, reused from tracker), and
recommendations are emitted ONLY once a bucket clears `min_samples`. Until then the module says
"explore" and its ranking nudge is exactly 0 — so it never tunes on small-sample noise. As live
replies accrue the guidance sharpens on its own.

Consumers:
  - /status       → a learning dashboard (reply rate per bucket, most→least reliable)
  - /daily-agent  → soft nudges (targeting + subject) via recommend()
  - tracker.rank_pending_leads → an optional, data-gated score delta via score_delta() (inert
                    until a bucket has enough data; lazy-imported there to avoid an import cycle)
"""
from __future__ import annotations

import re

import config
import tracker

_AGENT_SUBJECT_RE = re.compile(r"\]\s*Agent:\s*([^\n]+)")   # smtp_send logs "[date] Agent: <subject>"
_FR_HINTS = ("é", "è", "ê", "à", "ç", "ù", "î", " chez ", "alternance", "vous", "votre", "recrut")


def _replied(row) -> bool:
    """A lead counts as replied only on a GENUINE human reply — bounces / auto-responders are
    excluded (shared source of truth with the strategy bandit; see tracker.has_genuine_human_reply)."""
    return tracker.has_genuine_human_reply(row.get("Conversation Log"), row.get("Status"))


def _cold_subject(row) -> str:
    m = _AGENT_SUBJECT_RE.search(str(row.get("Conversation Log") or ""))
    return m.group(1).strip() if m else ""


def _guess_lang(subject: str) -> str:
    s = (subject or "").lower()
    return "fr" if any(h in s for h in _FR_HINTS) else "en"


def _features(row) -> dict[str, str]:
    """Bucket a contacted lead along every learnable, actionable dimension."""
    company = str(row.get("Company") or "")
    role = str(row.get("Role") or "")
    subject = _cold_subject(row)

    if tracker._is_big_corp(company):
        ctype = "large employer"
    elif tracker._is_esn(company):
        ctype = "ESN/staffing"
    else:
        ctype = "product startup"

    words = len(re.findall(r"\S+", subject))
    subj_len = "short (≤6w)" if words and words <= 6 else "long (10w+)" if words >= 10 else "mid (7-9w)"

    return {
        "company_type": ctype,
        "role_fit": tracker._role_fit(role)[1],
        "contract_intent": config.guess_contract_type(role),
        "subject_question": "with '?'" if "?" in subject else "no '?'",
        "subject_length": subj_len if subject else "(unknown)",
        "language": _guess_lang(subject) if subject else "(unknown)",
    }


def _contacted_rows():
    """Rows that were actually cold-emailed (have an Agent: subject line in the log)."""
    df = tracker.load()
    rows = []
    for _, r in df.iterrows():
        if str(r.get("Status") or "").strip() == "Pending":
            continue
        if _AGENT_SUBJECT_RE.search(str(r.get("Conversation Log") or "")):
            rows.append(r)
    return rows


def reply_stats(min_samples: int = 8) -> dict:
    """Reply-rate breakdown per dimension → bucket.

    Returns:
      {
        "base": {"sent": N, "replied": R, "rate": .., "wilson": ..},
        "dimensions": {
          "company_type": {"product startup": {sent, replied, rate, wilson, enough}, ...},
          ...
        },
        "min_samples": min_samples,
      }
    `enough` is True once a bucket has >= min_samples sends (i.e. its number is trustworthy).
    """
    rows = _contacted_rows()
    sent_total = len(rows)
    replied_total = sum(1 for r in rows if _replied(r))

    dims: dict[str, dict] = {}
    for r in rows:
        rep = _replied(r)
        for dim, bucket in _features(r).items():
            d = dims.setdefault(dim, {})
            b = d.setdefault(bucket, {"sent": 0, "replied": 0})
            b["sent"] += 1
            b["replied"] += int(rep)

    for d in dims.values():
        for b in d.values():
            b["rate"] = round(b["replied"] / b["sent"], 3) if b["sent"] else 0.0
            b["wilson"] = round(tracker._wilson_lower_bound(b["replied"], b["sent"]), 3)
            b["enough"] = b["sent"] >= min_samples

    return {
        "base": {
            "sent": sent_total, "replied": replied_total,
            "rate": round(replied_total / sent_total, 3) if sent_total else 0.0,
            "wilson": round(tracker._wilson_lower_bound(replied_total, sent_total), 3),
        },
        "dimensions": dims,
        "min_samples": min_samples,
    }


def insights(min_samples: int = 8, min_replies: int = 8) -> list[str]:
    """High-confidence, human-readable findings — only when the evidence supports them.

    Two guards keep this honest on small data (cold outreach reply rates are low, so a single
    reply must not swing a verdict):
      1. Emit nothing until the whole pipeline has >= min_replies GENUINE replies (bounces
         already excluded). With a handful of replies the honest phase is "explore".
      2. Judge a bucket by its Wilson lower bound vs the base rate — never a raw ratio — and
         require a few real replies in the bucket before calling it a winner.
    Returns [] in the explore phase.
    """
    stats = reply_stats(min_samples)
    base = stats["base"]["rate"]
    if stats["base"]["replied"] < min_replies or base <= 0:
        return []
    out = []
    for dim, buckets in stats["dimensions"].items():
        for name, b in buckets.items():
            if not b["enough"] or name.startswith("("):
                continue
            # winner: confidence-adjusted rate (Wilson LB) still beats the base, on real replies
            if b["replied"] >= 3 and b["wilson"] > base:
                out.append(f"↑ {dim}='{name}' reliably above base "
                           f"({b['replied']}/{b['sent']} = {b['rate']*100:.0f}%, base {base*100:.0f}%) — favour it.")
            # laggard: plenty of sends, rate well under half the base
            elif b["sent"] >= 2 * min_samples and b["rate"] < base * 0.5:
                out.append(f"↓ {dim}='{name}' below base "
                           f"({b['replied']}/{b['sent']} = {b['rate']*100:.0f}%, base {base*100:.0f}%) — de-prioritise / rethink.")
    return out


def recommend(min_samples: int = 8) -> dict:
    """Soft guidance for /daily-agent. Explore until there's evidence, then nudge."""
    ins = insights(min_samples)
    stats = reply_stats(min_samples)
    if not ins:
        return {
            "phase": "explore",
            "insights": [],
            "note": (f"Not enough reply data yet ({stats['base']['replied']} replies / "
                     f"{stats['base']['sent']} sent). Keep variety high across company types, "
                     f"contract asks and subject shapes so every bucket gets sampled."),
        }
    return {"phase": "exploit", "insights": ins,
            "note": "Bias toward the ↑ buckets and away from the ↓ ones — but company FIT still wins."}


def score_delta(company: str, role: str, min_samples: int = 8, cap: int = 8) -> tuple[int, str]:
    """Data-gated learned nudge for tracker.rank_pending_leads.

    Returns (delta, reason). `delta` is an int in [-cap, cap]: the summed contribution of the
    lead's company_type and contract_intent buckets, each counted ONLY when that bucket has
    >= min_samples contacted AND enough base data exists. Returns (0, "") while data is thin —
    so ranking is completely unchanged until there is real evidence. Never raises.
    """
    try:
        stats = reply_stats(min_samples)
        base = stats["base"]["wilson"]
        # Stay inert until there is real reply volume — not just sends — so the nudge never
        # rests on one or two lucky replies (matches the insights() gate).
        if stats["base"]["sent"] < min_samples * 2 or stats["base"]["replied"] < 8:
            return 0, ""
        feats = _features({"Company": company, "Role": role, "Status": "Pending",
                           "Conversation Log": ""})
        delta = 0.0
        bits = []
        for dim in ("company_type", "contract_intent"):
            bucket = stats["dimensions"].get(dim, {}).get(feats[dim])
            if bucket and bucket["enough"]:
                contrib = max(-cap, min(cap, (bucket["wilson"] - base) * 100))
                if abs(contrib) >= 1:
                    delta += contrib
                    bits.append(f"{dim}:{feats[dim]} {contrib:+.0f}")
        delta = int(max(-cap, min(cap, round(delta))))
        return (delta, "learned(" + ", ".join(bits) + ")") if delta else (0, "")
    except Exception:
        return 0, ""


if __name__ == "__main__":
    import json
    print(json.dumps(reply_stats(), indent=2, ensure_ascii=False, default=str))
    print("\nINSIGHTS:", insights() or "(explore — not enough data yet)")
