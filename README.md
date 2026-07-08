# 🤖 Autonomous Alternance Outreach Agent

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white">
  <img alt="Playwright" src="https://img.shields.io/badge/Playwright-scraping-2EAD33?logo=playwright&logoColor=white">
  <img alt="Claude" src="https://img.shields.io/badge/Built%20with-Claude%20Code%20skills-D97757">
  <img alt="Deploy" src="https://img.shields.io/badge/Runs%20on-Google%20Cloud%20·%20cron-4285F4?logo=googlecloud&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-blue">
</p>

> A production AI agent that runs itself every weekday to find and win a work opportunity (CDI / CDD / alternance) — scraping seven French job boards, finding the right decision-maker, researching each company, writing a genuinely personalized email, sending it, following up, reading the replies, and learning which approaches work.

**This repository is a sanitized showcase of the engineering.** The live system runs privately on a Google Cloud VM; no real contacts, credentials, or personal documents are included here — only the code, templates, and architecture. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full technical map.

> 💡 The agent's signature move: every cold email it sends discloses, in a P.S., that it was *written and sent by this very agent* — so the outreach itself is a live demo of the sender's AI-engineering skills.

---

## What it does

Every weekday, on a cron schedule, the agent:

1. **Scrapes seven job boards** — Station F, Welcome to the Jungle, HelloWork, APEC, France Travail, Free-Work, and La Bonne Alternance (the state-run "hidden market" API) — and filters for AI / Backend / Data / MLOps roles in Île-de-France.
2. **Finds the decision-maker** — not a generic `contact@`. It resolves the company's real domain, crawls its team / Station F / job pages for a named CTO / Head of AI / recruiter (size-aware), then derives and **verifies** their email before using it.
3. **Researches each company** and writes a personalized email using one of **seven** named strategies — never a template. Every email cites one real, specific fact about the company; no fact → it skips the company.
4. **Picks the contract ask** from the posting (CDI / CDD / alternance), reframing full-time roles instead of listing a desperate menu.
5. **Sends** via Gmail SMTP under strict anti-spam caps, verifies the recipient first (anti-bounce), and **logs everything** to a tracker.
6. **Syncs the inbox** (IMAP), classifies replies, drafts a suggested response for Zineb to approve, alerts on serious ones, and auto-detects bounces.
7. **Follows up** automatically (up to 3 touches at escalating gaps), and **tracks reply rate per strategy** to improve over time.

It self-heals: an offline pre-flight self-test gates every run, and an external dead-man's switch emails an alert if the VM ever stops or a run fails.

---

## Architecture

```
              ┌──────────────── GitHub (single source of truth for code + state) ───────────────┐
              │                                                                                  │
 Dev machine ─┤  push code                                                    pull code          ├─ Google Cloud VM
              │                                                        push results (contacts.xlsx)│  (cron, Mon–Fri)
              └──────────────────────────────────────────────────────────────────────────────────┘
                                                    │
             cron → run_*.sh → git pull → preflight gate → `claude --print "<skill>"` → Python I/O → git push
```

Each daily job is an **autonomous Claude Code skill** (`.claude/commands/*.md`) that orchestrates small, single-purpose Python tools. **The skills do the reasoning; the Python does the deterministic I/O.** Adding a capability usually means writing a skill, not more glue code.

| Layer | Files |
|---|---|
| **Skills** (LLM reasoning) | `.claude/commands/` — `scrape`, `find-contacts`, `daily-agent`, `speculative`, `followup-check`, `status`, `dashboard`, `cv-builder`, `cover-letter`, `interview-prep`, `linkedin-draft`, `loom-script` |
| **Job sources** (pluggable) | `scraper.py` (orchestrator) + `jobsource.py`, `wttj.py`, `hellowork.py`, `apec.py`, `france_travail.py`, `free_work.py`, `labonnealternance.py`, `companies.py` |
| **Enrichment & sending** | `company_resolver.py`, `contact_finder.py`, `email_verify.py`, `email_lint.py`, `smtp_send.py`, `imap_fetch.py` |
| **Intelligence** | `tracker.py` (state + lead ranking + strategy bandit), `learning.py`, `lead_facts.py`, `warm_network.py`, `school_partners.py`, `ats_detect.py` |
| **Docs & CV** | `cv_builder.py`, `dashboard.py`, `config.py` |
| **Safety** | `preflight.py` (26-check self-test), `vm/preflight_gate.sh`, `vm/health_check.sh`, `vm/git_sync.sh` |
| **Deployment** | `vm/` — `deploy.sh`, `setup.sh`, `crontab.txt`, `run_*.sh` |

---

## Key engineering decisions

- **Decision-maker discovery, not `contact@`** — `contact_finder.py` extracts real people via JSON-LD `schema.org/Person`, LinkedIn anchor parsing, DOM card patterns, and heading/title pairs, with guards against false positives (job titles, pronouns, company descriptions). Email patterns are verified (Hunter API → MX → SMTP probe, with catch-all detection) before use, and a send to a dead domain or invalid mailbox is **refused** at send time.
- **Deliverability first** — a warm-up ramp on cold volume (3 → 5 → 7/day) so a fresh mailbox isn't flagged, no attachments on cold emails (a spam signal — a LinkedIn link goes in the body instead), recipient verified before every send, and a per-domain cooldown. The AI-disclosure footer appears only on first contact.
- **Evidence-gated learning** — a per-strategy epsilon-greedy bandit ranks by the **Wilson lower bound** (so a reliably-good strategy beats one that was merely lucky early), and `learning.py` only emits guidance once a bucket clears a minimum sample size — otherwise it stays in "explore" and changes nothing.
- **Human-in-the-loop replies** — when a person answers, the agent **drafts** a suggested reply and alerts Zineb; it never auto-sends a reply to a human.
- **Opportunity-aware contract ask** — the agent reads the posting and leads with the right ask (CDI / CDD / alternance), never a menu.
- **Self-testing** — `preflight.py` runs 26 offline checks before every cron job; if the system is unhealthy it alerts and skips the run rather than operating broken.

---

## Tracker schema

State lives in a single Excel file (`contacts.xlsx`, gitignored — see `contacts.example.xlsx`). `tracker.py` is the only reader/writer and guards the schema:

| Company | Role | Contact Email | Conversation Log | Last Interaction Date | Status |
|---|---|---|---|---|---|

`Status ∈ {Pending, Emailed, Replied, Followed Up, Rejected, Interview Scheduled}`.

---

## Repository layout

```
.
├── .claude/commands/      # the autonomous skills (the "brain" of each daily job)
├── vm/                    # Google Cloud deployment: setup, cron, run scripts, health checks, git sync
├── documents/             # LaTeX CV templates (cv_builder.py compiles role-adapted PDFs)
├── *.py                   # deterministic I/O + intelligence tools
├── preflight.py           # 26-check self-test gating every run
├── ARCHITECTURE.md        # full technical map (start here)
├── contacts.example.xlsx  # sample tracker (the real one is private)
└── drafts/                # one illustrative sample email
```

---

## Running it yourself

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env          # then fill in your Gmail App Password etc.
python preflight.py           # verify the install (26 checks)
```

Deploy to a Google Cloud (or any Ubuntu) VM with `bash vm/setup.sh` — it installs dependencies, Playwright, the Claude Code CLI, and the cron schedule. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for how the pieces fit together.

---

## Tech stack

Python · Playwright · Claude Code skills · Gmail SMTP/IMAP · LaTeX/tectonic · pandas/openpyxl · Google Cloud · cron

---

## A note on data & ethics

This public repo contains **no real contact data**. The live agent stores third-party contact
information privately and sends a low volume of genuinely personalized, relevant messages — not bulk
mail. Every sample file uses fictional companies and people.

## License

[MIT](LICENSE) © Zineb Meftah
