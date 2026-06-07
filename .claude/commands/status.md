Show a quick dashboard of the alternance outreach pipeline.
Working directory: /path/to/stationf-agent

```bash
cd /path/to/stationf-agent && source venv/bin/activate && python -c "
import tracker as _t, json
from datetime import date, timedelta
import pandas as pd

df = _t.load()
today = str(date.today())

print('=== OUTREACH DASHBOARD ===', today)
print(f'Total contacts: {len(df)}')
print()
print('--- STATUS BREAKDOWN ---')
print(df[\"Status\"].value_counts().to_string())
print()
from config import COLD_CAP, WARM_CAP, DAILY_CAP
from smtp_send import today_send_counts
counts = today_send_counts()
cold_done, warm_done = counts.get('cold', 0), counts.get('warm', 0)
print(f'Sends today:  cold {cold_done}/{COLD_CAP}  |  warm {warm_done}/{WARM_CAP}  |  total {cold_done+warm_done}/{DAILY_CAP}')
print(f'Remaining:    cold {max(0,COLD_CAP-cold_done)}  |  warm {max(0,WARM_CAP-warm_done)}')
print()

overdue = _t.overdue_followups()
print(f'--- FOLLOW-UPS DUE ({len(overdue)}) ---')
for r in overdue:
    print(f'  {r[\"biz_days_waiting\"]:2d} days | {str(r[\"Company\"]):<25} | {str(r[\"Contact Email\"])[:55]}')

replied = df[df[\"Status\"] == \"Replied\"]
print(f'\n--- REPLIES AWAITING RESPONSE ({len(replied)}) ---')
for _, r in replied.iterrows(): print(f'  {r[\"Company\"]} | {r[\"Contact Email\"][:50]}')

print(f'\n--- RECENT ACTIVITY (last 7 days) ---')
cutoff = str(date.today() - timedelta(days=7))
recent = df[df[\"Last Interaction Date\"].fillna(\"\").astype(str) >= cutoff].sort_values(\"Last Interaction Date\", ascending=False)
for _, r in recent.head(10).iterrows():
    print(f'  {r[\"Last Interaction Date\"]}  {r[\"Status\"]:<14}  {r[\"Company\"]} — {r[\"Role\"][:40]}')

import tracker as _t
stats = _t.strategy_stats()
print(f'\n--- STRATEGY PERFORMANCE ---')
if not stats:
    print('  No data yet — strategy tags will accumulate as emails are sent.')
else:
    order = ['Q','O','V','M','U']
    labels = {'Q':'Technical Question','O':'Precise Observation','V':'Value Proof First','M':'Mirrored Challenge','U':'Ultra-short'}
    for s in order:
        if s not in stats: continue
        d = stats[s]
        bar = '█' * d['replied'] + '░' * (d['sent'] - d['replied'])
        print(f'  {s} {labels[s]:<22}  {d[\"replied\"]}/{d[\"sent\"]} replied  ({d[\"rate\"]*100:.0f}%)  {bar}')
"
```

Report the dashboard output clearly, highlight any items needing immediate attention (P1 replies, P2 follow-ups due, serious reply alerts).
