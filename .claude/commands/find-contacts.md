Enrich contacts.xlsx: replace generic fallback emails (contact@, info@, hello@, team@, jobs@) with named decision-makers found via automated scraping, then web search for anything remaining.
Working directory: /path/to/stationf-agent

## How it works

Two-pass enrichment:

**Pass 1 — Automated (Playwright, no LLM needed)**
For each target row, run `contact_finder.py` which visits:
1. The Station F company profile → team section (JSON-LD, LinkedIn links, DOM card patterns)
2. The company website `/team`, `/about`, `/equipe`, `/about-us` pages
3. The job detail page (poster/contact info)

**Pass 2 — Web search fallback (LLM)**
For rows where Pass 1 found nothing, do targeted web searches on LinkedIn / Crunchbase.

## Step 1 — Read the tracker

```bash
cd /path/to/stationf-agent && python -c "import tracker, json; df=tracker.load(); print(df.to_json(orient='records', date_format='iso', indent=2))"
```

Filter for rows where:
- Status is `Pending`, or `Emailed` / `Followed Up` if `$ARGUMENTS` includes `--all`
- **Never** process `Rejected` rows (no point enriching them)
- Contact Email local part is one of: `contact`, `hello`, `info`, `team`, `jobs`, `careers`, `recrutement` (no display name)

If `$ARGUMENTS` specifies a company name, only process that company.
If `$ARGUMENTS` specifies `--limit N`, process at most N companies this run.

## Step 2 — Pass 1: automated contact_finder

`contact_finder` now **resolves the company's real domain itself** (name → domain via
`company_resolver`, Clearbit-backed, high-precision) when you don't pass `--domain`. This is
what makes WTTJ / HelloWork rows enrichable — their fallback email (`contact@<guess>.com`) is
usually a *wrong* guessed domain, so do NOT trust it.

Rule of thumb:
- Station F rows, or any row whose email domain is clearly the **real** company site →
  pass `--domain` (and `--slug` / `--website` if known).
- WTTJ / HelloWork rows, or any generic `contact@<slug>.com` guess → **omit `--domain`** and
  let it resolve. If resolution finds nothing (some French startups aren't in Clearbit), the
  row falls through to Pass 2 (web search).

Run:
```bash
python /path/to/stationf-agent/contact_finder.py \
    --company "COMPANY_NAME" \
    [--domain "DOMAIN"] \
    [--slug "STATION_F_SLUG"] \
    [--website "https://WEBSITE_URL"] \
    [--job-url "https://JOB_URL"]
```

(You can also resolve a domain on its own: `python company_resolver.py "COMPANY_NAME"`.)

- Exit 0 with a `tracker:` line → a named contact was found. Write it to the tracker (see Step 4) and mark this row as **resolved**.
- Exit 1 → no contact found automatically. Move this row to the Pass 2 queue.

## Step 3 — Pass 2: web search for unresolved rows

For each row NOT resolved by Pass 1:

**Search strategy** (use WebSearch):
1. `"COMPANY_NAME" site:linkedin.com CTO OR "Head of AI" OR "Head of Engineering" OR "VP Engineering"`
2. `"COMPANY_NAME" about team`
3. `"COMPANY_NAME" crunchbase OR wellfound`

**Decision-maker priority — size-aware (who actually reads cold email differs by company size):**

First estimate headcount (Crunchbase / LinkedIn "employees" / Station F profile).

- **Small (< ~30 people)** → email the **CTO / Head of AI / Co-founder / CEO**.
  At this size the technical founder reads everything and decides hiring directly.
- **Mid (~30–150)** → email the **Head of AI / Head of Engineering / Engineering Manager /
  Lead** of the relevant team. The CTO exists but delegates; a team lead is reachable and
  owns the req. Head of Talent is a fine second choice.
- **Large (> ~150)** → do **NOT** email the CTO/C-suite (cold mail never reaches them).
  Target **Head of Talent / Tech Recruiter / Campus Manager**, or a **specific team lead**
  named on the job posting. A reachable recruiter beats an unreachable CTO every time.

The goal is the person who will actually open the email and act on it — not the most senior
title. A read email from a recruiter is worth infinitely more than an ignored one from the CTO.

**Email derivation**:
- Verified email found publicly → use as-is
- Known pattern (firstname.lastname@ from press release or signed blog) → apply to person's name
- No pattern → infer: `firstname.lastname@domain` for French startups; `firstname@domain` for very small/founder-led teams
- Strip diacritics in local part: é→e, ç→c, etc. Lowercase.
- Use the company's real domain, not stationf.co or linkedin.com.

**Verify before writing (mandatory)**:
```bash
python /path/to/stationf-agent/email_verify.py DERIVED_EMAIL
```
- Exit 0 → write to tracker
- Exit 1 (`unverifiable`) → try up to 2 alternative patterns (firstname@, f.lastname@), verify each
- All patterns fail → fall back to `contact@domain`, verify it. If that also fails → leave row as-is
- `[mx_only]` → write but append `⚠ guessed` in Conversation Log

If no person can be identified: leave the row as-is.

## Step 4 — Update the tracker

```bash
python -c "
import tracker
df = tracker.load()
mask = (df['Company'].str.strip().str.lower() == 'COMPANY'.lower()) & (df['Status'].str.strip() == 'Pending')
df.loc[mask, 'Contact Email'] = '\"First Last (Title)\" <email@domain.com>'
tracker.save(df)
print('Updated:', df.loc[mask, ['Company', 'Contact Email']].to_string())
"
```

## Report at the end

- Companies processed
- Pass 1 resolved (automated scraping): name / title / email / source
- Pass 2 resolved (web search): name / title / email / confidence
- Companies where no person was found (left as fallback)
- Total rows updated in contacts.xlsx
