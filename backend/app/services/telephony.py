"""Twilio call origination. DORMANT until Twilio credentials exist in .env —
live-untested; see TWILIO_INTEGRATION.md for the integration-day checklist."""

import uuid

import httpx
from fastapi import HTTPException

from app.config import settings

TWILIO_API = "https://api.twilio.com/2010-04-01"


def twilio_enabled() -> bool:
    return bool(settings.twilio_account_sid and settings.twilio_auth_token)


async def originate_call(
    to_number: str,
    contact_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
) -> str:
    """Start an outbound PSTN call; Twilio fetches TwiML from /twilio/outbound-answer.

    Returns the Twilio Call SID.
    """
    if not twilio_enabled():
        raise HTTPException(503, "Twilio is not configured (see TWILIO_INTEGRATION.md)")
    if not settings.public_base_url:
        raise HTTPException(503, "PUBLIC_BASE_URL is not set (ngrok URL required)")

    params = []
    if contact_id:
        params.append(f"contact_id={contact_id}")
    if campaign_id:
        params.append(f"campaign_id={campaign_id}")
    query = ("?" + "&".join(params)) if params else ""

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{TWILIO_API}/Accounts/{settings.twilio_account_sid}/Calls.json",
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            data={
                "To": to_number,
                "From": settings.twilio_phone_number,
                "Url": f"{settings.public_base_url}/twilio/outbound-answer{query}",
                "StatusCallback": f"{settings.public_base_url}/twilio/status",
                "StatusCallbackEvent": "initiated ringing answered completed",
            },
        )
    if resp.status_code >= 400:
        raise HTTPException(502, f"Twilio call creation failed: {resp.text[:300]}")
    return resp.json()["sid"]
