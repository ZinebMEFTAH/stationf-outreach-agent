Draft short LinkedIn connection notes for Zineb to send manually — a spam-immune second channel alongside cold email. Nothing is sent; this is draft-and-approve only.
Working directory: /path/to/stationf-agent

LinkedIn is a second touch on the SAME decision-makers the agent emails. A connection note can't
land in a spam folder and gets read far more often than cold email — but it must be sent by a human
(LinkedIn bans automation). So this skill only **drafts**: Zineb opens each draft, finds the person
on LinkedIn, and sends it herself.

Read CLAUDE.md and about_me.txt before drafting.
All python commands must use the venv: prefix every `python ...` call with
`source /path/to/stationf-agent/venv/bin/activate && `.

---

## STEP 1 — PICK THE TARGETS (up to 5)

LinkedIn works best as reinforcement of an email already sent to a **named** person who hasn't
replied. Get the candidates:

```bash
source /path/to/stationf-agent/venv/bin/activate && \
python -c "
import tracker, json
df = tracker.load()
# Emailed / Followed Up, not yet replied, with a NAMED contact (has a display name, not contact@)
m = df['Status'].isin(['Emailed','Followed Up'])
rows = []
for _,r in df[m].iterrows():
    email = str(r['Contact Email'])
    named = ('<' in email) or (email and not email.split('@')[0].lower() in ('contact','hello','jobs','hr','rh','recrutement','career','careers','info'))
    # skip anyone the daily double-tap already drafted a note for (no duplicate notes)
    if named and not tracker.has_linkedin_touch(r['Company'], r['Role'], email):
        rows.append({'Company': r['Company'], 'Role': r['Role'], 'Contact': email, 'Status': r['Status']})
print(json.dumps(rows[:12], ensure_ascii=False, indent=2, default=str))
"
```

Pick up to **5** with the clearest named person and best fit. If none qualify (all generic
inboxes), fall back to the top of `tracker.rank_pending_leads(limit=8)` — draft a note for the
company and leave the person as `[find the AI/eng lead or founder on LinkedIn]`.

---

## STEP 2 — RESEARCH THE HOOK (reuse, don't re-derive)

For each target, check the hook-fact cache first — it's the same person you already researched:
```bash
source /path/to/stationf-agent/venv/bin/activate && \
python -c "import lead_facts, json; r=lead_facts.get('COMPANY'); print(json.dumps(r, ensure_ascii=False) if r else '')"
```
Use that fact as the note's angle. If empty, do one quick WebSearch for a concrete detail.

---

## STEP 3 — WRITE THE NOTE

**Hard limit: 300 characters** (LinkedIn's connection-note cap). Under 250 is better.

Rules (same spirit as cold emails, tighter):
- Open on THEM (the researched hook), not on Zineb.
- One concrete proof point max (GE HealthCare + 1ère/126) — woven in, not listed.
- **No cost/AUA, no CV, no links** (LinkedIn shows her profile already).
- Warm, human, specific. It should read like a peer reaching out, not a mass request.
- End with a soft reason to connect, not a hard ask ("j'aimerais échanger sur [topic]").
- French by default (match the company's language).

**Good example (Craft AI):**
> Bonjour [Prénom], l'observabilité des agents IA en prod est exactement le mur sur lequel je
> bosse chez GE HealthCare (pipeline multi-agent). Major de promo L3 IA, je vise une alternance
> 2026 sur ce type de système — j'aimerais beaucoup échanger avec vous là-dessus.

(≈270 chars — count before saving.)

---

## STEP 4 — SAVE THE DRAFT (never send)

One file per target:
```bash
mkdir -p drafts/$(date +%Y-%m-%d)
```
**Capture a direct LinkedIn link** for each person (so the alert is one-click). Use the exact
profile URL if your STEP 2 research surfaced it; otherwise build a pre-filled people-search URL:
```bash
python -c "import urllib.parse; print('https://www.linkedin.com/search/results/people/?keywords='+urllib.parse.quote('PERSON_NAME COMPANY'))"
```

Write `drafts/YYYY-MM-DD/linkedin-<company-slug>.txt` containing:
```
COMPANY:  <name>
PERSON:   <name + title, or "find the AI/eng lead / founder">
ROLE:     <role>
LINKEDIN: <exact profile URL, or the people-search URL above>
CHARS:    <character count of the note>
---
<the note>
```

Then record the note as drafted (off-book — this only appends a `Agent (LinkedIn):` line to the
Conversation Log; it never counts against COLD/WARM caps and never resets `Last Interaction Date`,
so the email follow-up timer is untouched):
```bash
python -c "import tracker; print(tracker.note_linkedin_draft('COMPANY','ROLE','CONTACT_EMAIL'))"
```
This is the same marker the daily double-tap writes, so the two channels never draft a duplicate
note for the same person. Do **not** otherwise modify `contacts.xlsx` (no status/date changes).

---

## STEP 5 — ALERT ZINEB WITH THE BATCH

Send one internal summary so she knows drafts are ready to send by hand:
```bash
source /path/to/stationf-agent/venv/bin/activate && \
python smtp_send.py \
  --to you@example.com \
  --subject "[LINKEDIN] N notes ready to send manually — $(date +%Y-%m-%d)" \
  --kind alert \
  --body "N LinkedIn connection notes drafted. For each: click the link, send a connection request,
and paste the note below it.

═══════════════════════════════════
COMPANY — Person (Title)
🔗 <exact profile URL, or the people-search URL>
✉️ <the FULL connection note, verbatim, ready to paste>
═══════════════════════════════════
<...repeat one block per target...>

These are NOT sent automatically (LinkedIn forbids it). Send the ones you like, skip the rest.
Files: drafts/$(date +%Y-%m-%d)/" \
  --send
```

Done. Report to the console: how many drafted, for which companies, and where the files are.
