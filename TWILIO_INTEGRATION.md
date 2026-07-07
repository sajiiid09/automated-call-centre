# Twilio Integration Day — Checklist

Everything else is built and demo-ready; this is the only remaining wiring.
The adapter code exists but is **live-untested** (written without a Twilio
account): expect ~1 hour of verification and small fixes.

## 0. Prerequisites

- Twilio account with a **UK number** (regulatory bundle approved) — or a US
  number as fallback.
- If on trial: verify your own mobile number in Twilio console (outbound
  calls only reach verified numbers; a trial notice plays on each call).
  Upgrading (~$20) removes both limits.

## 1. Configure

```bash
# .env
TWILIO_ACCOUNT_SID=ACxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxx
TWILIO_PHONE_NUMBER=+44xxxxxxxxxx
PUBLIC_BASE_URL=https://<your-subdomain>.ngrok.app   # no trailing slash
```

```bash
ngrok http 8000        # must stay running; update PUBLIC_BASE_URL if URL changes
```

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

- Twilio start-message handshake in `/twilio/media` (message order assumptions).
- 8 kHz μ-law resampling quality via `TwilioFrameSerializer` defaults.
- ngrok free-tier URL changes on restart → webhooks break silently.
- Trial announcement adds ~6s before the agent greeting.
