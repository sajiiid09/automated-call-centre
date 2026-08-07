import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models import Call
from app.schemas import CallDetail, CallOut, OutboundCallRequest

router = APIRouter(prefix="/api/calls", tags=["calls"])


def _with_names(call: Call, model: type[CallOut] = CallOut) -> CallOut:
    out = model.model_validate(call)
    out.contact_name = call.contact.name if call.contact else None
    out.campaign_name = call.campaign.name if call.campaign else None
    return out


@router.get("", response_model=list[CallOut])
def list_calls(
    direction: str | None = None,
    campaign_id: uuid.UUID | None = None,
    disposition: str | None = None,
    db: Session = Depends(get_db),
):
    stmt = (
        select(Call)
        .options(selectinload(Call.contact), selectinload(Call.campaign))
        .order_by(Call.started_at.desc().nulls_last())
        .limit(200)
    )
    if direction:
        stmt = stmt.where(Call.direction == direction)
    if campaign_id:
        stmt = stmt.where(Call.campaign_id == campaign_id)
    if disposition:
        stmt = stmt.where(Call.disposition == disposition)
    return [_with_names(c) for c in db.scalars(stmt)]


@router.post("/outbound", status_code=202)
async def outbound_call(payload: OutboundCallRequest, db: Session = Depends(get_db)):
    """Ad-hoc single outbound PSTN call to a contact (requires Twilio)."""
    from app.config import settings
    from app.models import Contact
    from app.services import call_session, telephony

    contact = db.get(Contact, payload.contact_id)
    if contact is None:
        raise HTTPException(404, "Contact not found")

    # Refuse before creating anything, so a blocked call leaves no orphan row.
    await telephony.assert_dialable(contact.phone)

    call_id = await asyncio.to_thread(
        call_session.create_outbound_call_row,
        contact.id,
        None,
        settings.twilio_phone_number,
        contact.phone,
    )
    try:
        sid = await telephony.originate_call(contact.phone, call_id, contact_id=contact.id)
    except Exception:
        await asyncio.to_thread(call_session.mark_call_failed, call_id, "Origination failed")
        raise
    await asyncio.to_thread(call_session.attach_twilio_sid, call_id, sid)
    return {"call_id": str(call_id), "twilio_sid": sid}


@router.get("/{call_id}", response_model=CallDetail)
def get_call(call_id: uuid.UUID, db: Session = Depends(get_db)):
    call = db.get(
        Call,
        call_id,
        options=[
            selectinload(Call.turns),
            selectinload(Call.contact),
            selectinload(Call.campaign),
        ],
    )
    if call is None:
        raise HTTPException(404, "Call not found")
    return _with_names(call, CallDetail)
