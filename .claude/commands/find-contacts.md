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

**Email derivation + verification — let the helper do it (do NOT hand-derive patterns):**
Once you've identified the best-fit person (full name + title), run:
```bash
python /path/to/stationf-agent/contact_finder.py \
    --company "COMPANY_NAME" --person "First Last" --title "TITLE"
```
It resolves the company's real domain (company_resolver), derives the email pattern, and
SMTP/API-verifies it — diacritics stripped, lowercased, real domain only (never stationf.co /
linkedin.com). Pass `--domain DOMAIN` too if you already know the real domain (skips resolution).

- **Exit 0** → it prints a `tracker:` line, e.g. `"First Last (Title)" <email@domain.com>`.
  Copy that string **verbatim** into the tracker (Step 4) and mark the row resolved.
  Also read the `confidence:` line and be transparent about how solid the address is:
  - `smtp_ok` / `api_valid` → confirmed mailbox. Write it clean.
  - `smtp_catchall` / `mx_only` / `api_risky` → the domain is real but the **specific mailbox
    is a pattern guess**. Still write the `"Name (Title)" <guess>` string (it keeps the *person*
    visible to lead ranking and the LinkedIn double-tap), but append `⚠ guessed email (CONFIDENCE)`
    to the Conversation Log. Note: `/daily-agent`'s pre-send gate will **not** email an unconfirmed
    personal guess (it would risk a bounce) — it sends to the generic inbox instead and the LinkedIn
    double-tap reaches the person. So this row's value is the *named person + LinkedIn*, not the guess.
- **Exit 1** → no verifiable email (domain unresolved, or every pattern failed). Leave the row
  as-is: the generic fallback stays and the pre-send anti-bounce gate will skip it if it's dead.

**Always log the person's LinkedIn URL** (from the web search) in the Conversation Log, even
when you found an email — it lets Zineb vet the right human and reach out there if needed.

If no decision-maker can be identified at all: leave the row as-is.

## Step 3.5 — Capture a hook-fact (cheap now = cheaper daily-agent later)

You are already looking at each company here (their site, LinkedIn, job post). While you have it
open, capture **one specific, real hook-fact** — the kind of detail `/daily-agent` would open a
cold email on: a product they build, a technical choice, a recent launch/funding, a hard problem
visible from outside. One sentence, concrete, no adjectives. Store it so the send step doesn't
re-research from cold (this directly eases the 5-hour Claude usage window):

```bash
python -c "import lead_facts; lead_facts.put('COMPANY_NAME', 'ONE specific real fact about what they build', source='URL_YOU_SAW_IT_ON')"
```

Rules: only store something **true and specific** you actually saw — never a guess or a generic
line ("they do AI"). If you couldn't find anything specific, store nothing (the agent will
research it itself). This is best-effort and never blocks enrichment.

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
