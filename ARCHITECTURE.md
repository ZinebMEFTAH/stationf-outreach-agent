# Architecture — stationf_agent

How the autonomous outreach agent works, end to end. This is the "how it's built" map;
`CLAUDE.md` holds the hard constants/schema/commands, `instructions.txt` the strategy/why,
and `OPERATIONS.md` (private) the deployment + recovery runbook.

---

## 1. What it is

An autonomous daily agent that secures a work contract (CDI > CDD > Alternance, M1 2026‑2027)
for Zineb Meftah. Every weekday it: scrapes job boards → finds the right decision‑maker →
writes a hyper‑personalized email for that specific company → sends it → follows up →
reads replies → alerts Zineb. It runs unattended on a cloud VM and keeps all state in git.

**Design principle — skill‑orchestrated:** the reasoning lives in Claude Code **skills**
(`.claude/commands/*.md`); the Python modules are raw I/O the skills call. This is both the
architecture and the product story ("orchestrated with Claude Code skills").

---

## 2. The daily pipeline

```
                          ┌──────────────── git pull (latest code) ────────────────┐
                          ▼                                                          │
  06:00  /scrape        → 6 sources → matched AI/Backend/Data roles → Pending rows  │
  06:30  /find-contacts → generic contact@ rows → named decision‑maker + verified   │
  07:00  /daily-agent   → inbox sync → priority queue → send ≤5 (2 cold + 3 warm)   │
  12:00  /followup-check→ midday inbox scan, alert on serious replies (no sends)    │
  19:00  /speculative   → 5 new Station F companies → proactive [Suggested] pitches │
                          │                                                          │
                          └──────────── git commit + push (state back) ─────────────┘
```

Every stage is a cron‑invoked skill. State (`contacts.xlsx`) is the single source of truth,
committed to git after each run so the pipeline survives restarts.

---

## 3. Data model — `contacts.xlsx` (strict 6 columns)

| Column | Meaning |
|---|---|
| Company | Startup name |
| Role | Scraped title, or `[Suggested] Role` for speculative pitches |
| Contact Email | `"Name (Title)" <addr>` when enriched; plain `contact@domain` otherwise |
| Conversation Log | Append‑only history: `[date] Agent: subject` / `[date] Contact: body` |
| Last Interaction Date | YYYY‑MM‑DD of last send/receive |
| Status | Pending → Emailed → Followed Up → Replied → Interview Scheduled / Rejected |

Never add/rename/reorder columns. `tracker.py` is the only reader/writer (`load`/`save`,
schema‑guarded). The file is **binary**; the VM owns it (see §10).

---

## 4. Module map (raw I/O — called by skills)

| Module | Responsibility |
|---|---|
| `tracker.py` | Read/write `contacts.xlsx`; funnel, lead ranking, strategy stats, enrichment stats, cooldown |
| `jobsource.py` | Shared source core: `JobListing`, role keywords, email/domain/slug helpers, cookie banner |
| `scraper.py` | **Multi‑source orchestrator** — registry of sources, enrich loop, persist to tracker |
| `wttj.py` `hellowork.py` `apec.py` `france_travail.py` `free_work.py` | Per‑board discovery modules (see §5) |
| `companies.py` | Scrape the full Station F company directory (for `/speculative`) |
| `company_resolver.py` | Company **name → real email domain** (Clearbit autocomplete, high‑precision) |
| `contact_finder.py` | Find a named decision‑maker (Playwright crawl) + derive/verify their email |
| `email_verify.py` | Email reachability: Hunter API → MX → SMTP probe **with catch‑all detection**; pattern builder |
| `email_lint.py` | Executable quality gate for every draft (hard errors + content warnings) |
| `smtp_send.py` | Send one email, append the footer/signature, log to tracker, count caps |
| `imap_fetch.py` | Sync Gmail inbox → update tracker; classify replies; **bounce → Rejected** |
| `cv_builder.py` | Compile a role‑adapted CV PDF from LaTeX source |
| `config.py` | Caps, footers, secrets from `.env` |
| `preflight.py` | Offline health check — 24 checks; gates every cron run |

---

## 5. Job sources (pluggable)

`scraper.py` holds a `SOURCES` registry; each board is a small module exposing
`discover(page, max_pages)` and `resolve_company_site(page, listing)`. Add a board = write a
module + register it. `--source stationf|wttj|hellowork|apec|francetravail|freework|all`.

| Source | How data is fetched | Enrich inline? |
|---|---|---|
| **Station F** | Playwright scrape of the board + company profile | ✅ named contact inline |
| **Welcome to the Jungle** | public Algolia jobs API (no key) | discovery‑only |
| **HelloWork** | server‑rendered search DOM | discovery‑only |
| **APEC** | public JSON API (`/rechercheOffre`) | discovery‑only |
| **France Travail** | official OAuth2 API — **inert until `FRANCE_TRAVAIL_ID/SECRET` set** | discovery‑only |
| **Free‑Work** | public JSON API; freelance filtered out (CDI/alternance) | discovery‑only |

"Discovery‑only" sources hide the employer's domain, so their rows land with a generic
`contact@…` and are upgraded later by `/find-contacts` (§6). Each source filters titles with
the shared role keywords and emits source‑neutral `JobListing`s.

---

## 6. Enrichment chain (generic inbox → named, verified contact)

```
company name ──► company_resolver.resolve_domain()  ──► real domain (high precision; else None)
                          │
                          ▼
        contact_finder: Pass 1 crawl (Station F profile / team / job page) ──► named person
                          │  (SPA team pages often yield nothing → Pass 2)
                          ▼
        /find-contacts Pass 2 (skill): web‑search the size‑appropriate decision‑maker
                          │   small→CTO/founder · mid→eng lead · large→recruiter
                          ▼
        contact_finder --person  ──► build_patterns() ──► email_verify.verify()
                          ▼
        "First Last (Title)" <verified@domain>  written to tracker  (+ LinkedIn + ⚠ if guessed)
```

- **`company_resolver`** favours precision: only a confident match (domain root == slug, or
  exact name on a non‑foreign TLD) — a wrong‑but‑live domain is worse than none.
- **`build_patterns`** orders by French‑company frequency (`prenom.nom` first).
- **`email_verify`** grades confidence: `api_valid`/`smtp_ok` (confirmed) vs
  `smtp_catchall`/`mx_only`/`api_risky` (deliverable but a guess — logged with ⚠).
- A send to a dead domain / invalid mailbox is **refused** at send time (anti‑bounce gate).

---

## 7. Email content & quality

- **Research first** (`/daily-agent` 4b): every email cites one real, specific fact about the
  company (WebSearch + WebFetch). No fact → skip the company.
- **Strategy bandit:** 7 named strategies (Q/O/V/M/U/A/G); `tracker.recommend_strategy_order`
  is an epsilon‑greedy bandit. Explore phase samples under‑used strategies; exploit phase ranks
  by the **Wilson lower bound** (confidence‑adjusted rate) so a reliably‑good strategy beats one
  that was merely lucky early (a 1/1 ranks below a 6/10).
- **Opening‑line library** by company type (AI/dev‑tools/data/fintech/healthtech/early/scale‑up)
  — shapes to adapt, never copy.
- **`email_lint` hard gate** (must pass before any send): banned openers/subjects, word limits,
  LinkedIn on cold, no footer/signature in the draft, finance folded into a clause; **warnings**
  for clichés, first‑line‑about‑Zineb, missing CTA, "je" overuse.
- **Footer:** `smtp_send` auto‑appends the AI‑agent P.S. disclosure on **cold** emails only;
  follow‑ups get signature only; replies are sent **manually by Zineb**; alerts are raw.

---

## 8. Sending, caps & reply policy

- **Caps** (`config.py`): COLD_CAP=2, WARM_CAP=3 (follow‑ups), DAILY_CAP=5; FOLLOWUP_DAYS=4.
- **Lead ranking** (`tracker.rank_pending_leads`) spends the 2 scarce cold slots on the best
  targets — transparent 0–100 score: role fit + contract match + **deliverability tier**
  (confirmed named 25 > guessed named 16 > generic 8) + speculative bonus, with a **modest ESN/
  staffing down‑rank** (bodyshops below genuine product startups) and the cooldown penalty.
- **Anti‑spam:** no attachment on cold (LinkedIn inline), recipient verified before every send,
  over‑contact cooldown (don't email the same domain twice in 7 days).
- **Human replies are NOTIFY‑ONLY.** When a person replies, the agent sends Zineb an alert and
  **never auto‑answers** — she replies herself. `Replied` rows are excluded from the send queue.
- **Bounces** (`imap_fetch`) auto‑mark the row `Rejected`.

---

## 9. Skills (the orchestration layer)

| Skill | Purpose |
|---|---|
| `/scrape` | Run the 6 sources, add Pending rows, auto‑enrich generic emails |
| `/find-contacts` | Upgrade generic `contact@` rows to named, verified decision‑makers |
| `/daily-agent` | Full loop: inbox → queue → send (2 cold + 3 warm) |
| `/followup-check` | Midday inbox scan; alert on serious replies; read‑only |
| `/speculative` | Evaluate new Station F companies → `[Suggested]` proactive pitches |
| `/status` | Dashboard: funnel, enrichment coverage, follow‑ups due, strategy stats (+confidence) |
| `/cv-builder` | Compile a role‑adapted CV PDF |

---

## 10. Scheduling, deployment & state sync

- Runs on a **cloud VM** (Google Cloud Compute Engine; see `OPERATIONS.md`). The plists in
  `mac/` are an alternative launchd runner for when the Mac is on.
- **VM cron** (UTC, Mon–Fri) fires `vm/run_*.sh` → each runs `preflight` then `claude --print`
  on the matching skill.
- **State sync:** every run script does `git fetch + git merge -X ours origin/main` (pull
  latest code, **keep the VM's data files on conflict**, never drift to a detached HEAD), runs,
  then `git commit + push`. `contacts.xlsx` is marked `binary merge=ours` in `.gitattributes`.
- **The VM is the source of truth for `contacts.xlsx`** — avoid committing it from elsewhere.
- Health signal: a fresh `Zineb Outreach Agent` commit on `main` each weekday.

---

## 11. Quality gate — `preflight.py`

24 offline checks run before every cron job (and abort + alert on failure): module imports,
config caps/footers, contract/language detection, contact‑finder guards, **company resolver**,
**email patterns**, **sources registry**, **enrichment stats**, verification gate, tracker
schema/helpers, strategy bandit, linter, lead ranking, funnel/cooldown, smtp logic, CV sources.

---

## 12. Two‑repo workflow

Private repo (`stationf-agent`) = the live system. Public repo (`stationf-outreach-agent`) =
sanitized showcase. **Every push updates both:** `git push` then `bash sync_public.sh`
(allowlist copy + personal‑data scrub + secret‑scan gate; aborts if anything sensitive leaks).
`contacts.xlsx`, `drafts/`, CV PDFs, `.env`, and `OPERATIONS.md` never reach the public mirror.
