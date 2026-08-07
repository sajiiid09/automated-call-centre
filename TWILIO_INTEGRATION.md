# Twilio Live Verification

**The integration code is complete and tested.** What remains cannot be done
from a keyboard: someone has to point a public URL at the backend and make real
phone calls. This is that checklist.

Everything below is a one-time setup plus ~15 test calls. Budget an hour.

## Current state

| Item | Status |
|---|---|
| Twilio credentials in `.env` | ✅ verified — account active, **Trial** tier |
| Phone number | ✅ `+447888862925`, voice-capable |
| Deepgram STT + TTS, Gemini | ✅ live-verified |
| Adapter code (`/inbound`, `/outbound-answer`, `/status`, `/media`) | ✅ implemented, unit-tested |
| Real campaign dialing (supervisor) | ✅ implemented, unit-tested |
| Webhook signature validation | ✅ implemented |
| `PUBLIC_BASE_URL` / ngrok | ⬜ **you** |
| `OUTBOUND_ALLOWLIST` | ⬜ **you** — nothing dials until this is set |
| Console webhooks | ⬜ **you** |
| Live inbound / outbound / campaign calls | ⬜ **you** |

## Preflight — bound the blast radius

Create a Twilio **subaccount with a low balance cap** and use its credentials.
Worst case is then capped by the subaccount, not by your main balance.

Trial tier: outbound only connects to numbers you have verified in the console
(Verified Caller IDs), and a trial announcement plays for ~6s before the agent
speaks. Upgrading (~$20) removes both.

## 1. Configure

```bash
ngrok http 8000     # must stay running; the free-tier URL changes on restart
```

```bash
# .env — credentials are already set; these two are not
PUBLIC_BASE_URL=https://<your-subdomain>.ngrok.app   # https, no trailing slash
OUTBOUND_ALLOWLIST=+44xxxxxxxxxx                     # your own verified mobile
```

`PUBLIC_BASE_URL` **must** be `https://` — it is rewritten to `wss://` for the
media stream, and an `http://` value produces silent audio with no error.

`OUTBOUND_ALLOWLIST` is fail-closed: while it is empty, every real call is
refused. This is deliberate. Add numbers only as you need them.

Restart the backend after editing `.env`.

## 2. Point Twilio at the backend

Console → Phone Numbers → your number → Voice configuration:

- **A call comes in**: Webhook, `POST https://<ngrok>/twilio/inbound`
- **Call status changes**: `POST https://<ngrok>/twilio/status`

## 3. Confirm the mode flipped

```bash
curl -s localhost:8000/api/campaigns | jq -r '.[0].dialing_mode'   # → "twilio"
```

If it says `simulated`, a prerequisite is missing — check the `https://` prefix
first. This one command catches nearly every misconfiguration, including an
ngrok URL that changed on restart.

## 4. Verify, in order

Each step exercises something the one before it doesn't.

1. **Inbound.** Phone the Twilio number. Expect the greeting, working barge-in,
   and a transcript in the dashboard. Check the call row shows **real E.164
   numbers**, not `web-call`.
2. **Outbound, answered.**
   ```bash
   curl -X POST localhost:8000/api/calls/outbound \
     -H 'Content-Type: application/json' \
     -d '{"contact_id": "<uuid of an allowlisted contact>"}'
   ```
   Watch the row walk `initiated` → `ringing` → `in_progress` → `completed`,
   with `twilio_sid` set and `duration_seconds` ≈ talk time (not talk + ring).
3. **Outbound, unanswered.** Same call, but let it ring out. The row must reach
   `no_answer` with `disposition="failed"`. *This is the case that used to
   produce no row at all and hang the campaign forever.*
4. **Blocked number.** `curl` for a contact not on the allowlist → **503**, and
   nothing in Twilio console → Monitor → Calls.
5. **Campaign, 2 contacts** (both your verified number). Start → confirmation
   dialog → one phone rings. Hang up; within ~3s the second call originates.
   Keep Monitor → Calls open and confirm **never two concurrent legs**.
6. **Stop mid-call.** The live leg must drop immediately and no further calls
   originate.
7. **Restart mid-call.** `Ctrl-C` the backend while a call is live, then restart.
   Within `DIAL_STALE_CALL_SECONDS` (default 300) the stranded contact is reaped
   and the queue resumes on its own.
8. **Signature validation.** From your laptop:
   ```bash
   curl -X POST https://<ngrok>/twilio/status -d 'CallSid=CA1&CallStatus=completed'
   ```
   → **403**. A 204 here means anyone on the internet can drive your campaign
   state machine.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `dialing_mode` stays `simulated` | `PUBLIC_BASE_URL` missing or not `https://` |
| 503 "OUTBOUND_ALLOWLIST is empty" | working as designed — add the number |
| 503 "Daily outbound cap reached" | raise `MAX_OUTBOUND_CALLS_PER_DAY` |
| Call connects, silence both ways | `PUBLIC_BASE_URL` was `http://`, so the stream URL is invalid |
| Webhooks stopped firing | ngrok restarted and issued a new URL |
| Twilio 400 on origination | number not verified on the trial account |
| Contact stuck in `calling` | check backend logs; the reap clears it after the stale timeout |

## When every box above is ticked

Delete this file, mark Phase 6 complete in [PLAN.md](PLAN.md), and drop the
"live-untested" wording from [README.md](README.md),
[ARCHITECTURE.md](ARCHITECTURE.md), and [PROCEDURE.md](PROCEDURE.md) §6.
