# Demo Runbook

Full client demo without Twilio: voice runs over browser web-calls using the
real Deepgram + Gemini pipeline. ~10 minutes.

## Before the demo

```bash
# 1. env — .env at repo root with real keys
cp .env.example .env   # fill DEEPGRAM_API_KEY + GEMINI_API_KEY

# 2. services
docker compose up -d db
cd backend && source .venv/bin/activate && uvicorn app.main:app --port 8000
cd frontend && npm run dev          # http://localhost:3001
```

Checklist: `curl localhost:8000/health` ok · dashboard loads on :3001 ·
mic works in browser (Chrome recommended) · speakers audible.

### Reset demo data (optional, destructive)

```bash
docker compose exec db psql -U acc -d callcentre \
  -c "TRUNCATE transcript_turns, calls, campaign_contacts, campaigns, contacts CASCADE;"
```

## Demo script

1. **Dashboard** — stat cards, explain: AI agent handles inbound + outbound,
   everything lands here.
2. **Inbound call** — click **Call agent** on the Dashboard. Speak as a
   customer ("What are your opening hours?"). Interrupt the agent mid-sentence
   to show barge-in. End call.
3. **Transcript** — Calls page → open the call → chat-style transcript.
4. **Contacts** — add a contact live, or Import CSV (`name,phone,notes`).
5. **Campaign** — New campaign: name "Spring promo", goal "Book a product
   demo", script "Offer 20% discount for bookings this week", pick 2 contacts.
6. **Run campaign** — open the campaign → **Start campaign**. The "Next call"
   card appears. Click **Answer as <contact>** and roleplay the contact
   (one interested, one not). After each call the disposition + summary appear
   automatically and the queue advances; campaign completes after last contact.
7. **Results** — campaign table shows per-contact disposition; Dashboard now
   shows disposition breakdown and call stats.
8. **Close** — "When the Twilio number is live, these same campaigns dial real
   phones — the pipeline and dashboard don't change" (see TWILIO_INTEGRATION.md).

## Talking points / caveats

- Voices: Deepgram Aura (demo tier) — upgradeable to Cartesia/ElevenLabs.
- Gemini free tier: occasional slow responses under rate limits.
- Browser call = same agent pipeline that Twilio will feed; only transport differs.
