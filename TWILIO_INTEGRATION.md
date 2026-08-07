# Twilio Integration Day — Checklist

Everything else is built and demo-ready; this is the only remaining wiring.
The adapter code exists but is **live-untested** (written without a Twilio
account): expect ~1 hour of verification and small fixes.

## Current state

| Item | Status |
|---|---|
| Twilio account credentials in `.env` | ✅ verified against the API — account active, **Trial** tier |
| Phone number | ✅ `+447888862925` confirmed owned, `voice=true` |
| Deepgram STT + TTS | ✅ live-verified (TTS→STT round trip) |
| Gemini LLM + dispositions | ✅ live-verified |
| `PUBLIC_BASE_URL` / ngrok | ⬜ not set — **blocks everything below** |
| Console webhooks | ⬜ not pointed at the backend |
| Live inbound call | ⬜ never attempted |
| Live outbound call | ⬜ never attempted |
| Campaign dialer → real origination | ⬜ still simulated |

Start at step 1.

## 0. Prerequisites

- Twilio account with a **UK number** (regulatory bundle approved) — or a US
  number as fallback. ✅ done
- If on trial: verify your own mobile number in Twilio console (outbound
  calls only reach verified numbers; a trial notice plays on each call).
  Upgrading (~$20) removes both limits. **The account is on Trial** — verify
  your mobile before testing outbound.

## 1. Configure

Credentials are already in `.env`. Only `PUBLIC_BASE_URL` is missing:

```bash
# .env
TWILIO_ACCOUNT_SID=ACxxxxxxxx                        # ✅ set
TWILIO_AUTH_TOKEN=xxxxxxxx                           # ✅ set
TWILIO_PHONE_NUMBER=+44xxxxxxxxxx                    # ✅ set
PUBLIC_BASE_URL=https://<your-subdomain>.ngrok.app   # ⬜ TODO, no trailing slash
```

```bash
ngrok http 8000        # must stay running; update PUBLIC_BASE_URL if URL changes
```

Must be `https://` — `_stream_twiml()` rewrites the scheme to `wss://`, so an
`http://` value yields a broken stream URL and silent audio.

Restart the backend after editing `.env`.

## 2. Point Twilio at the backend

Twilio console → Phone Numbers → your number → Voice configuration:

- **A call comes in**: Webhook, `POST https://<ngrok>/twilio/inbound`
- **Call status changes**: `POST https://<ngrok>/twilio/status`

## 3. Verify inbound

1. Phone the Twilio number from your mobile.
2. Expect: agent greets you; conversation works; barge-in works.
3. Dashboard → Calls: transcript appears (direction `inbound`).

Debug: backend logs show `Twilio inbound call from …` then
`media stream connected`. If audio is silent, check `PUBLIC_BASE_URL` uses
`https://` (the TwiML converts it to `wss://`).

## 4. Verify outbound (single call)

```bash
curl -X POST localhost:8000/api/calls/outbound \
  -H 'Content-Type: application/json' \
  -d '{"contact_id": "<uuid of a contact with YOUR verified number>"}'
```

Expect your phone to ring; answer and talk to the agent.

## 5. Switch campaigns from simulated to real dialing

Currently campaign calls run as browser web-calls ("Answer as contact").
To make `Start campaign` place real calls, call
`telephony.originate_call(contact.phone, contact_id, campaign_id)` for the
next pending contact — hook point: `start_campaign` /
`advance_after_call` in `backend/app/services/dialer.py` (advance on the
Twilio `completed` status callback instead of web-call end). The pipeline,
transcripts, and dispositions already handle Twilio calls via
`/twilio/media`.

## Known integration risks (why live testing matters)

- Twilio start-message handshake in `/twilio/media` (message order assumptions:
  the loop reads at most 2 messages waiting for `start`).
- 8 kHz μ-law resampling quality via `TwilioFrameSerializer` defaults.
- ngrok free-tier URL changes on restart → webhooks break silently.
- Trial announcement adds ~6s before the agent greeting.

## When this is done

Delete this file, mark Phase 6 complete in [PLAN.md](PLAN.md), and update the
Twilio rows in [README.md](README.md), [ARCHITECTURE.md](ARCHITECTURE.md), and
[PROCEDURE.md](PROCEDURE.md) §6 from "live-untested" to live.
