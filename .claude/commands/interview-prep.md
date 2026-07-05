Generate a tailored interview-prep sheet for a company Zineb is interviewing with — her proven format, filled from research. Converting a reply into an interview into an OFFER is where the alternance is actually won; this is the highest-leverage step after someone says yes.
Working directory: /path/to/stationf-agent

`$ARGUMENTS` = the company name (required), optional `--lang en`, optional `--interviewer "Name (Title)"`, optional `--role "TITLE"`.

Read CLAUDE.md and about_me.txt (especially the PROJECT MATCHING GUIDE at the bottom) before writing.
All python commands must use the venv: prefix every `python ...` with
`source /path/to/stationf-agent/venv/bin/activate && `.

Output language: **French by default** (match the company / the thread's language); `--lang en` → English.

---

## STEP 1 — PULL WHAT WE ALREADY KNOW

```bash
source /path/to/stationf-agent/venv/bin/activate && python -c "
import tracker, json
df = tracker.load()
m = df['Company'].astype(str).str.strip().str.lower() == 'COMPANY'.lower()
for _, r in df[m].iterrows():
    print(json.dumps({k: str(r[k]) for k in df.columns}, ensure_ascii=False, indent=2))
"
```
- Read the **Role**, the **Contact Email** (named interviewer?), and the **Conversation Log** — the log
  often contains the reply, the interviewer's name/title, and how the intro happened (referral?).
- Pull the cached hook-fact (what they build) so you don't re-research from cold:
  ```bash
  python -c "import lead_facts, json; r=lead_facts.get('COMPANY'); print(json.dumps(r, ensure_ascii=False) if r else '(none)')"
  ```

If the company isn't in the tracker, proceed on `$ARGUMENTS` alone (she may be interviewing off-book).

## STEP 2 — RESEARCH (company + interviewer + role)

Be thorough — this sheet is only as good as the specifics in it.
1. **Company**: what they build, their stack, recent launches/funding, the hard technical problem in
   their domain. WebSearch + WebFetch their site/careers/eng-blog.
2. **The role**: re-read the posting if it's linked; note the concrete responsibilities and must-haves.
3. **The interviewer** (if named): WebSearch `"<Name>" "<Company>" linkedin` — their background (PhD?
   ex-founder? which team?) shapes what they probe. A PhD probes depth; a founder probes autonomy
   & ownership; a recruiter probes fit & logistics. Note it — it changes the whole sheet.

## STEP 3 — PICK HER STRONGEST PROOF FOR **THIS** COMPANY

Use the **PROJECT MATCHING GUIDE** in `about_me.txt`. Choose ONE lead project that matches their
actual domain (never default to "multi-agent RAG" for everyone):
- Healthtech / medical imaging → GE HealthCare RAG **and** the CT-scan classification project.
- AI agents / dev tools / autonomous systems → the StationF outreach agent (she built & runs it) +
  "I already build *with* agentic tools (Claude Code) daily — I have a real opinion, not theory."
- LLM / retrieval / RAG → GE HealthCare tiered retrieval (BM25 → cross-encoder reranker → LLM),
  prompt caching (~80% cost), semantic dedup.
- Data / analytics → the dedup / precision-recall calibration angle.
- Robotics / RL / CV → LeRobot (ACT) and Robot Vision Sim.
Map her proof to what THEY need — that mapping is the core of the sheet.

## STEP 4 — WRITE THE PREP SHEET

Write to `interview_prep/COMPANY_SLUG_prep.md` (mkdir the folder). Follow **this proven structure**
(it mirrors the sheets Zineb writes by hand — keep it concrete, in her voice, French by default):

```
# 🎯 Fiche entretien — <Role> · <Company> [· alternance M1 IA 2026-2027]

**Interlocuteur·rice :** <name + title, + background: PhD / founder / recruiter — what they probe>
**Canal :** <cold / referral by NAME / LinkedIn — note if it was a warm intro, it changes the tone>
**Ce qu'ils construisent :** <one precise sentence from research>
**Mon edge ici :** <the one-line reason she's uniquely strong for THIS company>

## 1. Ce qu'ils évaluent (donc ce que je dois montrer)
<4-6 bullets inferred from role + interviewer + company — depth? autonomy? domain fit? fiability over the year?>

## 2. Ma preuve n°1 pour eux — <the matched project>
<describe it precisely, in her words, mapped to their domain — this is the anchor of the interview>
### Schéma à dessiner (si screen-share / tableau)
<a simple ASCII architecture diagram she can reproduce — like her GE example>

## 3. War stories (2-3) — format: contrainte → mon contournement → qualité/coût/latence préservés
<2-3 concrete problem→solution stories from her real work; mark any "[à remplir avec le vrai détail]"
 she must fill with specifics only she knows — that authenticity is the proof>

## 4. Le pont honnête vers LEUR sujet
<how her real experience bridges to exactly what this role needs — no overclaiming>

## 5. Mes autres projets pertinents (1 ligne chacun)
<2-4 other projects that matter for THIS company, one line each>

## 6. Pourquoi ils ont besoin de moi — l'intersection que personne d'externe n'a
<the unique combination she brings; then 2-3 sentences of "comment j'aide concrètement" to say aloud>

## 7. Mes phrases clés (à dire naturellement)
<3-5 quotable lines in her voice she can drop naturally>

## 8. Questions intelligentes à LEUR poser (montrent que j'ai réfléchi)
<4-5 sharp, specific questions about their stack/roadmap/team — never generic; a great question is
 as memorable as a great answer>

## 9. Logistique — l'ask, sans hésiter
- Contrat : <lead with the fit — alternance M1 à partir de septembre 2026 ; ouverte CDI/CDD si besoin>.
- Rythme d'alternance : <typical 3-4 j entreprise / 1-2 j école — say she'll confirm the school calendar>.
- Dispo : septembre 2026. Master IA validé (<name the accepted M1 if relevant>).
- <If clearly a small startup (<250): ONE calm line that an alternance d'apprentissage is light to
  set up (AUA + exonérations) — never lead with cost, never a figure-dump. Drop it for a big group.>

## 10. Questions techniques probables + réponses courtes
<5-8 likely technical questions for THIS role, each with a crisp 1-2 line answer in her register>

## ⚠️ Pièges à éviter
<3-4 specific traps: e.g. don't oversell the RAG angle if they're not RAG; don't read the CV aloud;
 match depth to the interviewer; have real numbers ready; be honest about what she hasn't done yet>

## ✉️ Après l'entretien — mot de remerciement (à envoyer < 24h)
<A ready-to-send thank-you note (≤80 words, her voice). A note within 24h is standard and lifts the
 decision. It must: thank them briefly, reference ONE specific thing discussed (proof she listened),
 reaffirm the fit in one line, and leave the door open. NOT a re-pitch. Leave a [détail précis de
 l'échange à insérer] slot she fills with something real from the conversation.>
> Ex (FR) : "Merci pour l'échange — j'ai beaucoup aimé creuser [sujet précis discuté]. Ça confirme
> que [le lien avec son projet] : c'est exactement le type de problème que je veux approfondir en
> alternance avec vous. Au plaisir d'échanger sur la suite."

## 📌 Suivi
- Prochaine étape attendue : <what they said comes next + when>. Si pas de nouvelle sous ~[X jours],
  relancer poliment (le détecteur `stalled_conversations` + /followup-check le signaleront aussi).
```

**Rules for a great sheet:**
- Every section must be **specific to this company** — the blank-company test applies here too.
- Her voice: confident peer, honest about limits, concrete over adjectives. Never invent experience.
- Mark anything she alone can fill (`[à remplir : le vrai détail]`) rather than fabricating a number.
- If she came in via a **referral**, say so at the top and set a warmer, less "prove-myself" tone.

## STEP 5 — ALERT HER THAT IT'S READY

```bash
python smtp_send.py --to you@example.com \
  --subject "[INTERVIEW PREP] <Company> — <Role> — fiche prête" --kind alert \
  --body "Fiche de révision entretien prête : interview_prep/COMPANY_SLUG_prep.md

Interlocuteur : <name/title>
Preuve n°1 choisie : <the matched project>
À remplir toi-même : <list the [à remplir] spots — the real details only you have>

Relis-la, complète les détails réels, et dis-moi si tu veux que j'ajuste l'angle." --send
```
(`--kind alert` → raw, not logged, not counted against any cap.)

Report to the console: the file path, the interviewer, the chosen lead project, and the spots she must fill herself.
