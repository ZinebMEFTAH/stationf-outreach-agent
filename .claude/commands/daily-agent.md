Execute the full daily outreach protocol for Zineb Meftah's alternance search.
Working directory: /path/to/stationf-agent

**Dry-run mode**: if $ARGUMENTS contains `--dry-run` or `dry-run`, omit `--send` from every smtp_send.py call. Drafts are saved to disk but nothing is transmitted and the tracker is not mutated.

Read CLAUDE.md, about_me.txt, and instructions.txt before doing anything else.
All python commands must use the venv: prefix every `python ...` call with `source /path/to/stationf-agent/venv/bin/activate && `.

---

## STEP 0 — BACKUP

Before any outbound action (skip in dry-run):
```bash
source /path/to/stationf-agent/venv/bin/activate && \
  python -c "import tracker; p = tracker.backup(); print('[backup]', p)"
```

---

## STEP 1 — INBOX SYNC (always first, no exceptions)

```bash
cd /path/to/stationf-agent && source venv/bin/activate && python imap_fetch.py --since-days 7
```

Then pick up anything Zineb asked for by replying to her opportunity digest. `imap_fetch` only
matches mail from existing leads, so a reply from her own address matches nothing and is dropped —
this is what keeps the digest's "reply with any you want the outreach agent to chase" promise:

```bash
python digest_reply.py --apply --since-days 3
```

Each offer she left a link to becomes a Pending lead, so it enters today's queue below and is
qualified, linted and verified like any other — she asked for it explicitly, which is why this one
applies rather than drafting. Report what it added in the end-of-run summary. Anything it skipped
(usually an unresolvable domain) is worth mentioning to her: those are companies she wants chased
and the agent cannot reach.

> **THE AGENT NEVER AUTO-SENDS A REPLY.** Once a real person answers, the reply is Zineb's to
> send — she reviews and sends every one herself. But the agent now **drafts a suggested reply
> for her to approve**: it writes the draft to `drafts/`, includes it in the alert, and stops
> there. It NEVER calls `smtp_send.py --kind reply` itself, and never auto-answers. Replied rows
> are excluded from the send queue (they don't count against any cap).

Read every line of output carefully. For each reply printed, classify it yourself:

**Genuine human reply** = anything a real person wrote: interview request, call/meeting ask,
availability question, contract/start-date discussion, technical questions about Zineb,
internal forwarding to another person, or even a short/ambiguous human note.
**Automated / dead-end** = auto-reply, out-of-office, delivery bounce, unsubscribe notice.

For every **genuine human reply**, do two things — **draft** a suggested reply, then **alert**
Zineb with it. **Never send the reply yourself; never take any other action on the contact.**

**(a) Draft a suggested reply** (a suggestion for Zineb to approve, edit, and send manually):
- Match the reply's language (FR/EN) and answer what they actually said — propose availability
  for an interview (offer Google Meet + say she'll make time around her GE HealthCare internship),
  answer the technical question, thank + accept a referral, etc.
- Warm, concise, in Zineb's voice. **≤80 words.** No re-pitch of credentials, no footer, no
  signature (she adds her own sign-off, or `smtp_send` would when she sends it).
- Save it to `drafts/YYYY-MM-DD/NN-reply-COMPANY_SLUG.txt`, then lint (hard gate):
  ```bash
  python email_lint.py --kind reply --subject "Re: ORIGINAL_SUBJECT" \
    --company "COMPANY" --body-file drafts/YYYY-MM-DD/NN-reply-COMPANY_SLUG.txt
  ```
  Revise until it exits 0. This draft is a **suggestion only — it is NOT sent by the agent.**

**(b) Send Zineb the alert, with the suggested reply included** so she can approve/edit from her phone:
```bash
python smtp_send.py \
  --to you@example.com \
  --subject "[ALERT · CATEGORY] COMPANY — CONTACT_NAME_OR_EMAIL" \
  --kind alert \
  --body "Human reply — for Zineb to send (agent drafted a suggestion, did NOT respond).

Company: ...
Contact: ...
Category: interview_request | technical_questions | contract_discussion | internal_introduction | other
Summary: one sentence
Suggested action: what Zineb should do next

Reply (verbatim):
...

--- SUGGESTED REPLY (draft — review, edit, and send yourself) ---
saved to: drafts/YYYY-MM-DD/NN-reply-COMPANY_SLUG.txt
<paste the drafted reply text here>" \
  --send
```
(`--kind alert` is mandatory for alerts: it sends raw — no P.S. footer — and keeps the alert out of the tracker and the daily send caps. Without it, the alert would consume a cold-send slot. The suggested-reply draft is NOT sent and NOT counted — only Zineb sends it.)

---

## STEP 2 — READ TRACKER & COUNT REMAINING CAP

```bash
cd /path/to/stationf-agent && python -c "import tracker, json; df=tracker.load(); print(df.to_json(orient='records', date_format='iso', indent=2))"
```

Get today's authoritative send counts (written by smtp_send.py at each real send):
```bash
python -c "import tracker; print(tracker.today_send_counts())"
```

This prints `{'cold': N, 'warm': N}`. Get today's **effective** cold cap (the warm-up ramp lowers
it for a new mailbox — week 1 → 3/day, week 2 → 5/day, week 3+ → 7/day — so a fresh sender isn't
flagged as spam):
```bash
python -c "import config; print(config.effective_cold_cap())"
```
Compute:
- `cold_remaining = effective_cold_cap() - cold`   ← COLD_CAP ceiling = 7, ramped down early on
- `warm_remaining = 3 - warm`   ← WARM_CAP = 3 (follow-ups + replies)

If both caps are 0: print a summary and stop.

---

## STEP 3 — BUILD PRIORITY QUEUE

Collect candidates in two separate pools:

**WARM pool** (counts against `warm_remaining = 3`, WARM_CAP=3) — **FOLLOW-UPS ONLY**:
- Status == `Replied` rows are **NOT** in this pool. A human answered → it was already notified
  to Zineb in Step 1 and is hers to handle. The agent never auto-answers a reply.
- **Follow-ups are a multi-touch sequence** (up to 3 per lead, escalating gaps: 4 → 6 → 8 business
  days). Get the leads due for their next touch:
  ```bash
  python -c "import tracker, json; print(json.dumps(tracker.overdue_followups(), default=str, indent=2))"
  ```
  Each entry carries `followup_number` (1 = first follow-up, 2 = second, 3 = third/last) and
  `biz_days_waiting`. Most overdue first. A lead drops out of the pool automatically once it has had
  3 follow-ups or someone replies. **Match the follow-up type to the number** (see the follow-up
  section: FU1 = new angle/signal, FU2 = new result/proof, FU3 = short, graceful last nudge).
- **Before adding any follow-up**, verify the email is still reachable:
  ```bash
  python /path/to/stationf-agent/email_verify.py "CONTACT_EMAIL"
  ```
  - Exit 1 (`unverifiable`) → mark `Rejected`, skip.
  - Exit 0 → include normally.

**COLD pool** (counts against `cold_remaining`, from the ramped `effective_cold_cap()` — up to 7):
- **P3**: Status == `Pending` (first-contact emails). If fewer than 12 Pending rows exist, run `python scraper.py` to replenish first (7 cold/day burns the pool fast — keep it well stocked).
- **7 cold slots/day — spend them on the BEST leads, not the first ones.** Get the ranked shortlist:
  ```bash
  python -c "import tracker, json; print(json.dumps(tracker.rank_pending_leads(limit=15), indent=2, default=str))"
  ```
  This scores every Pending row by role-fit + contract match + deliverability (named contact) + speculative bonus. **Alternance-intent is now the decisive lever**: an explicit alternance posting (`★` in `reasons`) far outranks a generic CDI reframe — spend the scarce daily slots on companies that have *already decided they want an alternant* and on the pre-qualified hidden-market `[Suggested]` leads at the top of the list.
  Two flags on each lead change how you treat it:
  - `on_cooldown == true` (its company's domain was already emailed in the last 7 days) → **do NOT cold-email it**, pick the next. Prevents hitting the same company twice in a week when it has several open roles.
  - `likely_big_corp == true` (large employer — BNP, L'Oréal, Safran, …) → it was heavily down-ranked and should rarely surface. If it does (thin-pool day), **do NOT spend a cold slot on it**: a cold email dies in their ATS and the apprenticeship aid legally excludes ≥250-salarié employers. Instead treat it like the portal case in 4a — find its careers page and send Zineb an `[ALERT · apply-via-portal]` so she applies directly, then move on. This does not count against the cold cap.
  Fill the cold slots from the TOP of this list. Skip a top lead only if its email is unreachable, it's on cooldown, it's a big corp, or you can't find a specific hook for it (then take the next).

**Queue construction**:
1. Fill warm slots: take up to `warm_remaining` follow-ups (Replied rows are excluded — notify-only).
2. Fill cold slots: take up to `cold_remaining` items from the TOP of `rank_pending_leads`.
3. Deduplicate by Contact Email (same address → keep highest priority).

**Never mix the pools** — the warm budget (3) and cold budget (7) are separate. A quiet day with
0 follow-ups due sends only cold; never spend cold slots as follow-ups or vice versa. Cold never exceeds 7, warm never exceeds 3.

---

## STEP 4 — EXECUTE EACH ACTION

For each item in the queue:

### 4·0. WARM CHECK (do this FIRST — a referral changes everything)
Before anything else, check whether Zineb already knows someone at this company:
```bash
python -c "import warm_network; print(warm_network.summary('COMPANY'))"
```
- **Prints a name** → this is a **warm/referral lead** (it's already ranked at the top for this
  reason). Referrals convert 5-10× cold, so treat it specially:
  - **Open on the connection, not a cold hook**: FR "*[Prénom du contact] m'a parlé de ce que vous
    construisez chez [Company] et m'a suggéré de vous écrire…*" (only if that's TRUE — match the
    real relationship from the warm note; never fabricate a referral).
  - Warmer, peer tone; lighter on credentials (the intro carries trust). Keep it short.
  - Still lint + send as usual; the LinkedIn double-tap (4h) reinforces it.
  - If the relationship is "she knows them but they haven't offered to refer", the honest move is a
    softer "*je crois que nous avons [X] en commun*" — never claim a referral that didn't happen.
  - **Worth a video?** A warm lead (or a strongly AI-native one) is a prime candidate for a 30s Loom —
    note it in the end-of-run summary so Zineb can run `/loom-script COMPANY` for it. Don't draft the
    script inline here (keep the run lean); just flag the 1-2 best candidates of the day.
- **Prints nothing** → also check the SCHOOL/CFA partner channel:
  ```bash
  python -c "import school_partners; print(school_partners.summary('COMPANY'))"
  ```
  - **Prints a partnership** → this company recruits alternants from Zineb's own M1 program, a
    legitimate high-intent angle. Two cases:
    - **Small startup partner** (not `likely_big_corp` in the ranking) → cold email as usual, but open
      on the school tie: FR "*Je rejoins le M1 [programme] à la rentrée — [Company] en est partenaire
      via le CFA numiA, et c'est exactement le type d'équipe où je veux faire mon alternance.*"
    - **Big-corp / ESN partner** (SNCF, BNP, Société Générale, AXA, Sopra, CGI…) → a cold email still
      dies in their ATS, but the school makes them a REAL alternance target. Do **not** cold-email:
      route to the application path — send Zineb an `[ALERT · school-partner apply]` with the careers
      URL and the tip to run `/cover-letter COMPANY` (the letter leads on the CFA/school partnership).
      This does not count against the cold cap.
  - **Prints nothing** → also check the GLOBAL-BRAND channel below.
- **Prints nothing (no warm, no school tie)** → check the reachable-international brand channel:
  ```bash
  python -c "import global_brands as g; print(g.channel_of('COMPANY'), '|', g.summary('COMPANY'))"
  ```
  This is Zineb's **reachable-international** lever — recognizable employers that hire juniors/
  alternants IN France (so a French contract — alternance or CDI — is genuinely on the table, unlike
  a `[Remote/International]` remote-abroad role). Two channels:
  - **`cold`** (Paris-HQ scale-up: Mistral, Alan, Qonto, Doctolib, Contentsquare…) → **cold-email as
    usual** and keep the normal alternance→CDI→CDD ask, but *lead on the international-but-local angle
    and the AI-agent-demo card* — these teams are exactly the audience that values an autonomous AI
    pipeline. Open on their product/mission ("*l'échelle internationale que vous construisez depuis
    Paris…*"), not on the brand name. Still subject to COLD_CAP and the same verification gate.
  - **`portal`** (global giant with a big Paris eng office: Google, Meta, Datadog, Stripe…) → a cold
    inbox dies in campus recruiting / ATS, so do **NOT** spend a cold slot. Route to the application
    path exactly like `likely_big_corp` in 4a: find the France careers page and send Zineb an
    `[ALERT · apply-via-portal] COMPANY — ROLE` with the URL + a tip to run `/cover-letter COMPANY`.
    Does not count against the cold cap.
  - **Prints `  |  ` (empty)** → normal cold flow below.

### 4a. FIND A NAMED CONTACT (cold outreach only)
If the Contact Email is a generic fallback (`contact@`, `hello@`, `info@`, `team@`, `jobs@`) **and** the company has no name in the email:

**Step 1 — search for a verified email (preferred over guessing):**
Run 3–4 searches in order, stop as soon as you find an actual email address:
1. `"COMPANY_NAME" site:linkedin.com CTO OR "Head of AI" OR "Head of Engineering" OR "VP Engineering"`
2. `"COMPANY_NAME" "@DOMAIN" email` — looks for the email in press releases, blog posts, or GitHub
3. `"COMPANY_NAME" team OR about` — their /team or /about page often lists names with contact info
4. `site:github.com "COMPANY_NAME"` — founders/engineers often have emails in GitHub profiles

**Step 2 — derive the email:**
- If you found the email explicitly on the web (blog, GitHub, press release, website) → use it as-is (verified)
- If you found only the person's name → you MUST confirm the domain's email format first: find any other team member's email from the same domain (check the company blog author bylines, GitHub org member profiles, any press release quotes). Then apply that confirmed pattern to the new person's name.
- **If you cannot confirm the email format from at least one real example on that domain** → do NOT guess. Use `contact@domain` instead and address the person by name in the opening line of the email body: "Bonjour [Name]," — the email still reaches the company, and it won't bounce.
- Strip diacritics (é→e, ç→c, etc.), lowercase, use the company's real domain

**Step 3 — verify before using (mandatory):**
```bash
python /path/to/stationf-agent/email_verify.py DERIVED_EMAIL
```
- Exit 0 (`✅`) → use that email
- Exit 1 (`❌ unverifiable`) → try the next pattern:
  ```bash
  python /path/to/stationf-agent/email_verify.py firstname@domain.com
  python /path/to/stationf-agent/email_verify.py f.lastname@domain.com
  ```
  Stop at the first that returns exit 0. **Maximum 3 pattern attempts total.**
- If all 3 patterns return exit 1 → fall back to the original generic email (`contact@domain`) and verify it too. If even that fails → skip this company entirely (log "no reachable address found").

**Portal check before you skip or cold-email a generic inbox.** If the best you have is a generic
`contact@`/`jobs@` inbox (no named human) AND, during your search, you saw that the company routes
applications through a careers portal / ATS, a cold email will likely die unread — the right move
is to apply through the portal, which Zineb does herself. Test any apply/careers URL you found:
```bash
python -c "import ats_detect; print(ats_detect.detect('THE_APPLY_OR_CAREERS_URL') or '')"
```
- **Prints a portal name** (e.g. `Lever`, `Greenhouse`, `Workday`) → do **NOT** spend a cold slot.
  Send Zineb an alert with the link so she applies directly, then move to the next lead:
  ```bash
  python smtp_send.py --to you@example.com \
    --subject "[ALERT · apply-via-portal] COMPANY — ROLE" --kind alert \
    --body "Cold email skipped — COMPANY applies through PORTAL_NAME, not a monitored inbox.
Apply here: THE_APPLY_URL
Role: ROLE. (Agent did not email; this needs a human application.)
Tip: run /cover-letter COMPANY to get a tailored lettre de motivation ready to paste." --send
  ```
  Log `[date] Agent: routed to portal (PORTAL_NAME) — needs manual application` on the row and
  leave its Status as `Pending` (it wasn't emailed). This does not count against the cold cap.
  (A portal application is far stronger with a real lettre de motivation — `/cover-letter` builds it.)
- **Prints nothing** → proceed normally with the generic inbox (better than nothing for a small co).
- If exit 0 with `[mx_only]` (SMTP inconclusive) → use the email but add a note `⚠ guessed` when logging

**Step 4 — update the tracker:**
```bash
python -c "
import tracker
df = tracker.load()
mask = (df['Company'].str.strip().str.lower() == 'COMPANY'.lower()) & (df['Role'].str.strip().str.lower() == 'ROLE'.lower())
df.loc[mask, 'Contact Email'] = 'First Last (CTO) <firstname.lastname@domain.com>'
tracker.save(df)
"
```

### 4b. RESEARCH THE COMPANY (mandatory — do this before every single email)

**Quality is the only priority here — never trade depth of research for saved Claude usage.** A
better email is worth far more than saved tokens. Always do the FULL research below for every
company, even when a cached fact already exists. Use the best model, take the time, dig deep.

**Check the hook-fact cache first** — but treat it as a *starting point to build on*, never a
shortcut that lets you skip research:
```bash
python -c "import lead_facts, json; r=lead_facts.get('COMPANY'); print(json.dumps(r, ensure_ascii=False) if r else '')"
```
- **Returns a fact** → good, that's one confirmed angle. Still run the full research below to find
  the *sharpest* hook and fresh context — then pick the strongest angle (the cached one or a better
  one you just found).
- **Returns empty** → do the full research below and **store the best fact you find** for follow-ups:
  ```bash
  python -c "import lead_facts; lead_facts.put('COMPANY', 'the specific real fact you hooked on', source='URL')"
  ```

Each email must be written for this specific company, not adapted from a template. Research the
company thoroughly — go deeper than the minimum if the first pass is thin:

1. **WebSearch**: `"COMPANY_NAME" product OR technology OR AI OR engineering 2025 OR 2026` — find what they actually build and any recent news
2. **WebFetch their website** (homepage + /product or /about if it exists) — read what they do in their own words
3. **Identify one concrete, specific detail** you can hook on:
   - A technical architecture choice ("vous utilisez un pipeline X pour Y")
   - A product challenge that's visible from the outside ("le problème de [X] dans votre cas doit être…")
   - A recent launch, partnership, or pivot
   - A specific feature that connects directly to Zineb's skills

If you cannot find anything specific after 2–3 searches → skip this company and move to the next one. A generic email is worse than no email.

**This research step is NOT optional.** Every email hook must cite something real.

---

### 4c. DRAFT THE EMAIL

**The goal: the recipient reads the first sentence and thinks "this person actually looked at what we do."**

---

#### ABSOLUTE RULES — check every one before writing a single word

❌ NEVER start with: "Je m'appelle Zineb", "Je me permets de vous contacter", "Bonjour," as a standalone line, "Votre offre m'a interpellée", "Je suis à la recherche d'une alternance"
❌ NEVER write "Je suis Zineb Meftah" anywhere in the body — her name is already in the FROM field
❌ NEVER end the body with "Zineb Meftah" or any name — `smtp_send.py` adds the signature automatically
❌ NEVER write cost/AUA as its own paragraph or a figure-dump — at most ONE embedded clause, only when it fits the offer + company (see the AUA judgment section); when in doubt, omit it and lead with value
❌ NEVER use subject lines like: "Candidature alternance", "Candidature Analytics Engineer", "Ma candidature"
❌ NEVER write a list of technical skills (Python, PyTorch, Docker…) — those belong on the CV, not the email. NB: this is different from the CREDIBILITY line, which is 2–3 *achievements* (major de promo, ENSIA, research) compressed into ONE line — that is required, a skills laundry-list is not
❌ NEVER exceed 190 words in the body (cold) or 65 words (follow-up). **Cold target: 150–180 words (MEDIUM)** — rich enough to sell Zineb (a "what I'd bring" line + one credibility line), short enough to actually get read. Below ~90 words it reads as a thin drive-by; above ~180 a busy reader defers it (190 is a hard ceiling, not the goal). Ultra-short (Strategy U, busy execs) is the deliberate exception (~50–80).
❌ NEVER use "pipeline multi-agent RAG" as the credential if the company has nothing to do with RAG/document AI — match the right project to their actual domain (see project matching below)
❌ NEVER write two consecutive cold emails with the same paragraph structure — check the last draft in `drafts/` before writing

❌ NEVER let a sentence run past ~20 words or splice two clauses with a comma ("le mur n'est pas X, c'est Y quand Z…") — one idea per sentence, full stop, new sentence
❌ NEVER cram links into a prose sentence next to a credential ("1ère/126 — url1, url2") — each link on its own short line
❌ NEVER send a wall of text — the body is 4–6 short blocks separated by blank lines, not one dense paragraph
❌ NEVER pile up technical acronyms (BM25, cross-encoder, ACT, neuromorphique…) — name at most ONE technical thing, in plain words, and only if it IS the hook

✅ First sentence is always about THEM, based on specific research from step 4b
✅ Subject line must be specific enough that only someone who actually looked at their product could write it
✅ Every sentence must earn its place — if removing it wouldn't hurt the email, remove it
✅ Read it back before sending — if you run out of breath in a sentence, split it
✅ Lead with an INSIGHT into THEIR problem + name the STAKES (what it costs them) — see MAKE THEM CARE
✅ The CTA carries a payoff (an idea to show, a specific angle) — never a bare "10 minutes ?"

---

#### STRUCTURE & READABILITY — how it looks on screen decides if it gets read

A busy founder skims in 3 seconds. Dense, jargon-packed blocks get deleted no matter how sharp
the research. **Structure and plain language matter as much as the hook.** `email_lint.py` now gates
this: longest sentence ≤28 words, no one-block walls, links on their own line — but write it right
the first time.

**The skeleton — a MEDIUM email, ~150–180 words, 6 short blocks, blank line between each:**
```
Bonjour [Prénom],

[HOOK — 1–2 sentences about THEIR specific problem + what it costs them (the stakes). About THEM, plain words, no jargon dump.]

[PROOF — 2–3 sentences: the ONE directly-relevant thing you built, in production, said plainly.]

[WHAT I'D BRING — one sentence, "Pour [Company] : …" — translate the proof into 1–2 concrete things you'd deliver for THEM specifically. This is the block that was missing.]

[CREDIBILITY — one short line: the 2–3 strongest signals for THIS reader (pick from the palette below). One line, never a paragraph.]

[CONTRACT ASK — one clean sentence (see contract-type section).]

Projets : linkedin.com/in/zineb-meftah
[Démo/Code : one proof link on its own line]

[CTA — one low-friction question with a payoff.]
```

**Target ~150–180 words.** Two blocks are new vs. the old thin style: **WHAT I'D BRING** (your value to
*them*) and the **CREDIBILITY** line (finally uses your real background). They are what stop the email
reading as a forgettable drive-by — but keep each to ONE line/sentence so it stays scannable, never a
CV dump. A medium email that is 6 tight blocks reads *easier* than a 110-word email crammed in one
paragraph: length is fine, density is not.

**CREDIBILITY palette — pick the 2–3 that matter most to THIS reader, don't list them all:**
major de promo (1ère/126 en L2 et L3) · prépa ENSIA à Alger, cursus intégralement en anglais ·
anglais C2 · recherche publiée sur Hugging Face · stage IA en production chez GE HealthCare ·
programme de leadership tech de l'ambassade des États-Unis (AYLP) · lead GDSC / hackathon gagné.
A technical founder cares most about the production work + research; a recruiter about the ranking +
GE HealthCare + availability. Choose accordingly (see TAILOR THE EMAIL below).

**Sentence rules:** one idea per sentence; aim ≤20 words, never >28. No comma-splices — full stop,
new sentence. Plain language beats acronym soup: *"un système de recherche documentaire en
production"* reads; *"BM25 → reranker cross-encoder → génération citée"* does not. A reader should
never need a glossary.

**Links:** each on its own short line (`Projets : <url>`), max two, never stuffed into a sentence.

**BEFORE (dense, deleted) → AFTER (scannable, read):**
> ❌ BEFORE: "Perception sub-milliseconde à 100× moins d'énergie sur satellite et drone, en couplant
> caméras événementielles et processeurs neuromorphiques : de la vraie contrainte edge temps réel.
> J'ai livré un pipeline MLOps LeRobot temps réel (politique ACT) et de la détection d'objets
> embarquée in-browser. 1ère/126 en L3 IA — linkedin.com/…, huggingface.co/…"
>
> ✅ AFTER:
> Faire tenir de la perception temps réel dans le budget énergie d'un drone, c'est le vrai verrou.
> C'est exactement le terrain de Neurobus.
>
> De mon côté : un modèle de vision embarquée qui tourne en temps réel, et un détecteur d'objets qui
> s'exécute directement dans le navigateur, sans serveur. Major de ma promo L3 IA.
>
> Je démarre un Master IA en septembre — en alternance, ou en CDI si vous préférez un temps plein.
>
> Projets : linkedin.com/in/zineb-meftah
> Démo : huggingface.co/spaces/zino36/lerobot-pusht-trainer
>
> Auriez-vous 10 minutes cette semaine ?

Same facts, same links, same length — but the AFTER gets read. Every strategy below produces the
HOOK block; the skeleton above governs the whole email.

---

#### MAKE THEM CARE — attention & stakes (this is what earns the reply)

Readable isn't enough. A clean email that just says "here's my understanding of your problem and
here's what I built" still reads like a resume. The reply comes when the founder feels *understood*
and sees a *payoff*. Below are four levers that create that — **but they are a TOOLKIT, not a template
to stamp on every email.** You won't use all four every time, and the STRATEGY you pick (see PICK ONE
STRATEGY) decides which to lead with and in what shape. The only two constants: an insight that makes
them feel understood, and a reason to reply. Never send two emails with the same four-part shape —
that's a template, and templates get ignored.

**1. Open with an INSIGHT, not an observation.** An observation ("vous utilisez du RAG") is boring —
any applicant could write it. An insight is the sharpest version of THEIR problem, phrased the way
they feel it at 2am, so they think *"yes, exactly — this person gets it."*
> weak:   "Vous traitez des contrats avec de l'IA."
> strong: "Le piège n'est pas de résumer un contrat — c'est de citer la mauvaise obligation avec assurance."

**2. Name the STAKES — what it costs them if it stays unsolved.** One vivid, concrete line: lost
trust, wasted spend, a risk that bites. This is the biggest lever and the one most emails skip.
(The best line in the entire last batch was Sonaar's *"un appel d'offres fantôme entame la
confiance"* — do that every time.)
> "Une seule clause inventée, et votre client cesse de faire confiance au produit."

**3. Reframe the proof as "I've already solved THAT exact tension"** — not a resume bullet, but the
answer to the stakes you just named. Connect what you built to the outcome they want.
> "Chez GE HealthCare, un système que j'ai mis en prod refuse justement d'inventer : il cite la
> source exacte, ou il dit qu'il ne sait pas."

**4. Give the CTA a PAYOFF — a reason the 10 minutes is worth it.** Never a bare "10 minutes ?".
Offer something concrete: an idea you'll show them, a specific angle, a question only they can
answer. Make saying yes feel like a gain, not a favour.
> "Deux idées concrètes pour fiabiliser la citation dans Oro — 10 minutes pour vous les montrer ?"

**Voice:** a peer who can help ship the thing, not a student asking for a chance. She brings value;
she isn't requesting a favour. Confident, warm, specific.

**The test for every line: "so what — why should THEY care?"** If a sentence is about Zineb and
doesn't move the reader toward their own win, cut it or reframe it around them.

**Full BEST example (Tomorro) — all four moves, still ~100 words, lints clean:**
> Sur Oro, le piège n'est pas de résumer un contrat. C'est de citer la mauvaise obligation avec
> assurance. Une seule clause inventée, et votre client cesse de faire confiance au produit.
> *(← insight + stakes)*
>
> Chez GE HealthCare, un système que j'ai mis en prod refuse justement d'inventer : il cite la
> source exacte, ou il dit qu'il ne sait pas. Sur des specs médicales, où une erreur se paie cher.
> *(← proof that resolves the tension)*
>
> Master IA à la rentrée, dispo en alternance pour attaquer ça chez Tomorro. *(← contract ask)*
>
> Deux idées concrètes pour fiabiliser la citation dans Oro — 10 minutes pour vous les montrer cette
> semaine ? *(← CTA with a payoff)*
>
> Projets : linkedin.com/in/zineb-meftah

**Don't lose Zineb's sharpest card: HOW this email was sent.** For AI-native companies, dev tools,
autonomous-systems, and any technical/builder audience, the single strongest attention device is that
*this very message was written, targeted and sent autonomously by an agent she built and deployed.*
It's a pattern-interrupt and live proof at once — moves 1 & 3 in one line ("Ce message, vous ne l'avez
pas reçu de moi…"). Reach for it whenever the company would care (that's **Strategy A** below). The
auto P.S. footer always discloses it formally at the bottom; the body decides whether to also *lead*
with it as the hook — for the right company, nothing lands harder.

This is the bar. Structure makes it *readable*; the levers make it *matter*; the agent-demo, when it
fits, makes it *unforgettable*.

**But vary the vehicle every single time.** Readability and "do they care?" are constant — the *angle,
opening device, length, and rhythm must differ email to email*. One is a sharp technical question;
the next an ultra-short three-line punch; the next the agent-demo; the next a mirrored challenge that
names their pain. **We do not yet know which lands best for Zineb — so we are deliberately experimenting
(explore): try a different strong approach each time, measure the replies, and converge on the winner.**
Two emails that feel the same are a wasted experiment. Concretely: within today's batch, pick a
*different* strategy for each cold email (no repeats in one run), and skew toward one you haven't tried
much yet — the bandit in PICK ONE STRATEGY tells you which.

---

#### CONTRACT TYPE — choose the ask based on the opportunity (do this FIRST)

Zineb is open to three contract types. Her order of preference: **CDI > CDD > Alternance** —
but alternance is her structured path (she starts a Master IA in Sept 2026). The contract you
request depends on what the company posted.

🚫 **NEVER list all three as a menu** ("je cherche un CDI, CDD ou alternance"). That reads as
desperate and unfocused. Lead with the ONE that fits the posting; signal openness to the
others in a single confident clause, only when it adds value.

🌍 **INTERNATIONAL / REMOTE leads override this — check FIRST.** If the role is tagged
`[Remote/International]` (from the `remotive` source), the company is foreign with no French entity,
so **alternance is impossible** (it needs a French employer + French school). For these:
- **Write in ENGLISH**, always.
- **Ask for an internship, new-grad, or full-time (CDI-equivalent) role** — whichever the posting
  implies. **Never say "alternance"** (a French term they won't understand) and never mention a CV
  in French. One confident line, e.g. *"I'm starting an AI Master's this September and looking to join
  a team like yours remotely — as an intern now, or full-time."*
- It's a **remote** role — say so naturally ("…remotely from France / across European hours").
- Detect it: `python -c "import config; print(config.is_remote_international('ROLE_HERE'))"`.
Global companies WITH a French office (from the French sources, no tag) keep the normal logic below
— alternance is fine there.

📍 **LOCATION MODE — frame the email to how they work (Zineb pursues BOTH remote and in-person).**
Every ranked lead carries a `location_mode` (`remote` | `hybrid` | `onsite` | `""`); detect it for any
role with `python -c "import config; print(config.classify_location('ROLE_TEXT'))"`. There is **no
ranking bias** — remote and in-person are equally pursued — the mode only changes ONE framing line:
- **`remote`** (French/EU role, not the `[Remote/International]` tag) → note she works remotely well:
  *"…en full remote depuis la France, ou hybride si vous préférez."*
- **`hybrid`** → signal she's happy on-site part-week: *"…en hybride, présente à Paris plusieurs jours
  par semaine."*
- **`onsite` / `""`** (default, most Station F roles) → in-person availability: *"…sur place à Paris"*,
  and — since Zineb is **open to relocating (elsewhere in France or abroad)** — if the role is outside
  Île-de-France, add one honest line: *"prête à m'installer sur place pour le bon poste."*
Keep it to ONE clause near the contract ask — never a paragraph, never all modes listed.

First, detect the posting's contract type:
```bash
python -c "import config; print(config.guess_contract_type('ROLE_TITLE_HERE'))"
```
Returns: `alternance` | `stage` | `cdi` | `cdd` | `speculative` | `unspecified`

Then pick the ask:

**`alternance` posting → direct match.** Apply for the alternance, hint at staying long-term.
> FR: "Je postule à votre alternance — et l'idée de m'inscrire dans la durée chez vous m'attire."
> EN: "I'm applying for your work-study role — with the goal of building something lasting with you."

**`cdi` / `unspecified` posting → the reframe (strongest move).** They want a permanent hire.
Offer alternance as the lower-risk way to prove fit, while staying genuinely open to the CDI.
> FR: "Vous recrutez un·e [role]. Je démarre un Master IA en septembre — donc flexible sur le
> format : en alternance, le même profil 3–4 j/semaine avec une année pour valider avant un CDI ;
> ou un CDD/CDI directement si vous préférez un temps plein."
> EN: "You're hiring a [role]. I'm starting a Master's in AI this fall, so I'm flexible on
> format: as a work-study, the same profile 3–4 days/week with a year to prove fit before a
> permanent role — or a fixed-term/permanent contract if you'd rather."

**`cdd` posting → apply for the CDD, mention alternance as a long-term option.**
> FR: "Votre [role] en CDD m'intéresse. Je suis aussi ouverte à une alternance M1 si vous
> voulez construire dans la durée."

**`stage` posting → upsell to alternance** (she's already finishing her L3 graduation internship).
> FR: "Vous proposez un stage — je termine justement le mien chez GE HealthCare. Pour 2026-2027
> je vise une alternance M1 ; si vous êtes ouverts à ce format, j'aimerais en discuter."

**`speculative` (no posting) → alternance-first, open to the rest.**
> FR: "Je cherche une alternance M1 à partir de septembre 2026 — ouverte à un CDD ou CDI selon
> votre besoin."

The flexibility clause appears **once**, near the CTA. Never twice, never as a list.

**Seasonal urgency — calibrate to how close September is.** Check it:
```bash
python -c "import config; print(config.weeks_until_alternance(), 'weeks to start')"
```
French alternance seats fill across the summer, so proximity is a real, honest lever — but it must
read as *in-demand, not desperate*. Use at most ONE calm clause, only when < ~14 weeks out:
- FR: "je finalise mes choix pour la rentrée de septembre" / "je cale mon alternance pour septembre".
- EN: "I'm finalising where I'll be this September."
Never "je suis disponible immédiatement", never multiple urgency lines, never exclamation. If the
start is far off (or the ask is a pure CDI), drop it entirely.

---

#### PICK ONE STRATEGY — self-improving (multi-armed bandit)

**This is the engine that finds Zineb's best-performing style.** We don't guess the winner up front —
we send genuinely different, *credible* approaches, track which earn replies, and let the data crown
the winner. So the job each run is: pick a strong strategy that fits the company, **vary it from what
you sent recently**, and feed the loop. The agent learns which strategies actually earn replies —
before choosing, get the recommendation:
```bash
python -c "import tracker, json; print(json.dumps(tracker.recommend_strategy_order(), indent=2))"
```
- **`phase: explore`** (early — not enough data yet): prefer the **least-used** strategy that still
  fits the company, so every strategy gets a fair test. Variety now = better data later.
- **`phase: exploit`** (enough data): favour the **highest reply-rate** strategy (`recommend`)
  when it fits — but still occasionally try the least-used arm to stay adaptive.

This is a bias, NOT a rule: **fit to the company always wins.** If the recommended strategy doesn't
suit this company (e.g. bandit says `A` but the company isn't AI-native), pick the one that fits and
log it — the data will catch up. Never force a misfit strategy just because the bandit prefers it.

**Also check the broader self-improving signals (WS4)** — what's earning replies beyond strategy
(company type, contract ask, subject shape):
```bash
python -c "import learning, json; print(json.dumps(learning.recommend(), ensure_ascii=False, indent=2))"
```
- **`phase: explore`** → not enough reply data yet; keep variety high (vary subject shape, contract
  framing, company type) so every bucket gets sampled. Change nothing forced.
- **`phase: exploit`** → apply the `insights` as a *soft* nudge: lean toward the `↑` buckets (e.g.
  "subjects with '?' reply above base") and away from the `↓` ones when writing this email's subject
  and choosing whom to prioritise. Same rule as above: **company fit wins**; these are biases, not
  mandates. (The ranking already folds a tiny, data-gated version of this into `rank_pending_leads`.)

Then pick from the seven:

**Strategy Q — Technical Question** *(use when you found a specific technical challenge or architecture choice)*
Open with a genuine question only someone who studied their product would ask. Not rhetorical — one they'd actually want to answer.
> Subject: `"[Specific technical problem] chez [Company] — question + alternance M1"`
> Opening: `"Comment vous gérez [specific problem] côté [feature] ? C'est exactement le défi que j'ai attaqué chez GE HealthCare sur [analogous system] — j'aimerais comparer les approches."`

**Strategy O — Precise Observation** *(use when you spotted something specific: a tech choice, a product gap, a design decision)*
State something concrete you noticed. One sentence. Not "I noticed you use AI" — be exact.
> Subject: `"[Specific observation about their stack/product] — alternance M1 IA 2026"`
> Opening: `"Votre choix de [specific decision] pour [feature] — c'est exactement l'approche que j'aurais choisie, et c'est ce que j'ai mis en production chez GE HealthCare pour [analogous problem]."`

**Strategy V — Value Proof First** *(use for fast-moving startups, technical founders, very short attention spans)*
Lead with what Zineb delivered. No intro. Just the result, then connect it to them.
> Subject: `"[Specific result] en prod — alternance M1 chez [Company] ?"`
> Opening: `"J'ai livré en 1,5 semaine [very specific thing matching their domain] en production chez GE HealthCare. Ce que vous construisez chez [Company] — [one specific thing] — est exactement la continuité logique."`

**Strategy M — Mirrored Challenge** *(use when you can identify a specific pain point they likely face)*
Name the exact challenge they have, then show you've already faced it.
> Subject: `"Le problème de [specific challenge] chez [Company] — et comment je l'ai attaqué"`
> Opening: `"[Specific challenge] dans votre cas, c'est probablement [specific manifestation]. J'ai travaillé exactement là-dessus chez GE HealthCare — [what Zineb did and learned in one clause]."`

**Strategy U — Ultra-short** *(use when the contact is a CTO/founder known to be busy — max 4 sentences total including CTA)*
Shortest possible email. Every word pulls weight.
> Subject: `"[Company] + IA — 30 secondes ?"`
> Body: `"[One very specific sentence about their product.] J'ai livré [specific result] chez GE HealthCare — 1ère/126 en L3 IA Avignon. Alternance M1 septembre 2026. 10 minutes cette semaine ?"`

**Strategy A — Agent Demo** *(use for AI-native companies, developer tools, autonomous systems — this is Zineb's strongest card)*
**The outreach agent IS the demo — this very email is live proof she can build the thing.** It's the
purest form of MAKE THEM CARE moves 1 & 3 at once: the pattern-interrupt ("you didn't get this from a
human") IS the insight, and "it runs in prod, not a demo" IS the proof. Never bury it in a jargon list
of pipeline steps — open with the reveal, keep it punchy. **Full worked example (lints clean, ~90 words):**
> Subject: `"L'agent qui vous a écrit ce message — alternance M1 chez [Company]"`
>
> Ce message, vous ne l'avez pas reçu de moi. Un agent que j'ai conçu et déployé en production l'a
> écrit, ciblé, et envoyé tout seul. Il mesure même son propre taux de réponse pour s'améliorer.  *(← pattern-interrupt = insight)*
>
> C'est exactement le type de système autonome que [Company] construit. La différence : le mien
> tourne déjà en prod, pas en démo.  *(← proof + differentiator)*
>
> Master IA à la rentrée, dispo en alternance pour bâtir ça chez vous.  *(← contract ask)*
>
> Je peux vous le montrer tourner en live. 10 minutes cette semaine ?  *(← CTA with payoff)*
>
> Code : github.com/ZinebMEFTAH
> Profil : linkedin.com/in/zineb-meftah
>
> Why it works: the email itself is the portfolio. For any company building AI agents or autonomous
> systems, this lands harder than any credential. **The auto P.S. footer already discloses the full
> pipeline (scraping → LLM qualification → personalisation → SMTP) at the bottom — so the body's job
> is the hook, not the spec sheet.** Keep the body punchy; let the footer carry the detail.

**Strategy G — Insight Gift** *(give-first; the highest-reply-rate play when you can find something genuinely useful)*
Lead by GIVING, with no ask up front. Offer one concrete, specific thing of value: a small
improvement you noticed, a relevant approach/paper, a sharp take on a problem they're visibly
solving. The ask comes last, almost as an afterthought. Triggers reciprocity + proves competence.
> Subject: `"Une idée sur [specific thing] chez [Company]"`
> Opening: `"En testant [their product/feature], j'ai remarqué que [specific, real observation] — une piste : [concrete, useful suggestion you'd actually implement]. C'est le genre de problème que j'ai résolu chez GE HealthCare sur [analogous case]."`
> Close (soft): `"Si ça vous parle, je serais ravie d'en discuter — et je cherche justement une alternance/CDI M1 dans cette direction."`
> Why it works: you're the rare person who gave before asking. Even a "no" often comes with thanks + a door left open. ONLY use when the insight is genuinely good — a fake/generic "tip" backfires badly.

---

#### TAILOR THE EMAIL TO THE RECIPIENT — length AND content adapt to who reads it

You know the recipient's role (from the named contact). Match **both the length and the content** to
what THEY care about — this is the "adaptive by recipient" rule:

- **Founder / CTO / Head of AI / tech lead** → **full medium (~160–180 words).** They read substance.
  Go technical and specific (Strategies Q, O, M, A, G); reference their architecture/product. The
  WHAT-I'D-BRING block is technical (what you'd build/fix for them); the CREDIBILITY line leads on the
  **production work + published research** (ranking is secondary). Peer-to-peer tone — a fellow builder.

- **Head of Talent / Recruiter / Campus / HR** → **tighter medium (~130–150 words).** They scan for
  *fit & logistics*, not your reranker. WHAT-I'D-BRING = the right profile for [the role], availability
  (Sept 2026), contract flexibility; CREDIBILITY line leads on **1ère/126 + GE HealthCare + ENSIA**.
  Strategy V works best. Keep it scannable. NO deep architecture talk.

- **CEO of a non-technical / small startup** → **medium (~140–160 words), business value** not tech
  internals. WHAT-I'D-BRING = what Zineb can *build for them* in plain outcomes. Strategy V or M.

- **Busy CTO/founder known to be slammed** → **Strategy U, stays short by design (~50–80 words).** The
  deliberate exception to medium: one sharp product sentence + one proof + the ask. Don't pad it.

If you don't know the role, default to the technical/peer register at full medium (most Station F
contacts are technical).

---

#### SUBJECT LINE — the single biggest lever on whether the email is opened at all

The subject decides the open. If it reads like a mass application, it's deleted unread. Rules:

- **6–9 words, lowercase-ish, specific to THEM.** It must be something only someone who studied
  the company could write. If the subject would fit any company, rewrite it.
- **Lead with their world, not your ask.** Put the company/product/problem first, the alternance second.
- **A question or a curiosity gap pulls opens.** "Comment vous gérez X chez [Company] ?" / "Une idée sur X".
- **No spam triggers**: avoid "Candidature", ALL CAPS, multiple !!!, "URGENT", "gratuit".
- **Follow-ups**: always `Re: [original subject]` (threads + higher open rate).

Good: `"Reranker cross-encoder chez Sekoia — une question"` · `"Le coût d'inférence chez [Company] — une piste"` · `"J'ai déployé un agent IA pour vous écrire"`
Bad: `"Candidature alternance M1"` · `"Étudiante motivée cherche alternance"` · `"Mon profil pour votre offre"`

**Subject self-test**: cover the company name — does the subject still make sense for any company? If yes, it's too generic; rewrite with a concrete detail.

---

#### THE CTA — make replying almost effortless

The ask is where most emails die by asking for too much. Lower the friction:
- ✅ "Ça vaut un échange de 10 min cette semaine ?" / "Une réponse d'une ligne me suffit." / "Vous voyez ça aussi ?"
- ❌ "Seriez-vous disponible pour un entretien de 30 minutes ?" / "Voici mon CV, mon profil détaillé…" / multiple asks
- One ask only. A question mark. Easy to say yes (or even to say no) in one line.
- For Strategy G, the ask is secondary to the gift — keep it soft and last.

---

#### STRUCTURE — vary it, never use the same layout twice in a row

**Fixed spine, variable hook.** Vary the *opening hook and the layout* every time — but the
*spine stays constant*: relevant production proof → **what I'd bring THEM** → a one-line credibility
signal → the ask (contract + availability Sept 2026) → links. Only the first 1–2 lines (the researched
hook) truly change per company. Don't regenerate the whole email from scratch chasing novelty —
that's what drifts back into generic; keep the proven spine and swap the hook.

The 6 elements (hook, proof, what-I'd-bring, credibility, ask, CTA) do NOT have to appear in a rigid
order — vary the layout so no two consecutive emails look identical. Valid variations:

- **Question-first**: open with a question about their problem, answer it with the proof, then bring/ask
- **Result-first**: lead with the production result, tie it to their need, then bring/ask
- **Stakes-first**: name what their problem costs them, then show you've solved that exact thing
- Whichever layout you pick, the what-I'd-bring line and the one-line credibility signal must be present
  (except Strategy U, which drops them by design to stay ultra-short)

Before writing: check `drafts/YYYY-MM-DD/` for the most recent cold email. If it uses P1+P2+P3 structure → do NOT use that structure. If it opens with a question → do NOT open with a question.

---

#### MANDATORY SELF-CHECK before saving the draft

Run through this mentally before every send:

1. **The blank-company test**: Remove the company name from the email. Does it still work? If yes → the hook is not specific enough → rewrite.
2. **"Je" count**: Count how many times "Je"/"j'" appears. If more than 5 → the email is drifting self-centered → trim (the hook must still be about THEM; the what-I'd-bring/credibility lines legitimately use some "je").
3. **What-I'd-bring check**: Is there one clear "Pour [Company] : …" line saying what you'd deliver for THEM? If missing (and it's not Strategy U) → add it. This is the block that stops the email feeling thin.
4. **Credibility check**: Is there ONE line with 2–3 achievements chosen for this reader? Missing → add it. A paragraph of them → compress to one line.
5. **Cost check**: If cost/AUA appears — is it justified (small startup < 250 + alternance in the ask) AND folded into one clause (not its own paragraph, not the opener)? If it's a large company, a pure-CDI focus, a figure-dump, or a standalone paragraph → cut it.
6. **Name check**: Does the body end with "Zineb" or "Zineb Meftah"? → Delete it.
7. **Word count**: Cold 150–180 words (medium); < ~90 reads thin (unless Strategy U), > 180 → cut. Follow-up > 65 words → cut until under limit.
6. **Subject line test**: Could this subject line have been written without reading about the company? If yes → rewrite.
7. **First line test**: Does the opening sentence start with "Je"/"J'" or describe Zineb? → rewrite so it opens on THEM (the researched fact).
8. **Cliché test**: Any generic flattery — "votre entreprise/société", "acteur majeur", "leader dans", "passionné(e) par", "rejoindre votre équipe", "vos valeurs"? → delete and replace with something specific and true.
9. **CTA test**: Is there exactly one low-friction question (ending in "?") that makes replying effortless? If none → add one; if several asks → keep the easiest.
10. **Show-don't-tell test**: Are competence claims backed by a concrete result/architecture rather than adjectives ("innovant", "rigoureuse")? Replace adjectives with proof.

---

#### GOVERNMENT AID (AUA) — a JUDGMENT call: include it or drop it based on the offer + the company

The cost angle is a **lever, not a default**. Used well on the right target it removes a real
barrier; used wrong it undersells her. Decide per email.

**Include the AUA clause ONLY when BOTH hold:**
1. **Alternance is part of the ask** (alternance / cdi-reframe / cdd-with-alternance-option /
   stage-upsell / speculative). Pure CDI or pure CDD with no alternance → the AUA doesn't apply → drop it.
2. **The company is a small startup / SME (< 250 employees).** The *aide unique à l'apprentissage*
   is legally restricted to employers under 250 salariés — quoting the figures to a large company
   is factually wrong and reads as a canned template. Big corp / listed group / clearly ≥250 → **drop it**,
   lead on fit + value + availability instead. Station-F-scale startup → almost certainly < 250 → fine to keep.

**Even when it applies, it is never the pitch and never leads.** Lead with value, proof, and fit;
the cost is at most ONE embedded clause, folded into another sentence (ideally the CTA) — never its
own paragraph, never a figure-dump. If in doubt, leave it out — a strong candidate doesn't open on price.

Facts (only if you include it): AUA up to **6 000 €** first year (< 250 salariés); charges quasi-nulles;
coût réel often **400–700 €/mois**. Phrasing must change every email — never copy-paste.

**Bad** (standalone paragraph — FORBIDDEN):
> "Pour votre taille, une alternance coûte moins de 700 €/mois réel : l'AUA (jusqu'à 6 000 € la 1ère année…) et les exonérations font qu'un alternant revient 3 à 4× moins cher qu'un CDI."

**Good** (one clause, embedded in the CTA):
> "Je vise une alternance M1 à partir de septembre 2026 — un format léger à mettre en place de votre côté. 10 minutes cette semaine pour en parler ?"

**Good** (mid-email, only if it truly fits a small startup):
> "…ce qui, en alternance d'apprentissage, reste accessible pour une équipe de votre taille."

---

#### MATCHING ZINEB'S PROJECTS TO THE COMPANY'S DOMAIN — be precise, not generic

**Use the PROJECT MATCHING GUIDE at the bottom of `about_me.txt`** (already loaded at step 0).
It maps 15+ company domains to the exact project, credential, and framing to use.

Key principle: pick ONE project that fits the company's actual technical domain. Never default
to "multi-agent RAG pipeline" for every company — match what THEY build.

For agent-infrastructure / developer tools companies → **Strategy A**: the outreach agent
itself is the live portfolio demo. This is Zineb's strongest card.

If nothing in the guide fits clearly → describe the GE HealthCare pipeline in the company's
own vocabulary, not in generic AI buzzwords.

---

#### OPENING-LINE PATTERNS BY COMPANY TYPE (adapt — NEVER copy verbatim)

These are *shapes* for the first 1–2 sentences, not templates. The bracketed parts MUST be
filled from your 4b research with something real and specific; an unfilled or vague bracket
means you haven't researched enough. Every opener leads on THEM, then bridges to ONE precise
Zineb proof. (They still pass the blank-company test: remove the company and it collapses.)

- **AI / LLM product** → name the hard part of their AI, then the matching proof.
  *"Sur [leur produit], le vrai mur c'est [retrieval/hallucination/latence sur X] — c'est
  exactement ce que j'ai calibré chez GE HealthCare avec un reranker cross-encoder en prod."*
- **Dev tools / agent infra** → Strategy A, the agent IS the demo.
  *"Cet email a été écrit et envoyé par un agent que j'ai mis en prod — le genre de pipeline
  [scraping→LLM→envoi vérifié] que vous construisez chez [eux]."*
- **Data / analytics** → a specific data-quality/pipeline problem in their stack.
  *"Séparer le signal du bruit dans [leur cas data] coûte cher en faux positifs — j'ai attaqué
  ça avec [dédup sémantique / calibrage précision-rappel] chez GE HealthCare."*
- **Fintech / regulated** → reliability/compliance angle (verification, precision).
  *"En [paiement/conformité], un faux positif coûte la confiance — mon travail chez GE
  HealthCare portait précisément sur la calibration précision/rappel en environnement régulé."*
- **Healthtech** → her GE HealthCare domain is a direct match; lead with it.
- **Early / founder-led startup** → builder-to-builder, light on credentials, one sharp idea.
  *"[Observation précise sur leur produit récent]. J'ai une idée concrète sur [X] — 10 min ?"*
- **Scale-up / larger** → reference the specific team/role on the posting, not the C-suite.

If you can't fill the brackets with something true and specific → research more or skip the
company. A filled-in cliché is still a cliché.

---

#### LANGUAGE & TONE
- French company or French-language role → French + attach CV_FR. Otherwise English + CV_EN.
- **`[Remote/International]`-tagged lead → ALWAYS English + CV_EN**, and an internship/CDI ask (never alternance).
- Tone: confident peer, never student begging. Write as if Zineb is doing them a favour by applying.
- Warm and direct — no "je me permets", no excessive politeness, no filler.
- Record the strategy by passing **`--strategy X`** to `smtp_send.py` (see SEND step) — it writes the
  `Agent (Strategy:X):` marker automatically and correctly. **Never hand-write the marker** (that was
  the old, gap-prone way); the flag guarantees the memory has no holes and the bandit sees every send.
- **Do NOT** write the P.S. footer, signature, or mention the CV — all added automatically.

### 4d. LINKEDIN LINK — cold emails only

Every cold email body must include Zineb's LinkedIn URL as a natural inline mention — not a separate line, embedded in the flow:

> "…vous pouvez retrouver mes projets sur [linkedin.com/in/zineb-meftah](https://www.linkedin.com/in/zineb-meftah)."

or at the end of the credentials sentence:

> "…Major de promotion L3 IA Avignon (1ère/126) — [linkedin.com/in/zineb-meftah](https://www.linkedin.com/in/zineb-meftah)."

Do NOT write it as a standalone paragraph or label it as "Mon LinkedIn :". One inline hyperlink, that's it.

**Second link — the proof link (add ONE, inline).** LinkedIn shows who she is; the second link
should *prove she's real* in one click. Two links maximum, both woven into the flow, never a list.

- **Default (most companies) → the proof page: `https://zinebmeftah.github.io/alternance`.** A focused,
  bilingual (FR/EN) 20-second landing built for cold outreach: hero "systèmes IA autonomes en
  production", the alternance-M1-Sept-2026 ask, and GE HealthCare RAG + the outreach agent + LeRobot as
  live proofs — and it links onward to the full portfolio. Weave it in, e.g. "…quelques systèmes que
  j'ai mis en prod : zinebmeftah.github.io/alternance." Works for technical and non-technical
  recipients alike, so it's the safe strong default. (The deeper full portfolio lives at the root
  `zinebmeftah.github.io` — use that only if a recipient explicitly wants the complete CV/projects.)
- **AI-native / dev-tools / ML companies → a domain-matched RUNNING artifact instead.** For a
  technical founder, live code can beat a portfolio index. Swap the second link for the one that
  matches what they build (from the LINKS block in `about_me.txt`):
  - Agent infra / dev tools → GitHub `github.com/ZinebMEFTAH` (the outreach agent itself).
  - ML / models / research → a Hugging Face artifact (the LeRobot Space or the inverse-fine-tuning blog).
  Use the portfolio OR the artifact as the second link — not both (keep it to two links total incl.
  LinkedIn). If unsure, the portfolio is the better default.

Keep LinkedIn in the body too (the linter requires it).

---

### 4e. FOLLOW-UP STRATEGY — different rules from cold emails

> Follow-ups go ONLY to people who have **not** replied. If someone replied, it's a human
> conversation now — already notified to Zineb in Step 1, never answered by the agent.

Follow-ups are NOT shorter cold emails. A follow-up that re-pitches is worse than silence.

**Hard limit: 40–60 words. Under 40 is often better.**
(The P.S. footer is auto-omitted on follow-ups — only the "Zineb Meftah" signature is
appended. So your 40–60 words are the whole email; keep them tight.)

❌ NEVER repeat the pitch — they read it already
❌ NEVER start with "Je reviens vers vous" or "Suite à mon précédent message"
❌ NEVER list skills or credentials again
❌ DO NOT include the LinkedIn link (already in the cold email)
❌ DO NOT re-pitch cost/AUA in a follow-up (redundant — follow-ups are short, new-signal only)

**Match the type to `followup_number`** (from `overdue_followups()`) — the sequence escalates, it
does not repeat. Each touch must add something the last one didn't; never send the same nudge twice.

**FU1 (`followup_number == 1`) — New signal** *(best: shows you're still watching them)*
Something changed at their company since your cold email — new feature, funding round, hiring page update, press mention.
> "J'ai vu que vous avez [specific new thing]. Ça rend ma question sur [topic] encore plus pertinente. Toujours partant pour 10 minutes ?"
> *(~25 words)*
> If you genuinely can't find a new signal, use the FU2 shape instead — never pad.

**FU2 (`followup_number == 2`) — New result from internship** *(shows progress)*
You shipped something concrete at GE HealthCare since the cold email. State the result only.
> "Depuis mon premier message : [specific new metric or milestone] chez GE HealthCare. Un échange rapide ?"
> *(~20 words)*

**FU3 (`followup_number == 3`) — Graceful last nudge** *(final touch — then stop)*
Acknowledge the silence, lower the bar to a one-word reply, and make clear it's the last time.
> "Je ne veux pas insister — un simple 'pas maintenant' et je vous laisse tranquille. Sinon, 10 minutes cette semaine ?"
> *(~22 words)*
> After FU3 the lead exits the sequence automatically (the log now has 4 Agent touches). Never a 4th.

**Subject**: `Re:` + a **trimmed** version of the original subject (keep the distinctive part, drop
the long "— alternance M1 IA 2026-2027" tail). It threads in their inbox and lifts the open rate;
a shorter Re: line reads less like an automated nudge.

**NEVER send more than 2 follow-ups** to the same address with no reply → mark Rejected.

---

**Replies (a human answered): the agent does NOT respond.** It was already notified to Zineb
via a `--kind alert` in Step 1. Leave the row as `Replied`, do not draft or send anything to
it, and do not count it against any cap. Zineb answers it personally.

---

### 4f. SAVE THE DRAFT
```bash
mkdir -p /path/to/stationf-agent/drafts/YYYY-MM-DD
```
Write the email body (and nothing else — no signature, no footer) to:
`drafts/YYYY-MM-DD/NN-KIND-COMPANY_SLUG.txt`
where NN = 01, 02, … and KIND = cold | followup.

**Then LINT it — this is a hard gate, not optional:**
```bash
python email_lint.py --kind KIND --subject "SUBJECT" --company "COMPANY" \
  --body-file drafts/YYYY-MM-DD/NN-KIND-COMPANY_SLUG.txt
```
- Exit 1 (any ❌ ERROR) → **do NOT send.** Rewrite the draft to fix every error, re-lint, repeat.
- Warnings (⚠️) → fix them too unless you have a deliberate reason not to.
- Only proceed to send once the linter exits 0.

The linter mechanically enforces the rules above (word count, no footer/signature in the draft,
no banned openers/subjects, LinkedIn on cold, cost/AUA never a standalone paragraph, etc.). If you disagree
with an error, the rule still wins — revise.

---

### 4f. BUILD THE CV (follow-ups only — NEVER on cold emails)

Cold emails have **no attachment** — attaching a PDF on first contact is a spam signal.

For **follow-ups**, build a role-adapted CV before sending using the `/cv-builder` skill:

```
/cv-builder --lang fr --focus FOCUS --role "ROLE_TITLE" --company "COMPANY"
```

Or just pass the company name and it will look up the role automatically:
```
/cv-builder COMPANY_NAME
```

The skill outputs the exact `--attach` flag to use. Use `--lang en` for English-language companies.

Additional documents to attach **only when explicitly requested** by the contact:
- Grades / academic record: `documents/Relevez_notes.pdf`
- Certificates: `documents/certificates.pdf`

---

### 4g. SEND
```bash
python smtp_send.py \
  --to "CONTACT_EMAIL" \
  --subject "SUBJECT" \
  --body-file drafts/YYYY-MM-DD/NN-kind-company.txt \
  --company "COMPANY" \
  --role "ROLE" \
  --kind cold|followup \
  --strategy X \
  --send
# --strategy X: the ONE strategy letter you picked (Q/O/V/M/U/A/G). REQUIRED on cold sends —
#   smtp_send records it automatically as "Agent (Strategy:X):" so the bandit remembers what was
#   tried. Do NOT hand-write the marker anywhere; the flag is the single source of truth.
# Cold emails: NO --attach flag
# Follow-ups: add --attach documents/CV_Zineb_Meftah_FR_custom.pdf (omit --strategy on follow-ups)
```

**Handle a deliverability refusal (anti-bounce).** `smtp_send.py` verifies before every send and
refuses anything it cannot justify. There are now **three** distinct refusals; they need different
responses, and **none of them is ever retried with the same address**.

> **Follow-ups are exempt from (a) and (b).** An address that already received a delivered email
> from us is proven to exist — better evidence than any API, since a bounce would have flipped the
> row to `Rejected` and blocklisted the address. So `--kind followup` to an already-emailed contact
> goes through even when verification is degraded. **Never skip a follow-up because Hunter is down**
> — on 2026-09-02 three follow-ups to previously-reached people were drafted and then dropped for
> exactly that reason, losing a day on the highest-converting channel. Refusal (c) still applies.

**(a) `unconfirmed personal mailbox [mx_only|api_risky] for name@domain …`** — a guessed
`firstname.lastname@` that could not be confirmed. Sending a wrong guess bounces and damages
deliverability for all future mail.
1. Re-send to the company's **generic inbox** `contact@<real-domain>` (same body — keep the
   "Bonjour [Prénom]," opener so it still reaches the person by name), `--kind` unchanged.
2. The **LinkedIn double-tap (4h) still reaches the named person directly**, so the person is
   covered on two channels without risking a bounce. Log the fallback on the row.

**(b) `unverified generic inbox [mx_only] for contact@domain …`** — NEW, and it means the
fallback in (a) is also unavailable for this company. `mx_only` proves only that the domain
resolves; the 2026-09 audit found all 55 August bounces were generic inboxes accepted on exactly
this signal. Usually it means the Hunter quota is spent, so nothing on this domain can be
confirmed today.
→ **Do NOT retry any address at this company. Skip the lead entirely.** Draft the LinkedIn note
  instead (that channel is unaffected), leave the row `Pending`, and move to the next lead. The
  lead is not lost — it becomes sendable again when Hunter quota resets. **A skipped lead does
  not consume a COLD_CAP slot**, so take the next-ranked lead and keep going until the cap is
  genuinely spent.

**(c) `recipient is on the hard-bounce blocklist: …`** — this exact address already hard-bounced
(`bounce_guard.py`). It is dead permanently.
→ **Never retry it, in any form.** If the row's address is blocklisted, the row needs a *different*
  address before it is workable: run `/find-contacts` for that company, or skip it. As in (b), this
  does not consume a COLD_CAP slot. If a generic local was blocklisted, other role inboxes on that
  domain (`jobs@`, `hello@`) are blocked too — the company runs none — but a **named personal**
  mailbox there is still allowed, so a real decision-maker is worth finding.

Check an address before composing, to avoid wasting a research pass on a dead lead:
```bash
python bounce_guard.py check "contact@example.com"    # exit 1 = blocked
```

After each successful send, wait before the next (spam-rate protection):
```bash
sleep 90
```

---

### 4h. LINKEDIN DOUBLE-TAP (same pass, cold sends to a NAMED person only)

Email and LinkedIn are two touches on the **same decision-maker**. A connection note can't land
in spam and is read far more often than cold email — so every cold email to a *named* person gets
a same-day LinkedIn note **drafted for Zineb to send by hand**. This roughly doubles the odds of a
reply at near-zero extra cost: you already did the research in 4b, so reuse that exact hook.

**Do this immediately after a successful cold SEND, but ONLY when:**
- the recipient is a **named** decision-maker (Contact Email has a display name `"Name (Title)" <…>`
  or a personal address — NOT a generic `contact@/hello@/jobs@` inbox; LinkedIn needs a person), AND
- no note was drafted for this lead already:
  ```bash
  python -c "import tracker; print(tracker.has_linkedin_touch('COMPANY','ROLE','CONTACT_EMAIL'))"
  ```
  Prints `True` → skip (already double-tapped). `False` → draft it.

**Write a SUBJECT + the note — reuse the 4b hook, don't re-research.** Same rules as
`/linkedin-draft`, tighter:
- **≤300 characters** for the note (LinkedIn's cap; aim < 250). Count before saving.
- Open on THEM (the researched hook), one proof point max (GE HealthCare + 1ère/126) woven in.
- **No links, no CV, no cost/AUA** (LinkedIn already shows her profile). Warm, peer-to-peer, specific.
- Soft close, not a hard ask: "…j'aimerais échanger avec vous là-dessus." French by default.
- **SUBJECT — ≤50 chars**, about THEM (the researched thing), sentence case, no emoji/caps/"!".
  Banned: "Candidature", "Alternance", "CV", "Opportunité", her own name — those read as a mass
  application. Don't restate the note's opening words. A connection *request* has no subject field;
  the subject is for when Zineb sends it as a message/InMail instead, so **the note must stand alone
  without it** (never open the note with "Objet :"). The subject is not counted in the 300.

**Capture a direct, VALID LinkedIn link for the person** so the alert is one-click for Zineb. The
link must always resolve — a broken link is worse than a search:
- **Only** use an exact profile URL if you saw it **verbatim in a real search result** during 4a/4b
  (copy it exactly, e.g. `https://www.linkedin.com/in/marie-dupont-4a8b21`). **NEVER guess or
  construct a `/in/firstname-lastname` slug** — LinkedIn slugs carry unpredictable suffixes, so a
  fabricated one 404s.
- Otherwise (the default), build a pre-filled people-search URL — always valid, lands on them in one
  click. URL-encode the name + company:
  ```bash
  python -c "import urllib.parse; print('https://www.linkedin.com/search/results/people/?keywords='+urllib.parse.quote('PERSON_NAME COMPANY'))"
  ```
The alert is a plain-text email; Gmail auto-linkifies a bare `https://…` URL, so paste it raw (no
markdown, no angle brackets) on its own after the `🔗`.

**Save** to `drafts/YYYY-MM-DD/NN-linkedin-COMPANY_SLUG.txt` (same `NN` as the email), with a header:
```
COMPANY:  <name>
PERSON:   <name + title>
ROLE:     <role>
LINKEDIN: <exact profile URL, or the people-search URL above>
SUBJECT:  <≤50-char subject — used only if she sends it as a message/InMail>
CHARS:    <character count of the note, subject excluded>
---
<the note>
```

**Record it — off-book (does NOT touch caps or the email follow-up timer):**
```bash
python -c "import tracker; print(tracker.note_linkedin_draft('COMPANY','ROLE','CONTACT_EMAIL'))"
```
This appends `Agent (LinkedIn): connection note drafted` to the log only — it never counts against
COLD/WARM caps and never resets `Last Interaction Date`, so the email follow-up sequence is unaffected.

**Never send it** (LinkedIn bans automation). Zineb sends the ones she likes by hand — see the batch
alert in Step 5. If the cold email went to a **generic inbox** (no named person), skip the double-tap.

---

## STEP 5 — END-OF-RUN SUMMARY

**First, if any LinkedIn notes were drafted this run, send ONE batch alert** so Zineb can send them
by hand (they are not auto-sent):
```bash
python smtp_send.py \
  --to you@example.com \
  --subject "[LINKEDIN] N notes ready to send manually — $(date +%Y-%m-%d)" \
  --kind alert \
  --body "N LinkedIn notes drafted today (same people just emailed). For each: click the link, then
either send a connection request with the note, or — if you can message them — use the 📌 subject with it.

═══════════════════════════════════
COMPANY — Person (Title)
🔗 <exact profile URL, or the people-search URL>
📌 <the subject, ≤50 chars — for the message/InMail form only>
✉️ <the FULL note, verbatim, ready to paste>
═══════════════════════════════════
<...repeat one block per note...>

A connection request has no subject field — in that case ignore the 📌 line and send the note alone;
it reads fine on its own. LinkedIn forbids automation, so these are draft-only. Send the ones you
like, skip the rest.
Files: drafts/$(date +%Y-%m-%d)/" \
  --send
```
(`--kind alert` → raw, not logged, not counted.)

Then print:
- Date + run mode
- Actions taken: company / kind / recipient / subject / status
- LinkedIn notes drafted this run (company / person) + that the batch alert was sent
- Alerts sent (if any)
- Remaining daily cap
- Any errors or skips with reason
