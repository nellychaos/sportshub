"""Add confirmation_checks table for external confirmation signals.

Revision ID: 004
Revises: 003
Create Date: 2026-03-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "confirmation_checks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", sa.String(50), nullable=False),
        sa.Column("exists", sa.Boolean, nullable=False),
        sa.Column("confidence_boost", sa.Float, nullable=False),
        sa.Column("raw_data", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", "source_id", name="uq_confirmation_checks_event_source"),
    )
    op.create_index("idx_confirmation_checks_event_id", "confirmation_checks", ["event_id"])
    op.create_index("idx_confirmation_checks_source_id", "confirmation_checks", ["source_id"])


def downgrade() -> None:
    op.drop_table("confirmation_checks")
