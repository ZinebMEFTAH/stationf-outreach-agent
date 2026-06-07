Build a role-adapted CV PDF from the LaTeX source and report the output path.
Working directory: /path/to/stationf-agent

## What it does

Calls `cv_builder.py` which:
1. Reads the base `.tex` source (`CV_Zineb_Meftah_FR.tex` or `_EN.tex`)
2. Swaps the headline, subtitle, and alternance-target sentence to match the role focus
3. Compiles with `tectonic`
4. Outputs `documents/CV_Zineb_Meftah_{LANG}_custom.pdf`

## Step 1 — Resolve arguments

If `$ARGUMENTS` provides explicit flags, use them directly:
- `--lang fr|en`
- `--focus ai|backend|mlops|data|fullstack`
- `--role "ROLE_TITLE"` (used for auto-detection if --focus is omitted)
- `--company "COMPANY"` (optional, informational)

If `$ARGUMENTS` provides only a company name (no flags), look it up in the tracker:
```bash
python -c "
import tracker
df = tracker.load()
mask = df['Company'].str.strip().str.lower() == 'COMPANY_NAME'.lower()
row = df[mask].iloc[0] if mask.any() else None
if row is not None:
    print('role:', row['Role'])
    print('email:', row['Contact Email'])
"
```
Then infer `--lang` from the role (French keywords → fr, else en) and auto-detect `--focus` from the role title.

If no arguments at all → build the default AI focus in French.

## Step 2 — Build

```bash
python /path/to/stationf-agent/cv_builder.py \
  --lang LANG \
  --focus FOCUS \
  --role "ROLE_TITLE" \
  --company "COMPANY"
```

## Step 3 — Verify

```bash
ls -lh /path/to/stationf-agent/documents/CV_Zineb_Meftah_*_custom.pdf
```

## Step 4 — Report

Print:
- Full path to the compiled PDF
- Lang + focus used
- File size
- Ready-to-use `--attach` flag for `smtp_send.py`, e.g.:
  `--attach documents/CV_Zineb_Meftah_FR_custom.pdf`

## Focus detection reference

| Role keywords | Focus |
|---|---|
| mlops, platform, devops, sre, infra | `mlops` |
| data engineer, data analyst, analytics, données | `data` |
| fullstack, full-stack | `fullstack` |
| backend, software engineer, api, django, fastapi | `backend` |
| everything else (ai, ml, llm, alternance générique…) | `ai` |
