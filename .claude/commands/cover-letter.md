Generate a tailored lettre de motivation (cover letter) for an alternance application — for the leads the agent routes to a careers portal / ATS, and for any company, school or CFA that asks for one. This is the application-stage channel the cold-email flow otherwise abandons ("go apply" with no support).
Working directory: /path/to/stationf-agent

`$ARGUMENTS` = company name (required), optional `--lang en` (default French), optional
`--role "TITLE"`, optional `--focus ai|backend|mlops|data|fullstack`.

Read CLAUDE.md and about_me.txt (especially the PROJECT MATCHING GUIDE) before writing.
All python commands use the venv: prefix with
`source /path/to/stationf-agent/venv/bin/activate && `.

Output language: **French by default** (most alternance applications are FR); `--lang en` → English.

---

## STEP 1 — CONTEXT

```bash
source /path/to/stationf-agent/venv/bin/activate && python -c "
import tracker, json, lead_facts
df = tracker.load(); m = df['Company'].astype(str).str.strip().str.lower()=='COMPANY'.lower()
print('ROW:', json.dumps(df[m].to_dict('records')[:1], ensure_ascii=False, default=str))
print('HOOK:', json.dumps(lead_facts.get('COMPANY'), ensure_ascii=False) if lead_facts.get('COMPANY') else '(none)')
import school_partners; print('SCHOOL:', school_partners.summary('COMPANY') or '(not a school partner)')
"
```
**If `SCHOOL:` shows a partnership**, that is your STRONGEST angle for this letter (especially for a
big group): they recruit alternants from Zineb's own M1 program via the CFA. Lead the accroche on it —
FR: "*Je rejoins le M1 [programme] à la rentrée 2026, dont [Company] est partenaire via le CFA numiA —
c'est naturellement vers vous que je me tourne pour mon alternance.*" — then prove fit as below.

Note the **Role** (and any real job description if the posting is linked). Then research the company
(what they build, their stack, the concrete problem in their domain) so the letter is specific.

## STEP 2 — PICK THE MATCHED PROOF

Use the PROJECT MATCHING GUIDE in `about_me.txt` — choose ONE lead project that fits their actual
domain (healthtech → GE RAG + CT-scan; AI agents/dev-tools → the outreach agent; retrieval/LLM → GE
tiered retrieval; robotics/CV → LeRobot / Robot Vision Sim). Never default to "multi-agent RAG" for all.

## STEP 3 — WRITE THE LETTER

A lettre de motivation, **modern — not the stiff templated kind**. ~250–330 words, 3-4 short
paragraphs, her voice: confident, specific, warm. Structure:

- **Objet:** `Candidature en alternance — <Role> (M1 IA, rentrée septembre 2026)`.
- **Accroche (P1) — on THEM.** Open on something specific about the company (a product, a technical
  choice, the problem they solve) — NOT "Je me permets de vous adresser ma candidature." Show in the
  first sentence that she actually looked at what they do.
- **La preuve (P2) — fit by evidence.** The matched project described concretely (GE HealthCare result
  / the agent / the domain project) mapped to what THIS role needs. Weave in Major de promotion
  (1ère/126) and the GE HealthCare internship as proof, never as a list. Show, don't tell.
- **Pourquoi l'alternance + pourquoi eux (P3).** Master IA à partir de septembre 2026; the alternance
  is her structured path; ONE honest line on why this company specifically. Signal openness to CDI/CDD
  only if it fits — never a menu of three.
- **Clôture (P4) — confiante, simple.** A forward-looking close and availability (September 2026).
  A normal French sign-off ("Je vous prie d'agréer, Madame, Monsieur, mes salutations distinguées.")
  is fine here (unlike cold email) — it's a formal application.

**Hard rules (reuse the email quality bar):**
- No clichés ("acteur majeur", "leader", "passionnée par", "rejoindre votre équipe", "vos valeurs").
- No generic flattery, no skill dumps, no AI-cadence tells (stacked em-dashes, "X, Y et Z" triads).
- Every claim backed by a concrete result/architecture, not adjectives. The blank-company test applies:
  remove the company name and it should collapse.
- If it's a small startup (<250) AND alternance is the ask, ONE calm embedded clause on the aide
  à l'apprentissage is OK — never a paragraph, never a figure-dump. Big group / pure CDI → drop it.

## STEP 4 — SAVE

```bash
mkdir -p /path/to/stationf-agent/cover_letters
```
Write to `cover_letters/COMPANY_SLUG_LM.md` with a 2-line header (Company · Role · date) then the
letter body, ready to paste into a portal field or drop into a document. Note at the bottom: "Pour un
PDF formel, coller dans un modèle Word/LaTeX à l'en-tête de Zineb."

## STEP 5 — REPORT

Print the file path, the role, the matched project used, and a one-line note on where it fits (portal
application / school / CFA / company request). Do not email anything — this is a document for Zineb to
submit herself.
