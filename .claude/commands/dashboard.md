Regenerate the live monitoring dashboard from the current pipeline state and (re)publish it.
Working directory: /path/to/stationf-agent

A visual, self-contained HTML dashboard of the whole alternance pipeline: KPIs, priority actions
(replies to answer, overdue follow-ups, stalled leads), the conversion funnel + status breakdown,
channels (warm network, school/CFA partners), top pending leads, and the self-improving signals.

## Regenerate the snapshot

```bash
cd /path/to/stationf-agent && source venv/bin/activate && python dashboard.py
```
This reads the live `contacts.xlsx` + all modules and writes a fresh self-contained `dashboard.html`
(data embedded, no external requests — it's gitignored because it contains contact data).

## Publish / update the Artifact

`dashboard.html` is designed to be published as a Claude Artifact (a private hosted page Zineb can
open anytime). To publish or refresh it, ask Claude to **"publish dashboard.html as an artifact"** —
Claude renders the file with the Artifact tool. To update the SAME page (not mint a new URL), Claude
redeploys to the existing artifact URL.

Design lives in `dashboard_template.html` (a `/*__DATA__*/` placeholder is replaced with the JSON);
`dashboard.py --json` prints just the data blob for debugging. To change the look, edit the template.

Report: the file path written, and the counts of priority items (replies awaiting, overdue follow-ups,
stalled leads) so Zineb sees at a glance what needs attention.
