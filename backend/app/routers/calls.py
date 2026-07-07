import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models import Call
from app.schemas import CallDetail, CallOut

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
async def outbound_call(payload: dict, db: Session = Depends(get_db)):
    """Ad-hoc single outbound PSTN call to a contact (requires Twilio)."""
    from app.services import telephony

    contact_id = payload.get("contact_id")
    if not contact_id:
        raise HTTPException(400, "contact_id is required")
    from app.models import Contact

    contact = db.get(Contact, uuid.UUID(str(contact_id)))
    if contact is None:
        raise HTTPException(404, "Contact not found")
    sid = await telephony.originate_call(contact.phone, contact_id=contact.id)
    return {"twilio_sid": sid}


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
