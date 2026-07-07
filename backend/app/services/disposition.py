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

DISPOSITIONS = ["interested", "not_interested", "callback", "voicemail", "failed"]

PROMPT = """\
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


def classify_call(call_id: uuid.UUID) -> None:
    """Blocking; run via asyncio.to_thread. Safe to call for any finished call."""
    with SessionLocal() as db:
        call = db.get(Call, call_id)
        if call is None:
            return
        transcript = "\n".join(
            f"{'Agent' if t.role == 'agent' else 'Contact'}: {t.content}" for t in call.turns
        )

    if not transcript.strip():
        _write(call_id, "failed", "No conversation was recorded.")
        return

    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=PROMPT.format(dispositions=DISPOSITIONS, transcript=transcript),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        data = json.loads(response.text)
        disposition = data.get("disposition")
        if disposition not in DISPOSITIONS:
            raise ValueError(f"Unexpected disposition {disposition!r}")
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
