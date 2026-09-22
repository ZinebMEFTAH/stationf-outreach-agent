Find every alternance Zineb could take, order them best-for-her first, then work down the list
one at a time — building a CV and a lettre de motivation tailored to each posting — until none is
left. This is the ON-DEMAND replacement for the scheduled digest: she runs it when she is at the
machine, with Claude present, and does not want it on a cron.

Working directory: /path/to/stationf-agent
All python commands use the venv: prefix with
`source /path/to/stationf-agent/venv/bin/activate && `.

`$ARGUMENTS` — optional. Empty means "build the queue and show me the best ones". It may also be
a company name ("next one", "Groupe SII"), in which case skip to STEP 3 for that lead.

---

## THE CONTRACT WITH HER — do not make her repeat any of this

- **She says `/apply`.** Claude does everything below without asking for instructions again.
- **Volume is the goal.** Her words: *"the goal is to candidate for the most possible from the
  most suitable to the least till it becomes not suitable at all"*, and *"never tell me there is
  nothing more ! if u do not find improve ur way of searching"*. France rewards volume. A thin
  result is a bug in the search, not a fact about the market.
- **One at a time.** Present the best, build its pack, hand it over. When she says **"this one is
  finished"** (or "done", "j'ai postulé"), LOG IT (STEP 5) and move to the next immediately.
- **Never suggest a posting she has already applied to.** That is what the log is for, and it is
  enforced in code — but only if STEP 5 is actually run.

---

## STEP 0 — WHAT IS ALREADY IN FLIGHT (10 seconds, do it first)

```bash
python applications.py due
```

Applications still silent after `FOLLOWUP_DAYS` business days. **Chasing one of these is cheaper
than finding a new lead** — the company already wanted candidates and already has her file. If
any are listed, say so before building anything new and offer to draft the follow-up.

When she reports an outcome ("they answered", "refusée", "j'ai un entretien"):
```bash
python applications.py status "COMPANY" "ROLE" replied|interview|rejected|offer|ghosted "note"
```
Loose wording is fine — it matches the logged row rather than requiring the exact title.

## STEP 1 — BUILD THE QUEUE

```bash
python - <<'PY'
import json, pathlib, brief, opportunities as opp
pool = opp._fetch_all()                      # every source, deduped by leadset
got  = brief.build(pool, verify_top=25)      # read descriptions, verify the head against the ATS
pathlib.Path("cache/queue.json").write_text(json.dumps(
    [{"lead": q["lead"], "signals": q["signals"], "tier": q["tier"]} for q in got["queue"]],
    ensure_ascii=False, default=str), encoding="utf-8")
st = got["stats"]
print(f"queue {st['queue']} (T1 {st['tier1']} T2 {st['tier2']} T3 {st['tier3']}) "
      f"refused {st['refused']} retryable {st['retryable']}")
for q in got["queue"][:15]:
    print(brief.render(q), "\n")
PY
```

⚠ **If `retryable` is not ~0, RUN IT AGAIN before judging the tail.** It means a board throttled
us mid-run and those leads are unread — they are not weak, they are unmeasured. The description
cache makes the second pass nearly free.

⚠ This takes several minutes (it sweeps every board). Run it in the background and keep working.

## STEP 2 — JUDGE, DO NOT JUST RANK

The queue is ordered by *evidence*, not by suitability — that judgement is Claude's, and it is
the whole reason this is interactive. Read the briefs and pick, weighing:

- **Is it really engineering?** The discriminator is an ABSENCE: a posting whose AI content is
  ChatGPT and prompting and which names **no programming language** is an adoption role, not an
  engineering one (Docaposte vs Veolia, near-identical titles).
- **Does her experience transfer?** GE HealthCare IA & MLOps, the outreach agent, LeRobot, the
  compiler, the Hugging Face research — see `about_me.txt` PROJECT MATCHING GUIDE.
- **Does the employer already want an alternant?** Explicit alternance beats one to be persuaded.
- **Warm contact** (⚡) and **CFA numiA partner** (🎓) dominate — they are the difference between
  an application read by a person and one read by a filter.
- **Level**: `m1_ok` is an invitation. `m2_or_final` is a flag to weigh, never a refusal — plenty
  of two-year alternances start in M1 and still write "dernière année"/"Bac+5".
- **Source reliability**: a HelloWork/Adzuna row is a LEAD TO VERIFY, not a job.

Show her 3-5 with one line each on *why*, and name your pick. Then go.

## STEP 3 — VERIFY IT IS STILL OPEN, BEFORE SPENDING THE WORK

A pack is ~10 minutes and one was once built for an AP-HP job that no longer existed.

```bash
python ats.py verify "COMPANY" "ROLE"
```

- `live` → go.
- `gone` → drop it, say so, take the next.
- `unknown` → most large French employers (Taleo/SuccessFactors/iCIMS/Avature) cannot be read.
  Check the posting date instead, and **tell her to open the apply button before you build**.

## STEP 4 — BUILD THE PACK. THIS IS THE CAREFUL WORK

Her instruction, verbatim: *"for the cv and cover letter, doing them u should think of all angles
so i get best chances (that is a big and very important work that u should do it very
carefully)"*. Budget real effort here; this step decides the outcome.

**First, save the posting** so the build is reproducible and the CV can select against its words:
```bash
python -c "import descriptions as D, pathlib; \
  pathlib.Path('offers/SLUG.txt').write_text(D.fetch({'url':'URL'})['text'], encoding='utf-8')"
```

**Then read the whole posting yourself** and list, explicitly: what it asks for · what she has ·
what she does NOT have. The gap list is as important as the match list.

**CV:**
```bash
python cv_builder.py --lang fr --focus ai|backend|mlops|data|fullstack \
  --role "TITLE" --offer offers/SLUG.txt [--headline "..."]
```
- `--offer` orders the skills and picks which project blocks survive the one-page fit.
- The alternance rhythm is appended in code — never hand-write it. It is the FULL rhythm
  now: 3j université / 2j entreprise, **puis temps plein en entreprise dès avril**, which
  is the half an employer actually weighs (official fiche de formation).
- Verify, never eyeball: `pdftotext ... - | head` and check the links extract.
- ⚠ `&` must be `\&` in `--headline`; an em-dash is silently dropped — use `{\color{gold}$\cdot$}`.

**Letter** — follow `.claude/commands/cover-letter.md` in full. Non-negotiables:
- ~250-330 words, French, her voice. Open on something specific to THEM.
- **Lead on the CFA numiA partnership** if `school_partners.summary(company)` returns one.
- **Never claim a tool `about_me.txt` does not attribute to her.** No Kubernetes, no Terraform,
  no Databricks unless she has used them. Naming the gap honestly reads better than padding:
  the SII letter said outright she has not practised Terraform or Kubernetes on a real
  environment, because the posting said "attirée par" and that is exactly true.
- GE HealthCare is **"validé par l'équipe et approuvé pour la mise en production"** — never "en
  production", "déployé", "shipped", "live". The outreach agent runs on a **Google Cloud VM**;
  AWS EC2 is the Content Engine.
- **Measure, do not read through**: zero `"ce n'est pas X, c'est Y"`, ≤1 em-dash per paragraph,
  no clichés ("acteur majeur", "passionnée", "vos valeurs").
- Compile to PDF (she asked for PDF): write `cover_letters/SLUG_LM.tex` modelled on an existing
  one, then `tectonic cover_letters/SLUG_LM.tex`. Check it is ONE page.

**Then tell her plainly:** the apply URL, the two file paths, why this one, and what the gap is.

## STEP 5 — WHEN SHE SAYS "THIS ONE IS FINISHED"

Run this IMMEDIATELY. It is what stops the posting coming back around:

```bash
python applications.py log "COMPANY" "ROLE, city — terms" \
    --channel "HelloWork|LinkedIn|Workday|…" --pack "CV_... + ..._LM.pdf"
```

It writes the row AND reads it back through the real parser, failing loudly if the shape drifted.
Both `brief.py` and the 08:00 digest read that file, so a logged posting is gone from the queue
for good. Then go straight to the next lead — no need for her to ask again.

## STEP 6 — WHEN THE GOOD ONES RUN OUT

Do not stop and do not tell her there is nothing. Her rule: improve the search.
- `python source_lab.py tune <source> "new query" "another"` — measure new queries, keep winners.
- Widen to T2/T3 of the queue; they are ordered, not filtered out.
- Add a source (see the source table in CLAUDE.md, and the locked-platform notes before spending
  a session on one already proven unreadable).
