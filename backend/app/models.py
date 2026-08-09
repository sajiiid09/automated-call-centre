import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# Width of the vector() columns below. A literal, not settings.embedding_dim:
# this is DDL shape, and reading live config here would let the Alembic-built
# schema and the create_all-built test schema drift apart the moment someone
# edits .env. The migration hardcodes the same number, and app startup asserts
# settings.embedding_dim agrees — changing models requires a migration.
EMBEDDING_DIM = 768


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text)
    phone: Mapped[str] = mapped_column(Text, unique=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    calls: Mapped[list["Call"]] = relationship(back_populates="contact")


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text)
    goal: Mapped[str | None] = mapped_column(Text)
    script_prompt: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="draft")  # draft|running|stopped|completed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    contacts: Mapped[list["CampaignContact"]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="CampaignContact.position",
    )


class CampaignContact(Base):
    __tablename__ = "campaign_contacts"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(Text, default="pending")  # pending|calling|done|failed
    position: Mapped[int] = mapped_column(Integer, default=0)  # dial order

    campaign: Mapped[Campaign] = relationship(back_populates="contacts")
    contact: Mapped[Contact] = relationship()


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[uuid.UUID] = _uuid_pk()
    twilio_sid: Mapped[str | None] = mapped_column(Text, unique=True)
    direction: Mapped[str] = mapped_column(Text)  # inbound|outbound
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL")
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL")
    )
    from_number: Mapped[str | None] = mapped_column(Text)
    to_number: Mapped[str | None] = mapped_column(Text)
    # initiated|ringing|in_progress|completed|failed|no_answer
    status: Mapped[str] = mapped_column(Text, default="initiated")
    # outbound: interested|not_interested|callback|voicemail|failed
    # inbound:  resolved|needs_followup|complaint|enquiry|abandoned
    disposition: Mapped[str | None] = mapped_column(Text)
    disposition_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)

    contact: Mapped[Contact | None] = relationship(back_populates="calls")
    campaign: Mapped[Campaign | None] = relationship()
    turns: Mapped[list["TranscriptTurn"]] = relationship(
        back_populates="call", cascade="all, delete-orphan", order_by="TranscriptTurn.id"
    )


class TranscriptTurn(Base):
    __tablename__ = "transcript_turns"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(Text)  # agent|caller
    content: Mapped[str] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    call: Mapped[Call] = relationship(back_populates="turns")


# --- Knowledge base -------------------------------------------------------


class AgentProfile(Base):
    """Who the agent says it is, and how it uses the knowledge base.

    Exactly one row. An integer PK pinned by a CHECK constraint rather than the
    repo's usual UUID: this is a config row, not an entity, and `id = 1` makes
    the singleton self-enforcing without a partial unique index.
    """

    __tablename__ = "agent_profile"
    __table_args__ = (CheckConstraint("id = 1", name="ck_agent_profile_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    company_name: Mapped[str] = mapped_column(Text, default="the company")
    # $company_name / $contact_name are filled by agent.prompts.render_greeting
    greeting_template: Mapped[str] = mapped_column(Text, default="")
    persona: Mapped[str | None] = mapped_column(Text)
    # cosine similarity a caller utterance must reach to skip the LLM
    faq_threshold: Mapped[float] = mapped_column(Float, default=0.82)
    rag_top_k: Mapped[int] = mapped_column(Integer, default=4)
    rag_min_score: Mapped[float] = mapped_column(Float, default=0.25)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KbDocument(Base):
    """An uploaded document. Stores extracted text, not the original bytes —
    reindexing works without re-upload and there is no blob store to run."""

    __tablename__ = "kb_documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(Text, default="pending")  # pending|processing|ready|failed
    error: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    chunks: Mapped[list["KbChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="KbChunk.ordinal"
    )


class KbChunk(Base):
    __tablename__ = "kb_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_kb_chunks_document_ordinal"),
        # Declared on the model, not only in the migration, so the tests'
        # create_all schema matches production.
        Index(
            "ix_kb_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("kb_documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[KbDocument] = relationship(back_populates="chunks")


class Faq(Base):
    """A canned answer. On a high-confidence match the `answer` is spoken to
    the caller verbatim, with no LLM in the loop — so keep it speakable."""

    __tablename__ = "faqs"
    __table_args__ = (
        Index(
            "ix_faqs_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # NULL until the question has been embedded; such rows never match.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
