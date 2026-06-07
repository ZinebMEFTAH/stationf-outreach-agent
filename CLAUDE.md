# stationf_agent — Zineb's Alternance Outreach Agent

Autonomous daily agent to secure a work contract for Zineb Meftah — **CDI, CDD, or
Alternance** (M1 2026-2027), in that order of preference. The agent leads with whatever
fits each posting (see contract-ask logic in `/daily-agent`); it never lists all three.
Full profile: `about_me.txt` | Full protocol: `instructions.txt`

## contacts.xlsx — Strict 6-Column Schema (never add, rename, or reorder columns)

| Column | Description |
|---|---|
| Company | Startup name |
| Role | Scraped title, or `[Suggested] Role` for speculative pitches |
| Contact Email | RFC 5322 `"Name (Title)" <addr>` preferred; plain `addr` if unknown |
| Conversation Log | Append-only: `[YYYY-MM-DD] Agent: subject \n [YYYY-MM-DD] Contact: body` |
| Last Interaction Date | YYYY-MM-DD of last sent or received email |
| Status | `Pending` / `Emailed` / `Replied` / `Followed Up` / `Rejected` / `Interview Scheduled` |

## Mandatory P.S. Footer

Appended automatically by `smtp_send.py` — **never write it yourself**. Applied on **cold
(first-contact) emails only**: it's the AI-agent disclosure / differentiator at first contact.
Follow-ups carry the signature but no footer (redundant); replies carry signature only (the
conversation is human now); alerts are sent raw. English bodies get `FOOTER_EN`, French get `FOOTER_FR`.

> P.S. Ce message a été entièrement rédigé et envoyé de façon autonome par un agent IA que j'ai conçu et déployé en production : scraping Playwright du board Station F, qualification des opportunités par LLM, personnalisation du message selon le profil de chaque entreprise, envoi SMTP et relances automatiques — le tout orchestré avec des skills Claude Code. C'est précisément ce type de pipeline IA bout-en-bout que je veux contribuer à construire avec vous.

## Constants
- **COLD_CAP** = 2 new first-contact emails per calendar day
- **WARM_CAP** = 3 follow-ups + replies per calendar day  
- **DAILY_CAP** = 5 total outbound actions (COLD_CAP + WARM_CAP)
- **FOLLOWUP_DAYS** = 4 business days without reply → trigger follow-up

## Two-Repo Push Workflow

This is the **private** repo (`stationf-agent`) — the live system the VM runs from.
There is also a **public** sanitized showcase (`stationf-outreach-agent`).

**Every push must update both** — but the public one must NEVER receive sensitive data
(real `contacts.xlsx`, `drafts/`, CV PDFs, `.env`, personal emails/phone/address):

```bash
git push                 # 1. push the private repo as usual
bash sync_public.sh      # 2. sanitize + push the public mirror (allowlist + secret-scan gate)
```

`sync_public.sh` copies only an allowlist of safe files, scrubs personal data, and ABORTS
before pushing if its secret-scan gate detects anything sensitive. Safe by construction.

## I/O Scripts — Call Only for Raw System I/O

```bash
# Sync Gmail inbox → updates contacts.xlsx, prints reply summaries to stdout
python imap_fetch.py [--since-days 7]

# Send one email + auto-log to contacts.xlsx (omit --send for dry-run)
python smtp_send.py \
  --to "ADDR" --subject "TEXT" --body-file PATH \
  --company "NAME" --role "TITLE" --kind cold|followup|reply|alert \
  [--attach documents/CV_Zineb_Meftah_FR.pdf] \
  --send
# --kind cold → signature + P.S. footer; followup/reply → signature only;
# alert → raw internal notification (no footer, not logged, not counted)

# Scrape Station F job board → insert AI/Backend/Data listings as Pending rows
python scraper.py [--dry-run] [--max-pages N]

# Scrape full Station F company directory → cache/stationf_companies.json
python companies.py [--refresh]

# Pre-flight self-test — fast offline health check (run before relying on the system)
python preflight.py            # exit 0 = healthy, 1 = broken (lists failures)
# Cron run scripts call this automatically and SKIP the run + alert if it fails.

# Read contacts.xlsx as JSON (pipe into your reasoning)
python -c "import tracker, json; df=tracker.load(); print(df.to_json(orient='records', date_format='iso', indent=2))"

# Add a row manually
python -c "import tracker; tracker.add_contact(company='X', role='Y', contact_email='Z@domain.com')"

# Update a row's email (e.g. after finding a named decision-maker)
python -c "
import tracker
df = tracker.load()
mask = df['Company'].str.strip().str.lower() == 'company name here'.lower()
df.loc[mask, 'Contact Email'] = 'Name (Title) <email@domain.com>'
tracker.save(df)
"
```

## Documents
- `documents/CV_Zineb_Meftah_FR.tex` / `_EN.tex` — LaTeX source, adapted per role focus
- `documents/CV_Zineb_Meftah_FR_custom.pdf` / `_EN_custom.pdf` — compiled output (via `cv_builder.py`)
- `documents/Relevez_notes.pdf` — academic transcript (attach only if explicitly requested)
- `documents/certificates.pdf` — certificates (attach only if explicitly requested)

**Attachment rules (anti-spam):**
- ❌ **Cold emails**: NO attachment. Include LinkedIn URL inline in body instead.
- ✅ **Follow-ups / replies**: build adapted CV with `python cv_builder.py --lang fr|en --focus ai|backend|mlops|data|fullstack --role "ROLE"`, attach `_custom.pdf`

```
/cv-builder --lang fr --focus ai --role "AI Engineer"
# or just: /cv-builder Craft AI   ← looks up role from tracker automatically
# → documents/CV_Zineb_Meftah_FR_custom.pdf
```

## Daily Schedule

| Time (Paris) | Skill | What it does |
|---|---|---|
| **08:00** | `/scrape` | Scrape new Station F jobs, contact_finder enrichment for new rows |
| **08:30** | `/find-contacts --all` | Enrich any remaining generic contact@ emails (up to 8/day) |
| **09:00** | `/daily-agent` | Inbox sync → priority queue → send up to 5 emails (2 cold + 3 warm) |
| **12:00** | `/followup-check` | Inbox-only midday scan, alerts if serious replies, no sends |
| **21:00** | `/speculative` | Evaluate 5 new Station F companies for proactive pitches |

When Mac is **on**: launchd fires `vm/run_*.sh` → `claude --print` → skill executes.
`RunAtLoad=true` on all plists: if Mac was off at trigger time, the task runs on next boot/login (stamp file prevents double-runs).
When Mac is **off**: use `vm/deploy.sh` to set up Oracle Cloud Always Free VM (see `vm/` directory).

## Available Skills

| Skill | Purpose |
|---|---|
| `/daily-agent` | Full outreach loop: inbox sync → queue → generate & send (2 cold + 3 warm/day) |
| `/daily-agent --dry-run` | Preview only — drafts saved, nothing sent, tracker not changed |
| `/scrape` | Scrape Station F jobs, add Pending rows, auto-enrich generic emails |
| `/find-contacts` | Find named decision-makers for every generic `contact@` email |
| `/speculative` | Evaluate 5 new Station F companies and add `[Suggested]` pitches |
| `/followup-check` | Midday inbox scan — classify replies, send alerts, read-only |
| `/status` | Dashboard: status counts, follow-ups due, recent activity, strategy stats |
| `/cv-builder` | Compile a role-adapted CV PDF from the LaTeX source |
| `/cv-builder COMPANY` | Same — auto-detects lang & focus from tracker row |
