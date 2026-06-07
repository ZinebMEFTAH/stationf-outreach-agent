"""Scrape the full Station F company directory once and cache locally.

The directory at https://jobs.stationf.co/startups lists all member startups
as anchors with class `organization-link`. We cache (name, slug) pairs to
JSON so subsequent runs of find_speculative.py don't need to re-scrape.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

CACHE_PATH = Path(__file__).parent / "cache" / "stationf_companies.json"
DIRECTORY_URL = "https://jobs.stationf.co/startups"


@dataclass
class StationFCompany:
    name: str
    slug: str
    profile_url: str


def _scroll_full(page) -> None:
    last_h = 0
    for _ in range(60):
        page.mouse.wheel(0, 6000)
        page.wait_for_timeout(350)
        try:
            h = page.evaluate("document.body.scrollHeight")
        except Exception:
            break
        if h == last_h:
            break
        last_h = h


def scrape_directory(headless: bool = True) -> list[StationFCompany]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = ctx.new_page()
        try:
            page.goto(DIRECTORY_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1500)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PWTimeout:
                pass
            _scroll_full(page)
            raw = page.evaluate(
                """
                () => Array.from(document.querySelectorAll('a.organization-link')).map(a => ({
                  href: a.href,
                  name: (a.innerText || '').trim(),
                }))
                """
            )
        finally:
            ctx.close()
            browser.close()

    seen: set[str] = set()
    out: list[StationFCompany] = []
    for r in raw:
        href = r.get("href") or ""
        name = (r.get("name") or "").strip()
        if "/companies/" not in href or not name:
            continue
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        if slug in seen:
            continue
        seen.add(slug)
        out.append(StationFCompany(name=name, slug=slug, profile_url=href))
    return out


def load_cache() -> list[StationFCompany]:
    if not CACHE_PATH.exists():
        return []
    data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return [StationFCompany(**c) for c in data]


def save_cache(companies: list[StationFCompany]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps([c.__dict__ for c in companies], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def ensure_cache(refresh: bool = False) -> list[StationFCompany]:
    if not refresh:
        cached = load_cache()
        if cached:
            return cached
    print("[companies] scraping Station F directory...")
    cs = scrape_directory()
    save_cache(cs)
    print(f"[companies] cached {len(cs)} companies -> {CACHE_PATH}")
    return cs


if __name__ == "__main__":
    import sys
    refresh = "--refresh" in sys.argv
    cs = ensure_cache(refresh=refresh)
    print(f"Total: {len(cs)}")
    for c in cs[:8]:
        print(f"  {c.name} ({c.slug})")
