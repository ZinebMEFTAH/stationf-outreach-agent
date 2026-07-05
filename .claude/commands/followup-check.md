Midday inbox check — sync Gmail, classify new replies, send alerts if serious. No outbound emails sent.
Working directory: /path/to/stationf-agent

This runs at 12:00 Paris time to catch replies that arrived after the morning send cycle. It is read-only for the outreach pipeline.

## Run inbox sync

```bash
cd /path/to/stationf-agent && source venv/bin/activate && python imap_fetch.py --since-days 2
```

Read every line of output. For each reply printed, classify it:

**Serious** = interview request, call/meeting ask, availability question, contract/start-date discussion, technical questions, internal forwarding to another person.
**Not serious** = auto-reply, out-of-office, polite no, "no openings right now."

For every **serious** reply, send an immediate personal alert:
```bash
cd /path/to/stationf-agent && source venv/bin/activate && \
python smtp_send.py \
  --to you@example.com \
  --subject "[ALERT · CATEGORY] COMPANY — CONTACT" \
  --kind alert \
  --body "Serious reply at midday check.

Company: ...
Contact: ...
Category: interview_request | technical_questions | contract_discussion | internal_introduction | other
Summary: one sentence
Suggested action: what Zineb should do next

Reply (verbatim):
..." \
  --send
```
(`--kind alert` keeps it out of the tracker and the daily send caps, and sends it raw — no P.S. footer.)

**If the reply is an interview request**, add to the alert: *"Prep sheet: run `/interview-prep COMPANY`"*
— that generates her tailored prep (what they evaluate → best-matched proof → likely Qs → questions
to ask). Winning the interview is the whole game once you're here.

## Stalled warm leads — recover near-misses (read-only)

A reply that goes quiet is an interview/offer left on the table. Surface leads where a human replied
but the thread has stalled, so Zineb can re-engage:
```bash
python -c "import tracker, json; s=tracker.stalled_conversations(days=5); print(json.dumps([{'Company':r['Company'],'Contact':r['Contact Email'],'idle_days':r['biz_days_idle'],'last':r.get('last_reply','')} for r in s], ensure_ascii=False, indent=2))"
```
If any exist, send ONE consolidated alert (`--kind alert`) listing them with a suggested re-engage
line for each (reference what they last said). These are warm — recovering one is worth more than
many cold sends. Do NOT auto-reply; Zineb re-engages herself.

## Report

Print:
- How many messages fetched and matched
- For each matched reply: company, sender, subject, body snippet, your classification (serious/not)
- Alerts sent (if any)
- No outbound emails sent — this is inbox-only
