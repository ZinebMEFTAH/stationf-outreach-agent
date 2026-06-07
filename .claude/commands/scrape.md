Scrape the Station F job board and add new AI/Backend/Data listings to contacts.xlsx.
Working directory: /path/to/stationf-agent

## What this does
Runs the Playwright scraper against https://jobs.stationf.co/search, filters for roles matching AI / Backend / Data / MLOps keywords, deduces a contact email per company, and inserts new rows as `Pending` (skips existing company+role combos).

## Run

```bash
cd /path/to/stationf-agent && python scraper.py
```

For a dry-run preview without writing to contacts.xlsx:
```bash
cd /path/to/stationf-agent && python scraper.py --dry-run
```

To limit pages (faster test):
```bash
cd /path/to/stationf-agent && python scraper.py --max-pages 3
```

## After scraping

Read the output to see how many rows were added. Then report:
- Total listings matched
- Rows inserted / skipped (duplicates) / emails updated
- Any errors

After scraping, **always** run `/find-contacts` on any newly added rows that have a generic fallback email (`contact@`, `info@`, `hello@`, `team@`, `jobs@`). This ensures every new row has a named person before the main agent tries to email them.

If `$ARGUMENTS` contains "skip-enrich", skip the find-contacts pass.
