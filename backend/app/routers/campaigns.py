import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models import Call, Campaign, CampaignContact, Contact
from app.schemas import CampaignContactOut, CampaignCreate, CampaignDetail, CampaignOut, CampaignUpdate

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


def _get_or_404(db: Session, campaign_id: uuid.UUID) -> Campaign:
    campaign = db.get(
        Campaign,
        campaign_id,
        options=[selectinload(Campaign.contacts).selectinload(CampaignContact.contact)],
    )
    if campaign is None:
        raise HTTPException(404, "Campaign not found")
    return campaign


def _counts(campaign: Campaign) -> tuple[int, int]:
    total = len(campaign.contacts)
    called = sum(1 for cc in campaign.contacts if cc.status in ("done", "failed"))
    return total, called


def _to_out(campaign: Campaign) -> CampaignOut:
    total, called = _counts(campaign)
    out = CampaignOut.model_validate(campaign)
    out.total_contacts, out.called_contacts = total, called
    return out


def _set_contacts(db: Session, campaign: Campaign, contact_ids: list[uuid.UUID]) -> None:
    found = db.scalars(select(Contact.id).where(Contact.id.in_(contact_ids))).all()
    missing = set(contact_ids) - set(found)
    if missing:
        raise HTTPException(400, f"Unknown contact ids: {sorted(str(m) for m in missing)}")
    campaign.contacts = [
        CampaignContact(campaign_id=campaign.id, contact_id=cid) for cid in contact_ids
    ]


@router.get("", response_model=list[CampaignOut])
def list_campaigns(db: Session = Depends(get_db)):
    campaigns = db.scalars(
        select(Campaign)
        .options(selectinload(Campaign.contacts))
        .order_by(Campaign.created_at.desc())
    ).all()
    return [_to_out(c) for c in campaigns]


@router.post("", response_model=CampaignOut, status_code=201)
def create_campaign(payload: CampaignCreate, db: Session = Depends(get_db)):
    campaign = Campaign(name=payload.name, goal=payload.goal, script_prompt=payload.script_prompt)
    db.add(campaign)
    db.flush()
    _set_contacts(db, campaign, payload.contact_ids)
    db.commit()
    return _to_out(_get_or_404(db, campaign.id))


@router.get("/{campaign_id}", response_model=CampaignDetail)
def get_campaign(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    campaign = _get_or_404(db, campaign_id)
    detail = CampaignDetail.model_validate(campaign)
    detail.total_contacts, detail.called_contacts = _counts(campaign)

    latest_calls = {
        c.contact_id: c
        for c in db.scalars(
            select(Call)
            .where(Call.campaign_id == campaign_id)
            .order_by(Call.started_at.asc().nulls_first())
        )
    }
    rows = []
    for cc in campaign.contacts:
        row = CampaignContactOut.model_validate(cc)
        call = latest_calls.get(cc.contact_id)
        if call is not None:
            row.disposition = call.disposition
            row.disposition_summary = call.disposition_summary
            row.call_id = call.id
        rows.append(row)
    detail.contact_rows = rows
    return detail


@router.patch("/{campaign_id}", response_model=CampaignOut)
def update_campaign(campaign_id: uuid.UUID, payload: CampaignUpdate, db: Session = Depends(get_db)):
    campaign = _get_or_404(db, campaign_id)
    if campaign.status != "draft":
        raise HTTPException(409, "Only draft campaigns can be edited")
    data = payload.model_dump(exclude_unset=True)
    contact_ids = data.pop("contact_ids", None)
    for key, value in data.items():
        setattr(campaign, key, value)
    if contact_ids is not None:
        _set_contacts(db, campaign, contact_ids)
    db.commit()
    return _to_out(_get_or_404(db, campaign.id))


@router.post("/{campaign_id}/start", response_model=CampaignOut)
def start_campaign(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    from app.services import dialer

    dialer.start_campaign(db, campaign_id)
    return _to_out(_get_or_404(db, campaign_id))


@router.post("/{campaign_id}/stop", response_model=CampaignOut)
def stop_campaign(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    from app.services import dialer

    dialer.stop_campaign(db, campaign_id)
    return _to_out(_get_or_404(db, campaign_id))


@router.delete("/{campaign_id}", status_code=204)
def delete_campaign(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    db.delete(_get_or_404(db, campaign_id))
    db.commit()
