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


class CampaignStartRequest(BaseModel):
    # required in real dialing mode — guards against starting the wrong campaign
    confirm_real: bool = False


class CampaignContactOut(ORMModel):
    contact: ContactOut
    status: str
    disposition: str | None = None
    disposition_summary: str | None = None
    call_id: uuid.UUID | None = None
    call_status: str | None = None
    # None in simulated mode; False when the number is not allowlisted
    dialable: bool | None = None


class CampaignOut(ORMModel):
    id: uuid.UUID
    name: str
    goal: str | None
    script_prompt: str | None
    status: str
    created_at: datetime
    total_contacts: int = 0
    called_contacts: int = 0
    dialing_mode: str = "simulated"  # simulated | twilio


class CampaignDetail(CampaignOut):
    contact_rows: list[CampaignContactOut] = []


# --- Calls ---


class OutboundCallRequest(BaseModel):
    contact_id: uuid.UUID


class TranscriptTurnOut(ORMModel):
    id: int
    role: str
    content: str
    ts: datetime


class CallOut(ORMModel):
    id: uuid.UUID
    direction: str
    twilio_sid: str | None = None
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


# --- Knowledge ---


class AgentProfileOut(ORMModel):
    company_name: str
    greeting_template: str
    persona: str | None
    faq_threshold: float
    rag_top_k: int
    rag_min_score: float
    updated_at: datetime


class AgentProfileUpdate(BaseModel):
    company_name: str | None = Field(default=None, min_length=1, max_length=120)
    greeting_template: str | None = Field(default=None, max_length=600)
    persona: str | None = Field(default=None, max_length=4000)
    faq_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    rag_top_k: int | None = Field(default=None, ge=1, le=20)
    rag_min_score: float | None = Field(default=None, ge=0.0, le=1.0)


class FaqCreate(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    # spoken to the caller verbatim by TTS, so keep it short and speakable
    answer: str = Field(min_length=1, max_length=600)
    enabled: bool = True


class FaqUpdate(BaseModel):
    question: str | None = Field(default=None, min_length=3, max_length=500)
    answer: str | None = Field(default=None, min_length=1, max_length=600)
    enabled: bool | None = None


class FaqOut(ORMModel):
    id: uuid.UUID
    question: str
    answer: str
    enabled: bool
    hit_count: int
    created_at: datetime
    # False until the question has been embedded; such rows never match
    indexed: bool = False


class KbDocumentOut(ORMModel):
    id: uuid.UUID
    title: str
    filename: str
    content_type: str
    size_bytes: int
    status: str  # pending | processing | ready | failed
    error: str | None
    chunk_count: int
    created_at: datetime


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int | None = Field(default=None, ge=1, le=20)


class FaqMatchOut(BaseModel):
    id: uuid.UUID
    question: str
    answer: str
    score: float


class ChunkHitOut(BaseModel):
    title: str
    content: str
    score: float


class KnowledgeSearchResponse(BaseModel):
    # the top FAQ regardless of threshold, so near-misses are visible
    faq: FaqMatchOut | None = None
    threshold: float
    would_bypass_llm: bool
    chunks: list[ChunkHitOut] = []
