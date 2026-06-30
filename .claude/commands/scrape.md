Scrape the job boards and add new AI/Backend/Data listings to contacts.xlsx.
Working directory: /path/to/stationf-agent

## What this does
Runs the multi-source scraper across **Station F** (Playwright, https://jobs.stationf.co/search), **Welcome to the Jungle** (its public Algolia jobs API, France only), **HelloWork** (server-rendered search, France-wide), **APEC** (its public JSON API, cadre/engineering), **France Travail** (its official OAuth2 API — inert unless `FRANCE_TRAVAIL_ID`/`FRANCE_TRAVAIL_SECRET` are in `.env`), **Free-Work** (its public JSON API; CDI/alternance only, freelance filtered out), and **La Bonne Alternance** (the state-run "hidden market" API — inert unless `LBA_API_KEY` is in `.env`; surfaces software/data alternance postings *and* algorithm-flagged recruiters that haven't posted, filtered by métier + Île-de-France), filters for roles matching AI / Backend / Data / MLOps keywords, deduces a contact email per company, and inserts new rows as `Pending` (skips existing company+role combos). Station F rows are enriched inline with a named contact; the others are discovery-only, so their rows rely on the `/find-contacts` pass below. La Bonne Alternance ships the company website directly (its rows already carry the real domain), and its no-posting recruiters are added as `[Suggested]` speculative pitches.

## Run

```bash
cd /path/to/stationf-agent && python scraper.py          # all sources (stationf + wttj + hellowork + apec + francetravail + freework + labonnealternance)
```

Pick one source, preview without writing, or cap pages (Station F: total pages; WTTJ: pages per query):
```bash
cd /path/to/stationf-agent && python scraper.py --source stationf
cd /path/to/stationf-agent && python scraper.py --dry-run
cd /path/to/stationf-agent && python scraper.py --max-pages 3
```

## After scraping

Read the output to see how many rows were added. Then report:
- Total listings matched
- Rows inserted / skipped (duplicates) / emails updated
- Any errors

After scraping, **always** run `/find-contacts` on any newly added rows that have a generic fallback email (`contact@`, `info@`, `hello@`, `team@`, `jobs@`). This ensures every new row has a named person before the main agent tries to email them.

If `$ARGUMENTS` contains "skip-enrich", skip the find-contacts pass.
