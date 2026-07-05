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
f = _t.funnel()
print('--- CONVERSION FUNNEL ---')
print(f'  Pending {f[\"pending\"]}  →  Emailed {f[\"emailed\"]}  →  Followed-up {f[\"followed_up\"]}  →  Replied {f[\"replied\"]}  →  Interview {f[\"interview\"]}')
print(f'  Contacted: {f[\"contacted\"]}  |  Reply rate: {f[\"reply_rate\"]*100:.1f}%  |  Interview rate: {f[\"interview_rate\"]*100:.1f}%  |  Rejected: {f[\"rejected\"]}')
print()
e = _t.enrichment_stats()
print('--- ENRICHMENT COVERAGE (active rows) ---')
print(f'  Named decision-maker: {e[\"named\"]}/{e[\"active\"]} ({e[\"named_rate\"]*100:.0f}%)  [confirmed {e[\"named_confirmed\"]} | guessed {e[\"named_guessed\"]}]  |  generic inbox: {e[\"generic\"]}')
print()
import config as _c
from config import WARM_CAP, DAILY_CAP
from smtp_send import today_send_counts
counts = today_send_counts()
cold_done, warm_done = counts.get('cold', 0), counts.get('warm', 0)
cold_cap = _c.effective_cold_cap()   # ramped cap for today (≤ COLD_CAP)
ramp = '' if cold_cap == _c.COLD_CAP else f' (warm-up ramp; ceiling {_c.COLD_CAP})'
print(f'Sends today:  cold {cold_done}/{cold_cap}{ramp}  |  warm {warm_done}/{WARM_CAP}  |  total {cold_done+warm_done}/{DAILY_CAP}')
print(f'Remaining:    cold {max(0,cold_cap-cold_done)}  |  warm {max(0,WARM_CAP-warm_done)}')
print()

overdue = _t.overdue_followups()
print(f'--- FOLLOW-UPS DUE ({len(overdue)}) — multi-touch, up to {_c.MAX_FOLLOWUPS} per lead ---')
for r in overdue:
    fu = r.get('followup_number', 1)
    print(f'  FU{fu} | {r[\"biz_days_waiting\"]:2d} days | {str(r[\"Company\"]):<22} | {str(r[\"Contact Email\"])[:50]}')

replied = df[df[\"Status\"] == \"Replied\"]
print(f'\n--- REPLIES AWAITING RESPONSE ({len(replied)}) ---')
for _, r in replied.iterrows(): print(f'  {r[\"Company\"]} | {r[\"Contact Email\"][:50]}')

stalled = _t.stalled_conversations(days=5)
print(f'\n--- ⚠ WARM LEADS GOING COLD ({len(stalled)}) — replied but idle 5+ biz days, RE-ENGAGE ---')
for r in stalled[:8]:
    print(f'  {r[\"biz_days_idle\"]:2d}d idle | {str(r[\"Company\"])[:22]:22} | last: {r.get(\"last_reply\",\"\")[:45]}')

import warm_network as _wn
_wc = _wn.load()
print(f'\n--- WARM / REFERRAL NETWORK ({len(_wc)} contact(s)) ---')
if not _wc:
    print('  (empty) — add people you know: python warm_network.py add \"Name\" \"Company\" \"how you know them\"')
else:
    for c in _wc[:8]: print(f'  · {c[\"person\"]} @ {c[\"company\"]}' + (f' — {c[\"relationship\"]}' if c.get('relationship') else ''))

print(f'\n--- RECENT ACTIVITY (last 7 days) ---')
cutoff = str(date.today() - timedelta(days=7))
recent = df[df[\"Last Interaction Date\"].fillna(\"\").astype(str) >= cutoff].sort_values(\"Last Interaction Date\", ascending=False)
for _, r in recent.head(10).iterrows():
    print(f'  {r[\"Last Interaction Date\"]}  {r[\"Status\"]:<14}  {r[\"Company\"]} — {r[\"Role\"][:40]}')

import tracker as _t
rec = _t.recommend_strategy_order()
print(f'\n--- STRATEGY PERFORMANCE ({rec[\"phase\"].upper()} phase) ---')
any_data = any(r['sent'] for r in rec['ranked'])
if not any_data:
    print('  No data yet — strategy tags will accumulate as emails are sent.')
else:
    for r in rec['ranked']:
        bar = '█' * r['replied'] + '░' * (r['sent'] - r['replied'])
        print(f'  {r[\"letter\"]} {r[\"name\"]:<22}  {r[\"replied\"]}/{r[\"sent\"]} replied  ({r[\"rate\"]*100:.0f}%)  conf={r[\"score\"]:.2f}  {bar}')
print(f'  → recommend: {rec[\"recommend\"]} ({_t.ALL_STRATEGIES[rec[\"recommend\"]]}) — {rec[\"phase\"]}, ranked by confidence-adjusted rate')

import learning as _lrn
ls = _lrn.reply_stats()
print(f'\n--- SELF-IMPROVING SIGNALS (WS4) — base reply {ls[\"base\"][\"rate\"]*100:.0f}% ({ls[\"base\"][\"replied\"]}/{ls[\"base\"][\"sent\"]}) ---')
ins = _lrn.insights()
if ins:
    for i in ins: print('  ' + i)
else:
    print('  Exploring — not enough reply data per bucket yet; keeping variety high so every bucket samples.')
for dim in ('company_type', 'contract_intent', 'role_fit', 'subject_question'):
    bs = ls['dimensions'].get(dim, {})
    ranked = sorted(bs.items(), key=lambda kv: kv[1]['sent'], reverse=True)
    cells = [f\"{name} {b['replied']}/{b['sent']}({b['rate']*100:.0f}%)\" for name, b in ranked if not name.startswith('(')]
    if cells: print(f'  {dim:16s}: ' + '  '.join(cells[:4]))

leads = _t.rank_pending_leads(limit=5)
print(f'\n--- TOP PENDING LEADS (next cold sends) ---')
for l in leads:
    print(f'  {l[\"score\"]:3d} | {str(l[\"Company\"])[:22]:22s} | {str(l[\"Role\"])[:38]}')
"
```

Report the dashboard output clearly, highlight any items needing immediate attention (P1 replies, P2 follow-ups due, serious reply alerts).
