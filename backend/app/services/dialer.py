"""Sequential campaign dialer.

In simulated mode the dialer doesn't place PSTN calls: it surfaces the next
pending contact in the dashboard, where the user answers as that contact over
a web-call. In Twilio mode `services/campaign_runner.py` claims contacts here
and originates real calls. `advance_after_call` keeps the queue moving in
both modes and is safe to call more than once for the same contact.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Call, Campaign, CampaignContact
from app.services.telephony import TERMINAL_CALL_STATUSES


def _latest_call(db: Session, campaign_id: uuid.UUID, contact_id: uuid.UUID) -> Call | None:
    return db.scalars(
        select(Call)
        .where(Call.campaign_id == campaign_id, Call.contact_id == contact_id)
        .order_by(Call.started_at.desc().nulls_last())
        .limit(1)
    ).first()


def start_campaign(db: Session, campaign_id: uuid.UUID) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaign not found")
    if campaign.status == "running":
        raise HTTPException(409, "Campaign is already running")
    if not campaign.contacts:
        raise HTTPException(400, "Campaign has no contacts")
    campaign.status = "running"
    # Re-queue anything left mid-call by a previous run, but only when its call
    # is actually over — re-queueing a live leg would dial the contact twice.
    for cc in campaign.contacts:
        if cc.status == "calling":
            call = _latest_call(db, campaign_id, cc.contact_id)
            if call is None or call.status in TERMINAL_CALL_STATUSES:
                cc.status = "pending"
    db.commit()
    return campaign


def stop_campaign(db: Session, campaign_id: uuid.UUID) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaign not found")
    if campaign.status != "running":
        raise HTTPException(409, "Campaign is not running")
    campaign.status = "stopped"
    db.commit()
    return campaign


def next_pending_contact(db: Session, campaign_id: uuid.UUID) -> CampaignContact | None:
    """Read-only peek. NOT safe as a claim — use `claim_next_contact`."""
    return db.scalars(
        select(CampaignContact)
        .where(
            CampaignContact.campaign_id == campaign_id,
            CampaignContact.status == "pending",
        )
        .order_by(CampaignContact.position)
        .limit(1)
    ).first()


def claim_next_contact(db: Session, campaign_id: uuid.UUID) -> tuple[uuid.UUID, str, str] | None:
    """Atomically take the next contact to dial, or None if we must not dial.

    Locking the campaign row serializes this per campaign, so two supervisors
    (two workers, or a stale task after --reload) cannot both originate. The
    in-flight check inside that lock is what keeps dialing sequential.

    Returns (contact_id, name, phone) as plain values — an ORM object would be
    detached by the time it crossed back over the asyncio.to_thread boundary.
    """
    campaign = db.execute(
        select(Campaign).where(Campaign.id == campaign_id).with_for_update()
    ).scalar_one_or_none()
    if campaign is None or campaign.status != "running":
        return None

    in_flight = db.scalars(
        select(CampaignContact).where(
            CampaignContact.campaign_id == campaign_id,
            CampaignContact.status == "calling",
        )
    ).first()
    if in_flight is not None:
        return None

    cc = db.execute(
        select(CampaignContact)
        .where(
            CampaignContact.campaign_id == campaign_id,
            CampaignContact.status == "pending",
        )
        .order_by(CampaignContact.position)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if cc is None:
        return None

    cc.status = "calling"
    contact = cc.contact
    claimed = (contact.id, contact.name, contact.phone)
    db.commit()
    return claimed


def mark_calling(db: Session, campaign_id: uuid.UUID, contact_id: uuid.UUID) -> None:
    cc = db.get(CampaignContact, (campaign_id, contact_id))
    if cc is not None and cc.status == "pending":
        cc.status = "calling"
        db.commit()


def advance_after_call(
    db: Session, campaign_id: uuid.UUID, contact_id: uuid.UUID, call_ok: bool
) -> None:
    """Mark the contact done/failed; complete the campaign when queue empties.

    Idempotent: only a contact still in `calling` advances. Real calls can be
    reported terminal by more than one source (status callback, pipeline end,
    stale reap), and without this guard the queue would skip contacts.
    """
    cc = db.get(CampaignContact, (campaign_id, contact_id))
    if cc is None or cc.status != "calling":
        return
    cc.status = "done" if call_ok else "failed"
    campaign = db.get(Campaign, campaign_id)
    if campaign is not None and campaign.status == "running":
        if next_pending_contact(db, campaign_id) is None:
            campaign.status = "completed"
    db.commit()
