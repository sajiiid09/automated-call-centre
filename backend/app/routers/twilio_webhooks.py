"""Twilio webhooks + Media Streams bridge into the shared voice pipeline.

Inactive until Twilio credentials and an https PUBLIC_BASE_URL are configured;
see PROCEDURE.md. The WebSocket bridge follows Pipecat's Twilio chatbot
reference (FastAPIWebsocketTransport + TwilioFrameSerializer).

The row for an outbound call is created at origination, not here, so calls
that are never answered still have somewhere for their status callback to
land. This handler adopts that row when the media stream connects.
"""

import asyncio
import json
import uuid
from urllib.parse import urlencode
from xml.sax.saxutils import escape

from agent.pipeline import default_transport_params
from fastapi import APIRouter, Depends, Request, WebSocket
from fastapi.responses import Response
from loguru import logger
from pipecat.frames.frames import LLMRunFrame
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal, get_db
from app.models import Call, Contact
from app.services import dialer
from app.services.call_session import (
    CallSession,
    build_session_task,
    run_call_pipeline,
)
from app.services.twilio_auth import (
    stream_token,
    verify_twilio_request,
    verify_twilio_websocket,
)

router = APIRouter(prefix="/twilio", tags=["twilio"])

# Twilio CallStatus -> (our calls.status, advance the queue?, call_ok)
_TERMINAL_STATUS = {
    "completed": ("completed", True),
    "busy": ("failed", False),
    "no-answer": ("no_answer", False),
    "canceled": ("failed", False),
    "failed": ("failed", False),
}
_PROGRESS_STATUS = {"initiated": "initiated", "queued": "initiated", "ringing": "ringing"}


def _stream_twiml(params: dict[str, str]) -> str:
    ws_base = settings.public_base_url.replace("https://", "wss://", 1)
    query = urlencode({k: v for k, v in params.items() if v})
    url = escape(f"{ws_base}/twilio/media?{query}")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f'<Stream url="{url}" />'
        "</Connect></Response>"
    )


@router.post("/inbound", dependencies=[Depends(verify_twilio_request)])
async def inbound_call(request: Request) -> Response:
    """Answer webhook for the Twilio number: bridge audio to our WebSocket."""
    form = await request.form()
    from_number = str(form.get("From") or "")
    to_number = str(form.get("To") or "")
    logger.info(f"Twilio inbound call from {from_number} sid={form.get('CallSid')}")
    return Response(
        content=_stream_twiml(
            {
                "direction": "inbound",
                "from_number": from_number,
                "to_number": to_number,
                "token": stream_token(""),
            }
        ),
        media_type="application/xml",
    )


@router.post("/outbound-answer", dependencies=[Depends(verify_twilio_request)])
async def outbound_answer(request: Request) -> Response:
    """TwiML fetched when an originated outbound call is answered."""
    q = request.query_params
    call_id = q.get("call_id", "")
    return Response(
        content=_stream_twiml(
            {
                "direction": "outbound",
                "call_id": call_id,
                "contact_id": q.get("contact_id", ""),
                "campaign_id": q.get("campaign_id", ""),
                "token": stream_token(call_id),
            }
        ),
        media_type="application/xml",
    )


def _find_call(db: Session, call_id: str | None, call_sid: str | None) -> Call | None:
    """Our own id first — Twilio can call back before the SID is persisted."""
    if call_id:
        try:
            call = db.get(Call, uuid.UUID(call_id))
        except ValueError:
            call = None
        if call is not None:
            return call
    if call_sid:
        return db.scalar(select(Call).where(Call.twilio_sid == call_sid))
    return None


@router.post("/status", dependencies=[Depends(verify_twilio_request)])
async def status_callback(request: Request, db: Session = Depends(get_db)) -> Response:
    """Track call progress and advance the campaign queue on terminal states.

    This is the only signal that exists for a call nobody answered, so for
    outbound PSTN it — not the pipeline — owns advancing the queue.
    """
    form = await request.form()
    call_sid = form.get("CallSid")
    call_status = form.get("CallStatus")
    call_id = request.query_params.get("call_id")
    logger.info(f"Twilio status sid={call_sid} status={call_status} call_id={call_id}")

    call = _find_call(db, call_id, call_sid)
    if call is None:
        # unknown call (or a stray request): acknowledge, never 500
        return Response(status_code=204)

    if call_sid and not call.twilio_sid:
        call.twilio_sid = call_sid

    if call_status in _PROGRESS_STATUS:
        if call.status in ("initiated", "ringing"):
            call.status = _PROGRESS_STATUS[call_status]
        db.commit()
        return Response(status_code=204)

    if call_status not in _TERMINAL_STATUS:
        db.commit()
        return Response(status_code=204)

    new_status, call_ok = _TERMINAL_STATUS[call_status]
    answered = call.status == "in_progress" or bool(call.turns)
    if call.status not in ("completed", "failed", "no_answer"):
        call.status = new_status
    if not answered:
        # never picked up: no transcript to classify, so label it directly
        # rather than spending a Gemini call to say the same thing
        call.disposition = "failed"
        call.disposition_summary = f"Twilio reported {call_status}"
    if call.duration_seconds is None:
        duration = form.get("CallDuration")
        call.duration_seconds = int(duration) if duration else 0
    db.commit()

    # advance regardless of whether the row was already terminal — the guard in
    # advance_after_call makes this idempotent
    if call.campaign_id and call.contact_id:
        dialer.advance_after_call(db, call.campaign_id, call.contact_id, call_ok and answered)
    return Response(status_code=204)


@router.websocket("/media")
async def media_stream(websocket: WebSocket):
    """Twilio Media Streams WebSocket → shared voice pipeline."""
    if not verify_twilio_websocket(websocket):
        logger.warning("Twilio media WS: rejected unsigned stream request")
        await websocket.close(code=1008)
        return

    await websocket.accept()

    # Twilio sends "connected" then "start" messages before audio flows
    start_data = None
    for _ in range(2):
        message = json.loads(await websocket.receive_text())
        if message.get("event") == "start":
            start_data = message["start"]
            break
    if start_data is None:
        logger.error("Twilio media WS: no start message received")
        await websocket.close()
        return

    stream_sid = start_data["streamSid"]
    call_sid = start_data.get("callSid")

    query = websocket.query_params
    direction = query.get("direction", "inbound")
    call_id = uuid.UUID(query["call_id"]) if query.get("call_id") else None
    contact_id = uuid.UUID(query["contact_id"]) if query.get("contact_id") else None
    campaign_id = uuid.UUID(query["campaign_id"]) if query.get("campaign_id") else None
    from_number = query.get("from_number")
    to_number = query.get("to_number")

    if contact_id is None and from_number:
        contact_id = await asyncio.to_thread(_lookup_contact_by_phone, from_number)

    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token,
    )
    base = default_transport_params()
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            vad_analyzer=base.vad_analyzer,
            serializer=serializer,
        ),
    )

    session = CallSession(
        direction=direction,
        contact_id=contact_id,
        campaign_id=campaign_id,
        call_id=call_id,
        is_twilio=True,
        from_number=from_number,
        to_number=to_number,
        twilio_sid=call_sid,
    )
    call_id = await session.start()

    config = await session.build_config()
    task = build_session_task(transport, config, session)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"Twilio call {call_id}: media stream connected")
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"Twilio call {call_id}: media stream disconnected")
        await task.cancel()

    await run_call_pipeline(session, task)


def _lookup_contact_by_phone(phone: str) -> uuid.UUID | None:
    with SessionLocal() as db:
        return db.scalar(select(Contact.id).where(Contact.phone == phone))
