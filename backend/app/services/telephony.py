"""Twilio call origination, control, and the simulated/real dialing switch.

Real dialing is fail-closed: it stays off until Twilio is fully configured
*and* OUTBOUND_ALLOWLIST names the numbers that may be called. Everything
here is a no-op in simulated mode, which is what keeps the browser demo
working on machines without a public URL.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select

from app.config import settings

TWILIO_API = "https://api.twilio.com/2010-04-01"

DialingMode = Literal["simulated", "twilio"]

# Twilio call statuses that mean the leg is over.
TERMINAL_CALL_STATUSES = ("completed", "failed", "no_answer")


def twilio_enabled() -> bool:
    return bool(settings.twilio_account_sid and settings.twilio_auth_token)


def _twilio_ready() -> bool:
    """Everything required to place or receive a real call."""
    return (
        twilio_enabled()
        # https:// specifically: _stream_twiml rewrites it to wss://, so an
        # http:// URL yields a broken stream and silent audio.
        and settings.public_base_url.startswith("https://")
        and bool(settings.twilio_phone_number)
    )


def dialing_mode() -> DialingMode:
    """Real PSTN dialing or browser-simulated dialing."""
    if settings.dialer_mode == "simulated":
        return "simulated"
    if settings.dialer_mode == "twilio":
        return "twilio"
    return "twilio" if _twilio_ready() else "simulated"


def allowlist() -> set[str]:
    return {n.strip() for n in settings.outbound_allowlist.split(",") if n.strip()}


def is_dialable(phone: str) -> bool:
    """Whether this number may be called in the current mode."""
    if dialing_mode() != "twilio":
        return False
    return phone.strip() in allowlist()


def _count_outbound_today() -> int:
    from app.db import SessionLocal
    from app.models import Call

    since = datetime.now(timezone.utc) - timedelta(days=1)
    with SessionLocal() as db:
        return db.scalar(
            select(func.count())
            .select_from(Call)
            .where(
                Call.direction == "outbound",
                Call.twilio_sid.is_not(None),
                Call.started_at > since,
            )
        )


async def assert_dialable(to_number: str) -> None:
    """Raise unless a real call to this number is permitted right now."""
    if dialing_mode() != "twilio":
        raise HTTPException(503, "Real dialing is off (simulated mode) — see PROCEDURE.md")
    if not settings.public_base_url:
        raise HTTPException(503, "PUBLIC_BASE_URL is not set (ngrok URL required)")
    if not allowlist():
        raise HTTPException(
            503,
            "OUTBOUND_ALLOWLIST is empty. Real dialing is disabled until it lists "
            "the E.164 numbers that may be called.",
        )
    if to_number.strip() not in allowlist():
        raise HTTPException(503, f"{to_number} is not in OUTBOUND_ALLOWLIST")
    placed = await asyncio.to_thread(_count_outbound_today)
    if placed >= settings.max_outbound_calls_per_day:
        raise HTTPException(
            503,
            f"Daily outbound cap reached ({placed}/{settings.max_outbound_calls_per_day}). "
            "Raise MAX_OUTBOUND_CALLS_PER_DAY to continue.",
        )


async def _post_twilio(path: str, data) -> httpx.Response:
    """Single seam for every Twilio REST write — monkeypatched in tests."""
    async with httpx.AsyncClient() as client:
        return await client.post(
            f"{TWILIO_API}/Accounts/{settings.twilio_account_sid}/{path}",
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            data=data,
        )


async def originate_call(
    to_number: str,
    call_id: uuid.UUID,
    contact_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
) -> str:
    """Start an outbound PSTN call; Twilio fetches TwiML from /twilio/outbound-answer.

    ``call_id`` is threaded through both callback URLs so webhooks can find our
    row without waiting for the SID to be persisted. Returns the Twilio Call SID.
    """
    await assert_dialable(to_number)

    params = [f"call_id={call_id}"]
    if contact_id:
        params.append(f"contact_id={contact_id}")
    if campaign_id:
        params.append(f"campaign_id={campaign_id}")
    query = "?" + "&".join(params)

    resp = await _post_twilio(
        "Calls.json",
        {
            "To": to_number,
            "From": settings.twilio_phone_number,
            "Url": f"{settings.public_base_url}/twilio/outbound-answer{query}",
            "StatusCallback": f"{settings.public_base_url}/twilio/status?call_id={call_id}",
            # must be repeated params, not a space-joined string, or only the
            # default "completed" event is ever delivered
            "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
            "Timeout": settings.dial_ring_timeout_seconds,
        },
    )
    if resp.status_code >= 400:
        raise HTTPException(502, f"Twilio call creation failed: {resp.text[:300]}")
    return resp.json()["sid"]


async def hangup_call(twilio_sid: str) -> None:
    """End a live call leg. Used when a campaign is stopped mid-call."""
    resp = await _post_twilio(f"Calls/{twilio_sid}.json", {"Status": "completed"})
    if resp.status_code >= 400:
        raise HTTPException(502, f"Twilio hangup failed: {resp.text[:300]}")
