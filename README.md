# 🤖 Autonomous Alternance Outreach Agent

> A production AI agent that runs itself every weekday to find and win a work-study / job opportunity — scraping the [Station F](https://jobs.stationf.co) job board, finding the right decision-maker, writing a genuinely personalized email, sending it, following up, and learning which approaches work.

**This repository is a sanitized showcase of the engineering.** The live system runs privately on an Oracle Cloud VM; no real contacts, credentials, or personal documents are included here — only the code, templates, and architecture.

> 💡 The agent's signature move: every cold email it sends discloses, in a P.S., that it was *written and sent by this very agent* — making the outreach itself a live demo of the sender's AI engineering skills.

---

## What it does

Every weekday, on a cron schedule, the agent:

1. **Scrapes** the Station F job board (Playwright/headless Chromium) and filters for AI / Backend / Data / MLOps roles.
2. **Finds the decision-maker** — not a generic `contact@`. It scrapes the company's team page, Station F profile, and LinkedIn links to find a named CTO / Head of AI / recruiter (size-aware), then derives and SMTP-verifies their email.
3. **Researches each company** and writes a personalized email using one of six strategies — never a template.
4. **Picks the contract ask** based on the posting (CDI / CDD / alternance), with an opportunity-aware reframe for full-time roles.
5. **Sends** via Gmail SMTP, respecting strict anti-spam caps, and **logs everything** to a tracker.
6. **Syncs the inbox** (IMAP), classifies replies, alerts on serious ones, and detects bounces.
7. **Follows up** automatically after 4 business days, and **tracks reply rate per strategy** to improve over time.

It self-heals: a pre-flight self-test gates every run, and a health check emails an alert if anything breaks.

---

## Architecture

```
                    ┌──────────────── GitHub (single source of truth) ────────────────┐
                    │                                                                  │
   Dev machine ─────┤  push code                                        pull code      ├───── Oracle Cloud VM
                    │                                              push results (state) │      (cron, always-free)
                    └──────────────────────────────────────────────────────────────────┘
                                                     │
                       cron → run_*.sh → preflight gate → `claude --print "<skill>"` → Python I/O
```

Each daily job is an **autonomous Claude Code skill** (`.claude/commands/*.md`) that orchestrates small, single-purpose Python tools. The skills do the reasoning; the Python does the deterministic I/O.

| Layer | Files |
|---|---|
| **Skills** (LLM reasoning) | `.claude/commands/` — `scrape`, `find-contacts`, `daily-agent`, `cv-builder`, `followup-check`, `speculative`, `status` |
| **I/O tools** (deterministic) | `scraper.py`, `contact_finder.py`, `companies.py`, `email_verify.py`, `imap_fetch.py`, `smtp_send.py`, `cv_builder.py`, `tracker.py`, `config.py` |
| **Safety** | `preflight.py` (16-check self-test), `vm/preflight_gate.sh`, `vm/health_check.sh` |
| **Deployment** | `vm/` — `deploy.sh`, `setup.sh`, `crontab.txt`, `run_*.sh` |

---

## Key engineering decisions

- **Decision-maker discovery, not `contact@`** — `contact_finder.py` extracts real people via JSON-LD `schema.org/Person`, LinkedIn anchor parsing, DOM card patterns, and heading/title pairs, with guards against false positives (job titles, pronouns, company descriptions). Email patterns are SMTP-verified before use.
- **Deliverability first** — hard caps of **2 cold + 3 warm emails/day**, no attachments on cold emails (a spam signal — a LinkedIn link goes in the body instead), 90 s spacing between sends, and the AI-disclosure footer only on first contact.
- **Opportunity-aware contract ask** — `config.guess_contract_type()` reads the posting and the agent leads with the right ask (CDI / CDD / alternance), never a desperate menu.
- **Per-role CV** — `cv_builder.py` adapts a LaTeX CV to the role focus (AI / backend / MLOps / data / fullstack) and compiles it with `tectonic` before each follow-up.
- **Self-testing** — `preflight.py` runs 16 offline checks before every cron job; if the system is unhealthy it alerts and skips the run rather than operating broken.
- **Strategy analytics** — every send is tagged, and reply rate per strategy is tracked to learn what works.

---

## Tracker schema

State lives in a single Excel file (`contacts.xlsx`, gitignored — see `contacts.example.xlsx`):

| Company | Role | Contact Email | Conversation Log | Last Interaction Date | Status |
|---|---|---|---|---|---|

`Status ∈ {Pending, Emailed, Replied, Followed Up, Rejected, Interview Scheduled}`.

---

## Repository layout

```
.
├── .claude/commands/      # the autonomous skills (the "brain" of each daily job)
├── vm/                    # Oracle Cloud deployment: setup, cron, run scripts, health checks
├── documents/             # LaTeX CV templates (cv_builder.py compiles role-adapted PDFs)
├── *.py                   # deterministic I/O tools
├── preflight.py           # 16-check self-test gating every run
├── contacts.example.xlsx  # sample tracker (the real one is private)
├── drafts/2026-01-15/     # one illustrative sample email
├── CLAUDE.md              # operating manual / single source of truth for constants
└── instructions.txt       # agent protocol & philosophy
```

---

## Running it yourself

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env          # then fill in your Gmail App Password etc.
python preflight.py           # verify the install (16 checks)
```

Deploy to an always-free Oracle Cloud VM with `bash vm/deploy.sh ubuntu@<VM_IP>` (installs deps, Playwright, the Claude Code CLI, and the crontab). See `CLAUDE.md` for the full operating manual.

---

## Tech stack

Python · Playwright · Claude Code skills · Gmail SMTP/IMAP · LaTeX/tectonic · pandas/openpyxl · Oracle Cloud · cron

---

## A note on data & ethics

This public repo contains **no real contact data**. The live agent stores third-party contact
information privately and sends a low volume of genuinely personalized, relevant messages — not bulk
mail. Sample files use fictional companies and people.

## License

[MIT](LICENSE) © Zineb Meftah
