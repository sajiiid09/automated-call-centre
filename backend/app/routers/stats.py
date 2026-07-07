from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Call, Campaign, Contact

router = APIRouter(prefix="/api", tags=["stats"])


class Stats(BaseModel):
    total_calls: int
    total_contacts: int
    active_campaigns: int
    avg_duration_seconds: int | None
    dispositions: dict[str, int]


@router.get("/stats", response_model=Stats)
def get_stats(db: Session = Depends(get_db)):
    total_calls = db.scalar(select(func.count(Call.id))) or 0
    total_contacts = db.scalar(select(func.count(Contact.id))) or 0
    active_campaigns = (
        db.scalar(select(func.count(Campaign.id)).where(Campaign.status == "running")) or 0
    )
    avg_duration = db.scalar(
        select(func.avg(Call.duration_seconds)).where(Call.duration_seconds.is_not(None))
    )
    disposition_rows = db.execute(
        select(Call.disposition, func.count(Call.id))
        .where(Call.disposition.is_not(None))
        .group_by(Call.disposition)
    ).all()
    return Stats(
        total_calls=total_calls,
        total_contacts=total_contacts,
        active_campaigns=active_campaigns,
        avg_duration_seconds=int(avg_duration) if avg_duration is not None else None,
        dispositions={d: n for d, n in disposition_rows},
    )
