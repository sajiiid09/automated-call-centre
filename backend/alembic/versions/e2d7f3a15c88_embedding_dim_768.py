"""narrow the vector() columns from 1024 to 768 dimensions

The knowledge base was built against a 1024-wide column, but the embeddings
endpoint only serves jina-embeddings-v5-nano, whose native width is 768. It has
no matryoshka support, so the model cannot be resized to fit the schema — the
schema has to fit the model. Until this ran, every embed raised in
``embeddings._parse`` and, because the knowledge path is fail-open, the agent
silently answered every call with no company knowledge at all.

The HNSW indexes are dropped and rebuilt around the ALTER: an ANN index is built
over vectors of a fixed width and cannot survive a change to it.

Revision ID: e2d7f3a15c88
Revises: c4a91e6b7d02
Create Date: 2026-08-09 17:02:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e2d7f3a15c88'
down_revision: Union[str, Sequence[str], None] = 'c4a91e6b7d02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Hardcoded for the same reason c4a91e6b7d02 hardcodes its own: a column width
# is DDL, and a migration whose shape depends on the environment stops being
# reproducible. Must equal app.models.EMBEDDING_DIM.
NEW_DIM = 768
OLD_DIM = 1024

_INDEXES = (
    ("ix_kb_chunks_embedding_hnsw", "kb_chunks"),
    ("ix_faqs_embedding_hnsw", "faqs"),
)


def _resize(dim: int) -> None:
    for index_name, table in _INDEXES:
        op.drop_index(index_name, table_name=table)

    # USING NULL rather than a cast: vectors of the old width carry no meaning
    # at the new one, so any existing rows must be re-embedded. kb_chunks
    # .embedding is NOT NULL, so its rows are deleted instead — the documents
    # survive and can be re-ingested from /knowledge.
    op.execute("DELETE FROM kb_chunks")
    op.execute(f"ALTER TABLE kb_chunks ALTER COLUMN embedding TYPE vector({dim})")
    op.execute(f"ALTER TABLE faqs ALTER COLUMN embedding TYPE vector({dim}) USING NULL")

    # Documents keep their extracted text, so dropping back to "pending" is a
    # re-index and not a re-upload. Leaving them "ready" with zero chunks would
    # make the dashboard claim a knowledge base that retrieves nothing.
    op.execute("UPDATE kb_documents SET status = 'pending', chunk_count = 0")

    for index_name, table in _INDEXES:
        op.create_index(
            index_name,
            table,
            ["embedding"],
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )


def upgrade() -> None:
    """Upgrade schema."""
    _resize(NEW_DIM)


def downgrade() -> None:
    """Downgrade schema."""
    _resize(OLD_DIM)
