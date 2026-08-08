"""knowledge base: agent profile, documents, chunks, faqs

Revision ID: c4a91e6b7d02
Revises: b7f1c2d94a30
Create Date: 2026-08-09 02:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = 'c4a91e6b7d02'
down_revision: Union[str, Sequence[str], None] = 'b7f1c2d94a30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Must equal app.models.EMBEDDING_DIM. Hardcoded rather than read from settings
# because a column width is DDL, and a migration whose shape depends on the
# environment stops being reproducible.
EMBEDDING_DIM = 1024


def upgrade() -> None:
    """Upgrade schema."""
    # Must come first: every vector() column below depends on it. Requires the
    # pgvector/pgvector image — stock postgres:16-alpine does not ship it.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "agent_profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_name", sa.Text(), nullable=False, server_default="the company"),
        sa.Column("greeting_template", sa.Text(), nullable=False, server_default=""),
        sa.Column("persona", sa.Text(), nullable=True),
        sa.Column("faq_threshold", sa.Float(), nullable=False, server_default="0.82"),
        sa.Column("rag_top_k", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("rag_min_score", sa.Float(), nullable=False, server_default="0.25"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_agent_profile_singleton"),
    )
    # Seed the singleton so a fresh deploy has a profile without a first request.
    # services.knowledge.get_or_create_profile covers the create_all test path.
    op.execute(
        """
        INSERT INTO agent_profile (id, company_name, greeting_template)
        VALUES (1, 'the company',
                'Hello! Thanks for calling $company_name. How can I help you today?')
        ON CONFLICT (id) DO NOTHING
        """
    )

    op.create_table(
        "kb_documents",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "kb_chunks",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["document_id"], ["kb_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_kb_chunks_document_ordinal"),
    )
    op.create_index("ix_kb_chunks_document_id", "kb_chunks", ["document_id"])

    op.create_table(
        "faqs",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Cosine ANN indexes. Cheap to build at this corpus size, and they are what
    # keeps the per-turn lookup inside the sub-second call budget as it grows.
    op.create_index(
        "ix_kb_chunks_embedding_hnsw",
        "kb_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_faqs_embedding_hnsw",
        "faqs",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_faqs_embedding_hnsw", table_name="faqs")
    op.drop_index("ix_kb_chunks_embedding_hnsw", table_name="kb_chunks")
    op.drop_table("faqs")
    op.drop_index("ix_kb_chunks_document_id", table_name="kb_chunks")
    op.drop_table("kb_chunks")
    op.drop_table("kb_documents")
    op.drop_table("agent_profile")
    # The extension is deliberately left in place — other objects may depend on
    # it, and dropping it is not usefully reversible.
