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

This prints `{'cold': N, 'warm': N}`. Compute:
- `cold_remaining = 2 - cold`   ← COLD_CAP = 2 (new first-contact emails)
- `warm_remaining = 3 - warm`   ← WARM_CAP = 3 (follow-ups + replies)

If both caps are 0: print a summary and stop.

---

## STEP 3 — BUILD PRIORITY QUEUE

Collect candidates in two separate pools:

**WARM pool** (counts against `warm_remaining = 3`) — **FOLLOW-UPS ONLY**:
- Status == `Replied` rows are **NOT** in this pool. A human answered → it was already notified
  to Zineb in Step 1 and is hers to handle. The agent never auto-answers a reply.
- **Follow-ups**: Status == `Emailed` AND Last Interaction Date is more than 4 **business days** (Mon–Fri) before today.
  **Before adding any follow-up**, verify the email is still reachable:
  ```bash
  python /path/to/stationf-agent/email_verify.py "CONTACT_EMAIL"
  ```
  - Exit 1 (`unverifiable`) → mark `Rejected`, skip.
  - Exit 0 → include normally.

**COLD pool** (counts against `cold_remaining = 2`):
- **P3**: Status == `Pending` (first-contact emails). If fewer than 5 Pending rows exist, run `python scraper.py` to replenish first.
- **Only 2 cold slots/day — spend them on the BEST leads, not the first ones.** Get the ranked shortlist:
  ```bash
  python -c "import tracker, json; print(json.dumps(tracker.rank_pending_leads(limit=8), indent=2, default=str))"
  ```
  This scores every Pending row by role-fit + contract match + deliverability (named contact) + speculative bonus, and applies an **over-contact cooldown**: any lead whose `on_cooldown` is true (its company's domain was already emailed in the last 7 days) is heavily penalised — **do NOT cold-email it**, pick the next. This prevents hitting the same company twice in one week when it has several open roles. Fill the cold slots from the TOP of this list. Skip a top lead only if its email is unreachable or you can't find a specific hook for it (then take the next).

**Queue construction**:
1. Fill warm slots: take up to `warm_remaining` follow-ups (Replied rows are excluded — notify-only).
2. Fill cold slots: take up to `cold_remaining` items from the TOP of `rank_pending_leads`.
3. Deduplicate by Contact Email (same address → keep highest priority).

**Never mix the pools** — a day with 3 warm sends and 0 cold is fine. A day with 3 cold sends is not.

---

## STEP 4 — EXECUTE EACH ACTION

For each item in the queue:

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

Each email must be written for this specific company, not adapted from a template. Before opening a text editor, spend 2–3 minutes on the company:

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
❌ NEVER write the financial/AUA info as its own paragraph — it must be a single clause embedded inside another sentence (see Finance section below)
❌ NEVER use subject lines like: "Candidature alternance", "Candidature Analytics Engineer", "Ma candidature"
❌ NEVER write a list of Zineb's skills — weave them into the narrative
❌ NEVER exceed 110 words in the body (cold) or 65 words (follow-up) — cut ruthlessly
❌ NEVER use "pipeline multi-agent RAG" as the credential if the company has nothing to do with RAG/document AI — match the right project to their actual domain (see project matching below)
❌ NEVER write two consecutive cold emails with the same paragraph structure — check the last draft in `drafts/` before writing

✅ First sentence is always about THEM, based on specific research from step 4b
✅ Subject line must be specific enough that only someone who actually looked at their product could write it
✅ Every sentence must earn its place — if removing it wouldn't hurt the email, remove it

---

#### CONTRACT TYPE — choose the ask based on the opportunity (do this FIRST)

Zineb is open to three contract types. Her order of preference: **CDI > CDD > Alternance** —
but alternance is her structured path (she starts a Master IA in Sept 2026). The contract you
request depends on what the company posted.

🚫 **NEVER list all three as a menu** ("je cherche un CDI, CDD ou alternance"). That reads as
desperate and unfocused. Lead with the ONE that fits the posting; signal openness to the
others in a single confident clause, only when it adds value.

First, detect the posting's contract type:
```bash
python -c "import config; print(config.guess_contract_type('ROLE_TITLE_HERE'))"
```
Returns: `alternance` | `stage` | `cdi` | `cdd` | `speculative` | `unspecified`

Then pick the ask:

**`alternance` posting → direct match.** Apply for the alternance, hint at staying long-term.
> FR: "Je postule à votre alternance — et l'idée de m'inscrire dans la durée chez vous m'attire."
> EN: "I'm applying for your work-study role — with the goal of building something lasting with you."
> ✅ Keep the AUA/cost clause (it applies).

**`cdi` / `unspecified` posting → the reframe (strongest move).** They want a permanent hire.
Offer alternance as the lower-risk, lower-cost version while staying genuinely open to the CDI.
> FR: "Vous recrutez un·e [role]. Je démarre un Master IA en septembre — donc flexible sur le
> format : en alternance, le même profil 3–4 j/semaine à ~400–700€/mois avec une année pour
> valider avant un CDI ; ou un CDD/CDI directement si vous préférez un temps plein."
> EN: "You're hiring a [role]. I'm starting a Master's in AI this fall, so I'm flexible on
> format: as a work-study, the same profile 3–4 days/week at a fraction of the cost with a year
> to prove fit before a permanent role — or a fixed-term/permanent contract if you'd rather."
> ✅ Fold the cost numbers INTO this reframe — do not add a separate finance paragraph.

**`cdd` posting → apply for the CDD, mention alternance as a cheaper long-term option.**
> FR: "Votre [role] en CDD m'intéresse. Je suis aussi ouverte à une alternance M1 si vous
> voulez construire dans la durée à moindre coût."

**`stage` posting → upsell to alternance** (she's already finishing her L3 graduation internship).
> FR: "Vous proposez un stage — je termine justement le mien chez GE HealthCare. Pour 2026-2027
> je vise une alternance M1 ; si vous êtes ouverts à ce format, j'aimerais en discuter."

**`speculative` (no posting) → alternance-first, open to the rest.**
> FR: "Je cherche une alternance M1 à partir de septembre 2026 — ouverte à un CDD ou CDI selon
> votre besoin."

The flexibility clause appears **once**, near the CTA. Never twice, never as a list.

---

#### PICK ONE STRATEGY — self-improving (multi-armed bandit)

The agent learns which strategies actually earn replies. Before choosing, get the recommendation:
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
The outreach agent IS the demo. Lead with what she built to send this very email.
> Subject: `"J'ai déployé un agent IA autonome pour trouver cette opportunité chez [Company]"`
> Opening: `"Ce message a été ciblé, rédigé et envoyé par un agent IA autonome que j'ai conçu : scraping Playwright du board Station F, qualification LLM des opportunités, personnalisation par profil d'entreprise, envoi SMTP — en production depuis plusieurs semaines. [Company] construit [what they build] — c'est précisément ce type de système que je veux construire avec vous."`
> Why it works: the email itself is the portfolio. For any company building AI agents or autonomous systems, this lands harder than any credential.

**Strategy G — Insight Gift** *(give-first; the highest-reply-rate play when you can find something genuinely useful)*
Lead by GIVING, with no ask up front. Offer one concrete, specific thing of value: a small
improvement you noticed, a relevant approach/paper, a sharp take on a problem they're visibly
solving. The ask comes last, almost as an afterthought. Triggers reciprocity + proves competence.
> Subject: `"Une idée sur [specific thing] chez [Company]"`
> Opening: `"En testant [their product/feature], j'ai remarqué que [specific, real observation] — une piste : [concrete, useful suggestion you'd actually implement]. C'est le genre de problème que j'ai résolu chez GE HealthCare sur [analogous case]."`
> Close (soft): `"Si ça vous parle, je serais ravie d'en discuter — et je cherche justement une alternance/CDI M1 dans cette direction."`
> Why it works: you're the rare person who gave before asking. Even a "no" often comes with thanks + a door left open. ONLY use when the insight is genuinely good — a fake/generic "tip" backfires badly.

---

#### TAILOR THE EMAIL TO THE RECIPIENT — a CTO and a recruiter need different emails

You know the recipient's role (from the named contact). Match the email to what THEY care about:

- **Founder / CTO / Head of AI / tech lead** → they care about the *substance*. Go technical and
  specific (Strategies Q, O, M, A, G). Reference their architecture/product. Peer-to-peer tone —
  write as a fellow builder, not an applicant. Credentials light; let the technical hook carry it.

- **Head of Talent / Recruiter / Campus / HR** → they care about *fit & logistics*, not your reranker.
  Lead with: right profile for [the role they're filling], concrete proof (1ère/126 + GE HealthCare),
  availability (Sept 2026), contract flexibility (CDI/CDD/alternance + the cost angle), and *why this
  company specifically*. Strategies V or U work best. Keep it scannable. NO deep architecture talk.

- **CEO of a non-technical / small startup** → business value, not tech internals. What Zineb can
  *build for them* and the low cost (alternance). Strategy V or M.

If you don't know the role, default to the technical/peer register (most Station F contacts are technical).

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

**Fixed spine, variable hook.** Vary the *layout and the opening hook* every time — but the
*spine stays constant*: a concrete proof point (GE HealthCare result + 1ère/126), the ask
(contract + availability Sept 2026), and the links. Only the first 1–2 lines (the researched
hook) truly change per company. Don't regenerate the whole email from scratch chasing novelty —
that's what drifts back into generic; keep the proven spine and swap the hook.

The 5 elements (hook, bridge, credentials, finance, CTA) do NOT have to appear in that order. Valid structures:

- **Dense 2-paragraph**: Hook+bridge fused in P1, credentials+finance+CTA fused in P2
- **Single block**: Everything in one tight paragraph, CTA as a standalone line
- **Question-first**: Open with question, answer it with Zineb's experience, close with ask
- **Result-first**: Lead with the result, explain why it's relevant to them, close with ask

Before writing: check `drafts/YYYY-MM-DD/` for the most recent cold email. If it uses P1+P2+P3 structure → do NOT use that structure. If it opens with a question → do NOT open with a question.

---

#### MANDATORY SELF-CHECK before saving the draft

Run through this mentally before every send:

1. **The blank-company test**: Remove the company name from the email. Does it still work? If yes → the hook is not specific enough → rewrite.
2. **"Je" count**: Count how many times "Je" appears. If more than 2 → the email is too self-centered → cut.
3. **Finance paragraph check**: Is the cost/AUA info in its own paragraph? If yes → it must be merged into another sentence. No exceptions.
4. **Name check**: Does the body end with "Zineb" or "Zineb Meftah"? → Delete it.
5. **Word count**: Cold > 110 words or follow-up > 65 words → cut until under limit.
6. **Subject line test**: Could this subject line have been written without reading about the company? If yes → rewrite.
7. **First line test**: Does the opening sentence start with "Je"/"J'" or describe Zineb? → rewrite so it opens on THEM (the researched fact).
8. **Cliché test**: Any generic flattery — "votre entreprise/société", "acteur majeur", "leader dans", "passionné(e) par", "rejoindre votre équipe", "vos valeurs"? → delete and replace with something specific and true.
9. **CTA test**: Is there exactly one low-friction question (ending in "?") that makes replying effortless? If none → add one; if several asks → keep the easiest.
10. **Show-don't-tell test**: Are competence claims backed by a concrete result/architecture rather than adjectives ("innovant", "rigoureuse")? Replace adjectives with proof.

---

#### GOVERNMENT AID — one embedded clause, never a standalone paragraph

**Two gates — BOTH must hold, or drop the clause entirely:**
1. **Alternance is part of the ask** (alternance, cdi-reframe, cdd-with-alternance-option,
   stage-upsell, speculative). For a pure CDI or pure CDD ask with NO alternance mentioned, the
   AUA doesn't apply → drop it, lead with value instead.
2. **The company is an SME/startup (< 250 employees).** The *aide unique à l'apprentissage* is
   **legally restricted to employers under 250 salariés** — quoting the 6 000 € / 400–700 €
   figures to a large company is factually wrong and reads as a canned template. If the company
   is clearly large (big corp, listed group, or you can infer ≥250 from LinkedIn/their site) →
   **drop the AUA clause** and lead with fit + value + availability instead. When in doubt for a
   Station-F-scale startup, it's almost certainly < 250 → keep it.

The contact probably doesn't know these numbers. One clause, naturally embedded. Never its own paragraph.

Facts:
- AUA: jusqu'à **6 000 €** la 1ère année (entreprises < 250 salariés)
- Charges patronales: quasi-nulles
- Coût réel: **400–700 €/mois** pour l'entreprise

**Bad** (standalone paragraph — FORBIDDEN):
> "Pour votre taille, une alternance d'apprentissage coûte souvent moins de 700 €/mois réel : l'AUA (jusqu'à 6 000 € la 1ère année pour les moins de 250 salariés) et les exonérations quasi-totales font qu'un alternant M1 revient 3 à 4× moins cher qu'un CDI."

**Good** (embedded in the CTA sentence):
> "Je cherche une alternance M1 à partir de septembre 2026 — et en contrat d'apprentissage, le coût réel pour vous tourne souvent autour de 400–700 €/mois (AUA + exonérations). 10 minutes cette semaine ?"

**Good** (embedded mid-email):
> "...ce qui, en alternance d'apprentissage, représente un coût réel souvent inférieur à 700 €/mois pour votre équipe — soit bien moins qu'un junior en CDI."

The phrasing must change every email. Never copy-paste the previous wording.

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
- Tone: confident peer, never student begging. Write as if Zineb is doing them a favour by applying.
- Warm and direct — no "je me permets", no excessive politeness, no filler.
- Log the strategy used in the Conversation Log **exactly** as: `[date] Agent (Strategy:X): subject`
  (the colon after "Strategy" and the single letter are mandatory — the analytics parser relies on this format)
- **Do NOT** write the P.S. footer, signature, or mention the CV — all added automatically.

### 4d. LINKEDIN LINK — cold emails only

Every cold email body must include Zineb's LinkedIn URL as a natural inline mention — not a separate line, embedded in the flow:

> "…vous pouvez retrouver mes projets sur [linkedin.com/in/zineb-meftah](https://www.linkedin.com/in/zineb-meftah)."

or at the end of the credentials sentence:

> "…Major de promotion L3 IA Avignon (1ère/126) — [linkedin.com/in/zineb-meftah](https://www.linkedin.com/in/zineb-meftah)."

Do NOT write it as a standalone paragraph or label it as "Mon LinkedIn :". One inline hyperlink, that's it.

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
❌ DO NOT include the finance/AUA info again

**Pick one of three follow-up types:**

**F1 — New signal** *(best: shows you're still watching them)*
Something changed at their company since your cold email — new feature, funding round, hiring page update, press mention.
> Subject: `Re: [original subject]`
> "J'ai vu que vous avez [specific new thing]. Ça rend ma question sur [topic] encore plus pertinente. Toujours partant pour 10 minutes ?"
> *(~25 words)*

**F2 — New result from internship** *(second best: shows progress)*
You shipped something concrete at GE HealthCare since the cold email. State the result only.
> Subject: `Re: [original subject]`
> "Depuis mon premier message : [specific new metric or milestone] chez GE HealthCare. Un échange rapide ?"
> *(~20 words)*

**F3 — Reframe the ask** *(when no new signal — reduce friction)*
Acknowledge the silence, lower the bar to reply.
> Subject: `Re: [original subject]`
> "Pas de réponse — peut-être que le timing n'est pas bon. Un simple 'pas maintenant' me suffit. Sinon, 10 minutes ?"
> *(~22 words)*

**Subject**: Always `Re:` + original subject. It threads in their inbox and has a higher open rate.

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
no banned openers/subjects, LinkedIn on cold, finance folded into a clause, etc.). If you disagree
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
  --send
# Cold emails: NO --attach flag
# Follow-ups: add --attach documents/CV_Zineb_Meftah_FR_custom.pdf
```

After each successful send, wait before the next (spam-rate protection):
```bash
sleep 90
```

---

## STEP 5 — END-OF-RUN SUMMARY

Print:
- Date + run mode
- Actions taken: company / kind / recipient / subject / status
- Alerts sent (if any)
- Remaining daily cap
- Any errors or skips with reason
