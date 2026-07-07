"""Sequential campaign dialer.

Without Twilio (simulated mode) the dialer doesn't place PSTN calls: it
surfaces the next pending contact in the dashboard, where the user answers
as that contact over a web-call. `advance_after_call` runs after every
campaign call and keeps the queue moving; when Twilio arrives, telephony
origination slots in behind the same functions (see services/telephony.py).
"""

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Campaign, CampaignContact


def start_campaign(db: Session, campaign_id: uuid.UUID) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaign not found")
    if campaign.status == "running":
        raise HTTPException(409, "Campaign is already running")
    if not campaign.contacts:
        raise HTTPException(400, "Campaign has no contacts")
    campaign.status = "running"
    # re-queue anything that was mid-call when a previous run stopped
    for cc in campaign.contacts:
        if cc.status == "calling":
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
    return db.scalars(
        select(CampaignContact)
        .where(
            CampaignContact.campaign_id == campaign_id,
            CampaignContact.status == "pending",
        )
        .limit(1)
    ).first()


def mark_calling(db: Session, campaign_id: uuid.UUID, contact_id: uuid.UUID) -> None:
    cc = db.get(CampaignContact, (campaign_id, contact_id))
    if cc is not None and cc.status == "pending":
        cc.status = "calling"
        db.commit()


def advance_after_call(
    db: Session, campaign_id: uuid.UUID, contact_id: uuid.UUID, call_ok: bool
) -> None:
    """Mark the contact done/failed; complete the campaign when queue empties."""
    cc = db.get(CampaignContact, (campaign_id, contact_id))
    if cc is not None:
        cc.status = "done" if call_ok else "failed"
    campaign = db.get(Campaign, campaign_id)
    if campaign is not None and campaign.status == "running":
        if next_pending_contact(db, campaign_id) is None:
            campaign.status = "completed"
    db.commit()
