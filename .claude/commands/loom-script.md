Draft a tight 30–60 second personalized video-pitch script for a HIGH-VALUE lead — Zineb records it (Loom / phone) and sends the link. A short, specific video 3-4×s response for the best targets, and for an AI-native company she can literally screen-record her outreach agent running — a differentiator no text email can match. Draft-only; she records + sends by hand.
Working directory: /path/to/stationf-agent

`$ARGUMENTS` = company name (required), optional `--lang en`, optional `--for cold|followup|linkedin`
(default `followup` — a video is strongest as a warm second touch or on LinkedIn, NOT bolted onto a
first cold email where a video link reads as spammy).

Read CLAUDE.md and about_me.txt (PROJECT MATCHING GUIDE) first. French by default; `--lang en` → English.

**Use this only for leads that deserve it** — a dream company, an AI-native company (where showing the
agent run lands hardest), a warm/referral lead, or a lead that replied and is worth extra effort. Not
for every lead; the point is concentrated effort on the best targets.

---

## STEP 1 — CONTEXT

```bash
source /path/to/stationf-agent/venv/bin/activate && python -c "
import tracker, json, warm_network, lead_facts
df = tracker.load(); m = df['Company'].astype(str).str.strip().str.lower()=='COMPANY'.lower()
row = df[m].to_dict('records')
print('ROW:', json.dumps(row[:1], ensure_ascii=False, default=str))
print('WARM:', warm_network.summary('COMPANY'))
print('HOOK:', json.dumps(lead_facts.get('COMPANY'), ensure_ascii=False) if lead_facts.get('COMPANY') else '(none)')
"
```
Research the company briefly if the hook is thin (what they build + the one specific thing to name).

## STEP 2 — WRITE THE SCRIPT

A spoken script is NOT a written email — short sentences, natural speech, one idea per line, a beat to
breathe. **35–60 seconds ≈ 90–150 words. Hard cap 150.** Structure:

1. **Hook on THEM (0-8s)** — name the specific thing about their product/problem. No "Bonjour je m'appelle".
2. **The proof, shown not told (8-30s)** — the ONE matched project. For AI-native companies, the power
   move: *"je vous montre — cet email vous a été envoyé par un agent IA que j'ai déployé, le voici qui
   tourne"* and narrate the screen (scraping → qualification → envoi). For others, the GE HealthCare
   result or the domain-matched project.
3. **The fit + ask (30-50s)** — why THEM specifically, the alternance-M1-sept-2026 ask (open CDI/CDD),
   and a low-friction close ("*10 minutes cette semaine ?*").

Rules: warm, confident, peer-to-peer, concrete. No credential list, no cost/AUA, no reading the CV
aloud. Every sentence earns its place. Mark stage directions in [brackets] (e.g. [partage écran : agent
qui tourne]) so she knows what to show.

## STEP 3 — SAVE + THUMBNAIL LINE

Write to `drafts/YYYY-MM-DD/loom-COMPANY_SLUG.txt`:
```
COMPANY: <name>   |   FOR: <cold|followup|linkedin>   |   EST. LENGTH: <~Xs>
SUGGESTED SEND LINE (the text that accompanies the video link):
  <one warm sentence, e.g. "Plutôt que d'écrire, je vous montre en 40s ce que je peux apporter chez [Company] :">
--- SCRIPT ---
[0-8s]  <hook>
[8-30s] <proof, with [stage directions]>
[30-50s] <fit + ask>
```
Count the words; trim to ≤150.

## STEP 4 — ALERT ZINEB

```bash
python smtp_send.py --to you@example.com \
  --subject "[LOOM] Script vidéo prêt — <Company>" --kind alert \
  --body "Script vidéo (~Xs) prêt : drafts/YYYY-MM-DD/loom-COMPANY_SLUG.txt

Pour : <cold|followup|linkedin>. Enregistre en Loom (ou téléphone), puis envoie le lien avec la phrase
d'accompagnement. Pour une boîte IA : partage ton écran avec l'agent qui tourne — c'est ça qui marque.

<paste the send line + the script here>" --send
```

Report to console: the file path, estimated length, and where she should use it.
