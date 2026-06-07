Find Station F companies that have NOT posted AI/Backend/Data roles and add speculative outreach targets to contacts.xlsx.
Working directory: /path/to/stationf-agent

Zineb's pitch for speculative outreach: she doesn't wait for a job posting — she proactively contacts companies that would plausibly need an AI / Backend / MLOps alternant but haven't posted yet.

## Step 1 — Load the company directory

```bash
cd /path/to/stationf-agent && python companies.py
```

This outputs the cached list of Station F member companies (name + slug). If the cache looks stale (> 7 days old based on file mtime), run with `--refresh`.

Then load the evaluated-companies state cache:
```bash
cat /path/to/stationf-agent/cache/speculative_state.json
```

And read the tracker to see which companies are already in contacts.xlsx:
```bash
python -c "import tracker, json; df=tracker.load(); print(df['Company'].dropna().unique().tolist())"
```

## Step 2 — Identify candidates

Skip a company if ANY of these are true:
- Already present in contacts.xlsx (any status)
- Already in speculative_state.json (already evaluated, relevant or not)

If `$ARGUMENTS` specifies `--batch N`, process at most N companies. Default: **5** (to avoid burning too many web searches in one run — Station F has 500+ companies, evaluate them steadily over weeks).
If `$ARGUMENTS` specifies `--only "Company Name"`, only evaluate that one.

## Step 3 — Evaluate each candidate via web search

For each candidate company, use WebSearch:
1. Search `"COMPANY_NAME" site:jobs.stationf.co` to confirm they're a member
2. Search their website / Crunchbase / LinkedIn to understand what they do

**Mark as relevant** ONLY if the company:
- Has a real engineering/tech/data/AI product or platform, AND
- Is startup-stage and would plausibly hire an alternant in AI / Backend / Data / MLOps

**Mark as irrelevant** for: pure marketing/PR agencies, beauty/fashion/food/hospitality with no tech team, consulting firms with no product, NGOs, design studios, founder-only shops with no engineering hires.

If relevant, identify the best decision-maker to email using this cascade:

**Step 1 — Automated (try first, fast, no LLM cost):**
```bash
python /path/to/stationf-agent/contact_finder.py \
  --company "COMPANY_NAME" \
  --domain "DOMAIN_IF_KNOWN" \
  --slug "STATION_F_SLUG"
```
If it prints a `tracker:` line → use that contact directly.

**Step 2 — Web search (only if Step 1 returns nothing):**
Same decision-maker priority as `/find-contacts`: CTO > Head of AI > Head of Engineering > Co-founder/CEO > Recruiter.

## Step 4 — Persist state

After evaluating each company, immediately update the state file to avoid re-processing:

```bash
python -c "
import json
from pathlib import Path
p = Path('/path/to/stationf-agent/cache/speculative_state.json')
state = json.loads(p.read_text()) if p.exists() else {}
state['COMPANY_SLUG'] = {
    'name': 'COMPANY_NAME',
    'is_relevant': True,
    'suggested_role': 'AI Engineer (alternance proposée)',
    'contact_name': 'First Last',
    'contact_email': 'email@domain.com',
    'email_confidence': 'pattern_guess',
}
p.write_text(json.dumps(state, indent=2, ensure_ascii=False))
"
```

## Step 5 — Add relevant companies to contacts.xlsx

For each relevant company with a suggested role and contact email:

```bash
python -c "
import tracker
tracker.add_contact(
    company='COMPANY_NAME',
    role='[Suggested] AI Engineer (alternance proposée)',
    contact_email='First Last (CTO) <email@domain.com>',
    status='Pending',
)
"
```

The `[Suggested]` prefix tells `/daily-agent` to treat this as speculative outreach (the email will make no reference to a job posting and will instead make the case for creating or filling a role for Zineb).

## Report at the end

- Companies evaluated this run
- Relevant / irrelevant breakdown with brief rationale
- Rows added to contacts.xlsx
- State cache size (total evaluated to date)
