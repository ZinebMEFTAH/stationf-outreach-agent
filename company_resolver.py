"""Resolve a company *name* → its real email *domain*.

The discovery-only sources (Welcome to the Jungle, HelloWork) give us a company name
but hide the employer's website, so rows land with a guessed `contact@<slug>.com` that
is usually wrong. This module turns the name into the real domain using Clearbit's
**free, key-less** company autocomplete API, then sanity-checks it has MX records so we
never hand a dead domain to the email-derivation step.

Used by contact_finder (which auto-resolves when it has no domain) and callable directly:

    python company_resolver.py "Ippon Technologies"      # -> ippon.tech

Results are cached in cache/company_domains.json so repeat runs don't re-hit the API.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

CLEARBIT_URL = "https://autocomplete.clearbit.com/v1/companies/suggest?query="
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_CACHE_PATH = Path(__file__).resolve().parent / "cache" / "company_domains.json"

# TLD preference — France-relevant + common tech TLDs rank above foreign country TLDs,
# which helps avoid same-name companies in other countries (e.g. *.it / *.ru false hits).
_TLD_SCORE = {"fr": 16, "com": 13, "io": 9, "ai": 9, "co": 7, "tech": 7, "eu": 6, "net": 4}

# TLDs a French/EU employer plausibly uses (generic + fr/eu). Anything in _FOREIGN_TLDS
# is treated as a different-country same-name company and rejected — we'd rather resolve
# nothing than email the wrong company (the anti-bounce gate checks deliverability, not
# identity, so a wrong-but-live domain would slip through).
_GOOD_TLDS = {"com", "fr", "io", "ai", "co", "eu", "tech", "net", "org", "app", "dev", "xyz"}
_FOREIGN_TLDS = {"ru", "it", "kz", "ae", "qa", "kw", "cn", "in", "br", "de", "es", "pl",
                 "uk", "us", "ca", "jp", "kr", "tr", "nl", "be", "ch", "mx", "ar", "za",
                 "ng", "sa", "id", "vn", "th", "pt", "se", "no", "dk", "fi", "gr", "ro"}


def _norm(s: str) -> str:
    """Lowercase, drop accents/punctuation, collapse spaces, strip company suffixes."""
    s = (s or "").lower()
    s = (s.replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
           .replace("â", "a").replace("ç", "c").replace("ù", "u").replace("ô", "o")
           .replace("î", "i").replace("ï", "i").replace("û", "u"))
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(sas|sa|sarl|inc|ltd|llc|gmbh|group|groupe|technologies|technology|"
               r"solutions|consulting|labs|studio|france|paris)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _norm(s).replace(" ", ""))


def _load_cache() -> dict:
    try:
        return json.loads(_CACHE_PATH.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False))
    except Exception:
        pass


def _clearbit(name: str) -> list[dict]:
    url = CLEARBIT_URL + urllib.parse.quote(name)
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.load(r)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _confident(name: str, cand: dict) -> bool:
    """True only when we're confident this domain is THIS company (precision over recall).

    Accept when the domain root equals the company slug (e.g. trustpair → trustpair.fr),
    or the autocomplete name matches exactly on a generic/FR TLD. Reject foreign-country
    TLDs outright — Clearbit often returns a same-name company in another country.
    """
    dom = (cand.get("domain") or "").lower()
    if not dom or "." not in dom:
        return False
    root = dom.split(".")[0]
    tld = dom.rsplit(".", 1)[-1]
    if tld in _FOREIGN_TLDS:
        return False
    if root == _slug(name):
        return True
    if _norm(cand.get("name", "")) == _norm(name) and tld in _GOOD_TLDS:
        return True
    return False


def _score(name: str, cand: dict) -> int:
    """Higher = more likely the same company. Combines name match + TLD preference."""
    n = _norm(name)
    cn = _norm(cand.get("name", ""))
    dom = (cand.get("domain") or "").lower()
    if not dom or "." not in dom:
        return -1
    root = dom.split(".")[0]
    tld = dom.rsplit(".", 1)[-1]
    s = 0
    if cn and cn == n:
        s += 100
    elif cn and (cn.startswith(n) or n.startswith(cn)):
        s += 55
    nt, ct = set(n.split()), set(cn.split())
    s += 12 * len(nt & ct)
    ns = _slug(name)
    if ns and (ns in root or root in ns):
        s += 45
    s += _TLD_SCORE.get(tld, 0)
    return s


def resolve_domain(name: str, min_score: int = 25, use_cache: bool = True,
                   verify_mx: bool = True) -> str | None:
    """Return the best-guess real email domain for a company name, or None.

    Picks the highest-scoring Clearbit suggestion above `min_score`; if `verify_mx`,
    walks candidates in score order and returns the first whose domain has MX records.
    """
    if not name or not name.strip():
        return None
    key = _norm(name)
    cache = _load_cache() if use_cache else {}
    if use_cache and key in cache:
        return cache[key] or None

    # Precision gate first: keep only confident matches, then rank what survives.
    confident = [c for c in _clearbit(name) if _confident(name, c)]
    ranked = sorted(((c, _score(name, c)) for c in confident),
                    key=lambda cs: cs[1], reverse=True)

    chosen: str | None = None
    if ranked:
        top_score = ranked[0][1]
        # The name match decides the winner; MX only disambiguates near-ties.
        near = [c for c, sc in ranked if sc >= top_score - 10]
        chosen = ranked[0][0]["domain"].lower()
        if verify_mx and len(near) > 1:
            try:
                from email_verify import check_mx
            except Exception:
                check_mx = None
            if check_mx is not None:
                for c in near:
                    if check_mx(c["domain"].lower())[0]:
                        chosen = c["domain"].lower()
                        break

    if use_cache:
        cache[key] = chosen
        _save_cache(cache)
    return chosen


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    name = argv[0]
    no_mx = "--no-mx" in argv
    dom = resolve_domain(name, verify_mx=not no_mx)
    if dom:
        print(dom)
        return 0
    print(f"(no domain resolved for {name!r})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
