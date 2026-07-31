"""global_brands.py — the internationally-recognizable-employer channel.

Zineb wants her outreach to lean toward international companies — but "international" only
converts if the company can actually sign a FRENCH contract (alternance needs a French employer;
a CDI is easiest through a local entity). A remote role at a US-only startup can't do alternance;
a role at Datadog's Paris office or at a Paris-HQ'd scale-up (Mistral, Alan, Qonto…) can.

This module recognizes those companies: globally-recognizable tech employers with a real
France/Paris office that hire juniors and alternants. It is the reachable-international lever
(complement to remotive.py, which is the remote-abroad lever). A recognized brand is boosted in
tracker.rank_pending_leads so these float up the daily queue, and carries a `channel` so
/daily-agent picks the right move:

  • channel "cold"   — French/EU-origin scale-up, reachable by a warm cold email. Lead on the
                       product + the AI-agent-demo card; ask alternance→CDI→CDD as usual.
  • channel "portal" — global giant with a big Paris eng office (Google, Meta, Datadog…). A cold
                       inbox dies in campus recruiting / ATS, so route to the APPLICATION path
                       (careers portal + /cover-letter). Still worth surfacing — the French entity
                       CAN take an alternant/CDI, and a recognizable name is worth the effort.

Data lives in a sidecar (cache/global_brands.json) — PUBLIC info (company + where they hire), not
personal, so it's committed (like school_partners.json, unlike warm_contacts.json). Seeded from
research below; grow it as Zineb learns which brands actually recruit her profile in France.

Each entry: {"company", "channel" ("cold"|"portal"), "origin" (HQ country), "note"}.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "cache" / "global_brands.json"


def _norm(s: str) -> str:
    # Strip legal suffixes/punctuation only — NOT geographic words (same trap as school_partners:
    # stripping "France" turned "Air France" into "air" and mis-matched unrelated names).
    s = (s or "").lower()
    s = re.sub(r"[’'`.,\-_/&()]", " ", s)
    s = re.sub(r"\b(sas|sarl|sasu|sa|inc|ltd|llc|gmbh|group|groupe|technologies|technology|labs|ai)\b",
               " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(s: str) -> set:
    return {t for t in _norm(s).split() if len(t) >= 2}


def load() -> list[dict]:
    if not _PATH.exists():
        seed()
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(rows: list[dict]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def add(company: str, channel: str = "cold", origin: str = "", note: str = "") -> bool:
    if not company:
        return False
    channel = channel if channel in ("cold", "portal") else "cold"
    rows = [r for r in load() if _norm(r.get("company", "")) != _norm(company)]
    rows.append({"company": company.strip(), "channel": channel,
                 "origin": origin.strip(), "note": note.strip()})
    _save(rows)
    return True


def match(company: str) -> dict | None:
    """The brand entry for a company, or None. Token-set match (one name's tokens a subset of the
    other's) — so "Mistral" matches "Mistral AI" but "Alan" never matches "Balance"."""
    ct = _tokens(company)
    if not ct:
        return None
    for r in load():
        rt = _tokens(r.get("company", ""))
        if rt and (rt <= ct or ct <= rt):
            return r
    return None


def is_brand(company: str) -> bool:
    return match(company) is not None


def channel_of(company: str) -> str:
    """"cold" | "portal" | "" — how /daily-agent should approach this brand."""
    m = match(company)
    return m.get("channel", "") if m else ""


def summary(company: str) -> str:
    """One-line reason string for ranking / openers, or "" if not a recognized brand."""
    m = match(company)
    if not m:
        return ""
    origin = f", {m['origin']}" if m.get("origin") else ""
    if m.get("channel") == "portal":
        return f"marque mondiale (bureau France{origin}) — recrute juniors/alternants, voie candidature"
    return f"scale-up internationale{origin} — recrute juniors/alternants, joignable directement"


def companies() -> set[str]:
    return {r.get("company", "").strip() for r in load() if r.get("company")}


# ── Seed: recognizable employers that hire juniors/alternants IN FRANCE (researched July 2026) ────
# Two channels (see module docstring). Curated for truthfulness: every company here has a real
# France/Paris presence that recruits Zineb's profile (AI / ML / Data / Backend, junior/alternant).
# "cold"  = French/EU-origin scale-up reachable by a warm cold email.
# "portal"= global giant with a big Paris eng office — apply via portal, cold inbox won't land.
_SEED_COLD = [
    # French / EU-origin AI-native & tech scale-ups — email them directly, alternance is on the table
    ("Mistral AI", "France"), ("Hugging Face", "France/US"), ("Dust", "France"),
    ("Poolside", "France"), ("Photoroom", "France"), ("Dataiku", "France/US"),
    ("Owkin", "France/US"), ("Nabla", "France"), ("Shift Technology", "France"),
    ("Alan", "France"), ("Qonto", "France"), ("PayFit", "France"), ("Doctolib", "France"),
    ("Contentsquare", "France"), ("Pigment", "France"), ("Spendesk", "France"),
    ("Ledger", "France"), ("Back Market", "France"), ("Mirakl", "France"),
    ("Aircall", "France"), ("360Learning", "France"), ("Sorare", "France"),
    ("ManoMano", "France"), ("Swile", "France"), ("Vestiaire Collective", "France"),
    ("Ekimetrics", "France"), ("Deepki", "France"), ("Descartes Underwriting", "France"),
    ("Algolia", "France/US"), ("Criteo", "France"), ("Ubisoft", "France"),
    ("BlaBlaCar", "France"), ("Younited", "France"),
    # ── 2026-07-31 expansion: more recognizable French scale-ups that run alternance programs ──
    # Fintech / SaaS with real Paris eng teams
    ("Pennylane", "France"), ("Alma", "France"), ("Agicap", "France"), ("Yousign", "France"),
    ("Malt", "France"), ("Shine", "France"), ("Lydia", "France"), ("Payplug", "France"),
    ("Spendesk", "France"), ("Lifen", "France"),
    # Data / AI-native & dev-tools
    ("Kili Technology", "France"), ("Gladia", "France"), ("Veesion", "France"),
    ("Golem.ai", "France"), ("Hugging Face", "France/US"), ("iAdvize", "France"),
    ("Ornikar", "France"), ("Ledger", "France"),
    # Consumer / media / infra with strong data & backend hiring
    ("Deezer", "France"), ("Dailymotion", "France"), ("Believe", "France"),
    ("Veepee", "France"), ("Withings", "France"), ("OVHcloud", "France"),
    ("Voodoo", "France"), ("Jellysmack", "France"), ("Qonto", "France"),
]
_SEED_PORTAL = [
    # Global giants with substantial Paris engineering — apply via careers portal (+ /cover-letter)
    ("Google", "US"), ("Meta", "US"), ("Amazon", "US"), ("Microsoft", "US"),
    ("Datadog", "US"), ("Snowflake", "US"), ("Salesforce", "US"), ("ServiceNow", "US"),
    ("Uber", "US"), ("Spotify", "Sweden"), ("Stripe", "US"), ("Nvidia", "US"),
    ("Zalando", "Germany"), ("Airbnb", "US"), ("Booking.com", "Netherlands"),
    ("Adyen", "Netherlands"), ("Palantir", "US"), ("Scaleway", "France"),
    # ── 2026-07-31 expansion: more global giants / enterprise leaders with a real Paris eng office ──
    ("Databricks", "US"), ("MongoDB", "US"), ("Elastic", "Netherlands/US"), ("Confluent", "US"),
    ("Apple", "US"), ("Dassault Systèmes", "France"), ("SAP", "Germany"), ("Oracle", "US"),
    ("IBM", "US"), ("Shopify", "Canada"), ("Twilio", "US"), ("Qualcomm", "US"),
]


def seed(force: bool = False) -> int:
    """Populate cache/global_brands.json from the researched seed. No-op if already populated
    unless force=True. Returns the number of entries written."""
    if _PATH.exists() and not force:
        try:
            if json.loads(_PATH.read_text(encoding="utf-8")):
                return 0
        except Exception:
            pass
    rows, seen = [], set()
    # cold first, so a name accidentally listed in both channels keeps the (reachable) cold entry
    for company, origin in _SEED_COLD:
        key = _norm(company)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"company": company, "channel": "cold", "origin": origin,
                     "note": "scale-up internationale — recrute juniors/alternants en France"})
    for company, origin in _SEED_PORTAL:
        key = _norm(company)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"company": company, "channel": "portal", "origin": origin,
                     "note": "bureau France — voie candidature (ATS/campus)"})
    _save(rows)
    return len(rows)


if __name__ == "__main__":
    import sys
    a = sys.argv[1:]
    if a and a[0] == "seed":
        print("seeded", seed(force="--force" in a), "entries")
    elif a and a[0] == "list":
        for r in sorted(load(), key=lambda r: (r.get("channel", ""), r.get("company", ""))):
            print(f"  · [{r.get('channel','?'):6}] {r['company']:24} — {r.get('origin','')}")
    elif a and a[0] == "match" and len(a) > 1:
        print(json.dumps(match(a[1]), ensure_ascii=False, indent=2))
    else:
        print(__doc__)
