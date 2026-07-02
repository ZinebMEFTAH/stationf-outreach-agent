# stationf_agent — Zineb's Alternance Outreach Agent

Autonomous daily agent to secure a work contract for Zineb Meftah — **CDI, CDD, or
Alternance** (M1 2026-2027), in that order of preference. The agent leads with whatever
fits each posting (see contract-ask logic in `/daily-agent`); it never lists all three.
Full profile: `about_me.txt` | Full protocol: `instructions.txt`
How it all works: `ARCHITECTURE.md` | Deployment + recovery (private): `OPERATIONS.md`

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
Follow-ups carry the signature but no footer (redundant); replies (the agent **drafts** a
suggestion but **Zineb sends** them manually) carry signature only (the conversation is human
now); alerts are sent raw. English bodies get `FOOTER_EN`, French get `FOOTER_FR`.

> P.S. Ce message a été entièrement rédigé et envoyé de façon autonome par un agent IA que j'ai conçu et déployé en production : scraping Playwright du board Station F, qualification des opportunités par LLM, personnalisation du message selon le profil de chaque entreprise, envoi SMTP et relances automatiques — le tout orchestré avec des skills Claude Code. C'est précisément ce type de pipeline IA bout-en-bout que je veux contribuer à construire avec vous.

## Constants
- **COLD_CAP** = 2 new first-contact emails per calendar day
- **WARM_CAP** = 3 follow-ups per calendar day (human replies are **draft-and-approve** — when someone answers, the agent drafts a suggested reply and alerts Zineb with it, but never auto-sends; she reviews and sends it herself)  
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

# Scrape job boards (Station F + WTTJ + HelloWork + APEC + France Travail + La Bonne Alternance) → Pending rows
#   Multi-source orchestrator. Shared logic in jobsource.py; each board is a pluggable
#   module (Station F in scraper.py; WTTJ in wttj.py via its public Algolia API; HelloWork
#   in hellowork.py via server-rendered search; APEC in apec.py via its public JSON API;
#   France Travail in france_travail.py via its official OAuth2 API — needs FRANCE_TRAVAIL_ID
#   / FRANCE_TRAVAIL_SECRET in .env, else inert; Free-Work in free_work.py via its public
#   JSON API, CDI/alternance only; La Bonne Alternance in labonnealternance.py via the
#   state-run api.apprentissage.beta.gouv.fr "hidden market" API — needs LBA_API_KEY in .env,
#   else inert; surfaces software/data alternance postings AND algorithm-flagged recruiters
#   that haven't posted (added as [Suggested] pitches), filtered by métier + Île-de-France).
#   Station F rows are enriched inline with a named contact; the others are discovery-only
#   (real domain recovered later by company_resolver / /find-contacts — except La Bonne
#   Alternance, which ships the company website directly).
python scraper.py [--source stationf|wttj|hellowork|apec|francetravail|freework|labonnealternance|all] [--dry-run] [--max-pages N]

# Scrape full Station F company directory → cache/stationf_companies.json
python companies.py [--refresh]

# Pre-flight self-test — fast offline health check (run before relying on the system)
python preflight.py            # exit 0 = healthy, 1 = broken (lists failures)
# Cron run scripts call this automatically and SKIP the run + alert if it fails.

# Recipient verification (anti-bounce) — smtp_send.py auto-verifies before every real send
#   Uses Hunter.io if HUNTER_API_KEY is set (works on the VM where port 25 is blocked),
#   else MX + SMTP probe. A send to a dead domain / invalid mailbox is REFUSED, not sent.
python email_verify.py ADDR           # manual check: api_valid|smtp_ok|mx_only|unverifiable

# Email quality linter — MUST pass (exit 0) before any send (daily-agent gates on it)
python email_lint.py --kind cold|followup|reply --subject "TEXT" --company "NAME" --body-file PATH

# Lead prioritization & self-improving strategy (used by /daily-agent)
python -c "import tracker, json; print(json.dumps(tracker.rank_pending_leads(limit=8), default=str, indent=2))"
python -c "import tracker, json; print(json.dumps(tracker.recommend_strategy_order(), indent=2))"

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
| **00:00** | `/scrape` | Scrape new jobs (Station F + WTTJ + HelloWork + APEC + La Bonne Alternance), refill Pending leads for the day |
| **04:00** | `/followup-check` | Early inbox scan — catch overnight replies, alert if serious, no sends |
| **09:00** | `/daily-agent` | Inbox sync → priority queue → send up to 5 emails (2 cold + 3 warm) |
| **14:00** | `/speculative` | Evaluate 5 new Station F companies for proactive pitches |
| **19:00** | `/find-contacts --all` | Enrich remaining generic contact@ emails (up to 8/day), for tomorrow |

**Jobs are spaced 5 hours apart on purpose.** The VM authenticates `claude` with a Claude
*subscription* token (`CLAUDE_CODE_OAUTH_TOKEN`), whose usage limit resets on a rolling 5-hour
window. One heavy run per window → each starts with a fresh allowance (bunching them exhausts
the limit and later runs fail). Schedule lives in `vm/crontab.txt` (UTC).

The **VM crontab is the sole live runner** (this Mac is dev-only — no launchd loaded). Deployment
+ recovery details in `OPERATIONS.md`; the cloud VM is Google Cloud Compute Engine (`vm/`).

## Available Skills

| Skill | Purpose |
|---|---|
| `/daily-agent` | Full outreach loop: inbox sync → queue → generate & send (2 cold + 3 warm/day) |
| `/daily-agent --dry-run` | Preview only — drafts saved, nothing sent, tracker not changed |
| `/scrape` | Scrape Station F + WTTJ + HelloWork + APEC + La Bonne Alternance jobs, add Pending rows, auto-enrich generic emails |
| `/find-contacts` | Find named decision-makers for every generic `contact@` email |
| `/speculative` | Evaluate 5 new Station F companies and add `[Suggested]` pitches |
| `/followup-check` | Midday inbox scan — classify replies, send alerts, read-only |
| `/status` | Dashboard: status counts, follow-ups due, recent activity, strategy stats |
| `/cv-builder` | Compile a role-adapted CV PDF from the LaTeX source |
| `/cv-builder COMPANY` | Same — auto-detects lang & focus from tracker row |
