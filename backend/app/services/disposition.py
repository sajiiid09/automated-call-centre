"""Post-call disposition tagging: Gemini classifies the transcript into an
outcome and writes a one-line summary onto the call row."""

import json
import uuid

from google import genai
from google.genai import types
from loguru import logger

from app.config import settings
from app.db import SessionLocal
from app.models import Call

OUTBOUND_DISPOSITIONS = ["interested", "not_interested", "callback", "voicemail", "failed"]
INBOUND_DISPOSITIONS = ["resolved", "needs_followup", "complaint", "enquiry", "abandoned"]

# Union, kept for callers that just want to know every label that can appear on
# a call row. The two vocabularies are not strictly partitioned: "failed" is
# also written directly by call_session.mark_call_failed and by the Twilio
# status callback, for calls of either direction that never got off the ground.
DISPOSITIONS = OUTBOUND_DISPOSITIONS + INBOUND_DISPOSITIONS

OUTBOUND_PROMPT = """\
You are labelling the outcome of a sales/support phone call between an AI
agent and a contact. Read the transcript and reply with JSON only:
{{"disposition": one of {dispositions}, "summary": "one short sentence on the outcome"}}

Rules:
- "interested": contact showed interest / agreed to next steps
- "not_interested": contact declined or asked not to be contacted
- "callback": contact asked to be called later or a follow-up was agreed
- "voicemail": nobody engaged / it went to voicemail
- "failed": call too short or broken to judge

Transcript:
{transcript}
"""

INBOUND_PROMPT = """\
You are labelling the outcome of an inbound customer-service phone call: the
caller phoned the company and an AI agent answered. Read the transcript and
reply with JSON only:
{{"disposition": one of {dispositions}, "summary": "one short sentence on the outcome"}}

Rules:
- "resolved": the caller's question was answered and nothing further is needed
- "needs_followup": a human has to call back, or something was promised
- "complaint": the caller expressed dissatisfaction about the product or service
- "enquiry": a general information request, answered or not, needing no action
- "abandoned": the caller hung up before engaging, or the call was too short
  or broken to judge

Prefer "complaint" over "resolved" when the caller was unhappy, even if their
question was answered. Prefer "needs_followup" over "enquiry" whenever the
agent promised that someone would get back to them.

Transcript:
{transcript}
"""


# The label for a call that produced no transcript at all, per direction.
NO_CONVERSATION = {"outbound": "failed", "inbound": "abandoned"}


def vocabulary_for(direction: str) -> tuple[list[str], str]:
    """(labels, prompt) for a call direction. Inbound is the CX vocabulary."""
    if direction == "outbound":
        return OUTBOUND_DISPOSITIONS, OUTBOUND_PROMPT
    return INBOUND_DISPOSITIONS, INBOUND_PROMPT


def classify_call(call_id: uuid.UUID) -> None:
    """Blocking; run via asyncio.to_thread. Safe to call for any finished call."""
    with SessionLocal() as db:
        call = db.get(Call, call_id)
        if call is None:
            return
        direction = call.direction
        transcript = "\n".join(
            f"{'Agent' if t.role == 'agent' else 'Caller'}: {t.content}" for t in call.turns
        )

    dispositions, prompt = vocabulary_for(direction)

    if not transcript.strip():
        # Inbound gets "abandoned" rather than "failed" — nothing failed on our
        # side, the caller just hung up before saying anything.
        _write(call_id, NO_CONVERSATION.get(direction, "failed"), "No conversation was recorded.")
        return

    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt.format(dispositions=dispositions, transcript=transcript),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        data = json.loads(response.text)
        disposition = data.get("disposition")
        if disposition not in dispositions:
            raise ValueError(f"Unexpected {direction} disposition {disposition!r}")
        _write(call_id, disposition, str(data.get("summary", ""))[:500])
    except Exception:
        logger.exception(f"Disposition classification failed for call {call_id}")
        _write(call_id, None, None)


def _write(call_id: uuid.UUID, disposition: str | None, summary: str | None) -> None:
    with SessionLocal() as db:
        call = db.get(Call, call_id)
        if call is None:
            return
        call.disposition = disposition
        call.disposition_summary = summary
        db.commit()
