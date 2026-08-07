"""campaign contact dial order + dialer indexes

Revision ID: b7f1c2d94a30
Revises: 24628453590b
Create Date: 2026-08-08 03:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7f1c2d94a30'
down_revision: Union[str, Sequence[str], None] = '24628453590b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Deterministic dial order: without it the dialer's "next pending contact"
    # and the dashboard's client-side guess can disagree about who is ringing.
    op.add_column(
        "campaign_contacts",
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_campaign_contacts_campaign_position",
        "campaign_contacts",
        ["campaign_id", "position"],
    )
    # The supervisor's stale-call reap and the status-callback fallback both
    # filter on these two columns.
    op.create_index("ix_calls_campaign_contact", "calls", ["campaign_id", "contact_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_calls_campaign_contact", table_name="calls")
    op.drop_index("ix_campaign_contacts_campaign_position", table_name="campaign_contacts")
    op.drop_column("campaign_contacts", "position")
