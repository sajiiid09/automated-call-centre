"""Browser web-call signalling: the dashboard's call widget POSTs an SDP
offer here and PATCHes ICE candidates; each accepted offer spawns a voice
pipeline task bound to a new call row."""

import asyncio
import uuid

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pipecat.frames.frames import LLMRunFrame
from pipecat.transports.smallwebrtc.request_handler import (
    IceCandidate,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from agent.pipeline import default_transport_params
from app.config import settings
from app.services.call_session import (
    CallSession,
    build_session_task,
    run_call_pipeline,
)

router = APIRouter(prefix="/api/webrtc", tags=["webrtc"])

_handler = SmallWebRTCRequestHandler()
_background_tasks: set[asyncio.Task] = set()


def _parse_uuid(value, field: str) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise HTTPException(400, f"Invalid {field}")


@router.post("/offer")
async def webrtc_offer(request: Request):
    if not settings.deepgram_api_key or not settings.gemini_api_key:
        raise HTTPException(
            503, "Voice agent not configured: set DEEPGRAM_API_KEY and GEMINI_API_KEY in .env"
        )

    body = await request.json()
    webrtc_request = SmallWebRTCRequest.from_dict(body)
    request_data = webrtc_request.request_data or {}

    direction = request_data.get("direction", "inbound")
    if direction not in ("inbound", "outbound"):
        raise HTTPException(400, "direction must be inbound or outbound")
    contact_id = _parse_uuid(request_data.get("contact_id"), "contact_id")
    campaign_id = _parse_uuid(request_data.get("campaign_id"), "campaign_id")

    answer: dict | None = None

    async def on_connection(connection):
        nonlocal answer
        session = CallSession(direction=direction, contact_id=contact_id, campaign_id=campaign_id)
        call_id = await session.start()
        config = await session.build_config()

        transport = SmallWebRTCTransport(
            webrtc_connection=connection, params=default_transport_params()
        )
        task = build_session_task(transport, config, session)

        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            logger.info(f"Web call {call_id}: client connected, starting agent")
            await task.queue_frames([LLMRunFrame()])

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info(f"Web call {call_id}: client disconnected")
            await task.cancel()

        bg = asyncio.create_task(run_call_pipeline(session, task))
        _background_tasks.add(bg)
        bg.add_done_callback(_background_tasks.discard)

    answer = await _handler.handle_web_request(webrtc_request, on_connection)
    return answer


@router.patch("/offer")
async def webrtc_ice_patch(request: Request):
    body = await request.json()
    patch = SmallWebRTCPatchRequest(
        pc_id=body["pc_id"],
        candidates=[
            IceCandidate(
                candidate=c["candidate"],
                sdp_mid=c["sdp_mid"] if "sdp_mid" in c else c.get("sdpMid"),
                sdp_mline_index=c["sdp_mline_index"]
                if "sdp_mline_index" in c
                else c.get("sdpMLineIndex"),
            )
            for c in body.get("candidates", [])
        ],
    )
    await _handler.handle_patch_request(patch)
    return {"status": "ok"}
