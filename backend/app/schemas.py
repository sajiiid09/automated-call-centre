import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Contacts ---


class ContactCreate(BaseModel):
    name: str = Field(min_length=1)
    phone: str = Field(min_length=5, pattern=r"^\+?[0-9 ()-]{5,20}$")
    notes: str | None = None


class ContactUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    notes: str | None = None


class ContactOut(ORMModel):
    id: uuid.UUID
    name: str
    phone: str
    notes: str | None
    created_at: datetime


class ImportResult(BaseModel):
    imported: int
    skipped: int
    errors: list[str]


# --- Campaigns ---


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1)
    goal: str | None = None
    script_prompt: str | None = None
    contact_ids: list[uuid.UUID] = []


class CampaignUpdate(BaseModel):
    name: str | None = None
    goal: str | None = None
    script_prompt: str | None = None
    contact_ids: list[uuid.UUID] | None = None


class CampaignContactOut(ORMModel):
    contact: ContactOut
    status: str
    disposition: str | None = None
    disposition_summary: str | None = None
    call_id: uuid.UUID | None = None


class CampaignOut(ORMModel):
    id: uuid.UUID
    name: str
    goal: str | None
    script_prompt: str | None
    status: str
    created_at: datetime
    total_contacts: int = 0
    called_contacts: int = 0


class CampaignDetail(CampaignOut):
    contact_rows: list[CampaignContactOut] = []


# --- Calls ---


class TranscriptTurnOut(ORMModel):
    id: int
    role: str
    content: str
    ts: datetime


class CallOut(ORMModel):
    id: uuid.UUID
    direction: str
    status: str
    disposition: str | None
    disposition_summary: str | None
    contact_id: uuid.UUID | None
    campaign_id: uuid.UUID | None
    contact_name: str | None = None
    campaign_name: str | None = None
    from_number: str | None
    to_number: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None


class CallDetail(CallOut):
    turns: list[TranscriptTurnOut] = []
