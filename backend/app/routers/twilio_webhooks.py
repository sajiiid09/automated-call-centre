"""Twilio webhooks + Media Streams bridge into the shared voice pipeline.

DORMANT until Twilio credentials and PUBLIC_BASE_URL are configured —
written ahead of time, live-untested. Integration-day steps in
TWILIO_INTEGRATION.md. The WebSocket bridge follows Pipecat's Twilio
chatbot reference (FastAPIWebsocketTransport + TwilioFrameSerializer).
"""

import asyncio
import json
import uuid
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Request, WebSocket
from fastapi.responses import Response
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session
from pipecat.frames.frames import LLMRunFrame
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from agent.pipeline import default_transport_params
from app.config import settings
from app.db import SessionLocal, get_db
from app.models import Call, Contact
from app.services.call_session import (
    CallSession,
    build_session_task,
    run_call_pipeline,
)

router = APIRouter(prefix="/twilio", tags=["twilio"])


def _stream_twiml(query: str = "") -> str:
    ws_base = settings.public_base_url.replace("https://", "wss://", 1)
    url = escape(f"{ws_base}/twilio/media{query}")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f'<Stream url="{url}" />'
        "</Connect></Response>"
    )


def _lookup_contact_by_phone(phone: str) -> uuid.UUID | None:
    from sqlalchemy import select

    with SessionLocal() as db:
        return db.scalar(select(Contact.id).where(Contact.phone == phone))


@router.post("/inbound")
async def inbound_call(request: Request) -> Response:
    """Answer webhook for the Twilio number: bridge audio to our WebSocket."""
    form = await request.form()
    logger.info(f"Twilio inbound call from {form.get('From')} sid={form.get('CallSid')}")
    return Response(content=_stream_twiml("?direction=inbound"), media_type="application/xml")


@router.post("/outbound-answer")
async def outbound_answer(request: Request) -> Response:
    """TwiML fetched when an originated outbound call is answered."""
    q = request.query_params
    params = ["direction=outbound"]
    if q.get("contact_id"):
        params.append(f"contact_id={q['contact_id']}")
    if q.get("campaign_id"):
        params.append(f"campaign_id={q['campaign_id']}")
    return Response(content=_stream_twiml("?" + "&".join(params)), media_type="application/xml")


@router.post("/status")
async def status_callback(request: Request, db: Session = Depends(get_db)) -> Response:
    """Track call progress; mark failures for calls that never reached the WS."""
    form = await request.form()
    call_sid = form.get("CallSid")
    call_status = form.get("CallStatus")
    logger.info(f"Twilio status callback sid={call_sid} status={call_status}")

    if call_sid and call_status in ("busy", "no-answer", "failed", "canceled"):
        call = db.scalar(select(Call).where(Call.twilio_sid == call_sid))
        if call is not None and call.status not in ("completed", "failed"):
            call.status = "no_answer" if call_status == "no-answer" else "failed"
            db.commit()
    return Response(status_code=204)


@router.websocket("/media")
async def media_stream(websocket: WebSocket):
    """Twilio Media Streams WebSocket → shared voice pipeline."""
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
    contact_id = uuid.UUID(query["contact_id"]) if query.get("contact_id") else None
    campaign_id = uuid.UUID(query["campaign_id"]) if query.get("campaign_id") else None

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

    session = CallSession(direction=direction, contact_id=contact_id, campaign_id=campaign_id)
    call_id = await session.start()

    def _attach_twilio_sid():
        with SessionLocal() as db:
            call = db.get(Call, call_id)
            if call is not None:
                call.twilio_sid = call_sid
                db.commit()

    await asyncio.to_thread(_attach_twilio_sid)

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
