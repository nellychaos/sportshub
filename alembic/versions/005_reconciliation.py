"""Add reconciliation_results and source_accuracy_records tables.

Revision ID: 005
Revises: 004
Create Date: 2026-03-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Update events status check constraint to include 'completed'
    op.execute("ALTER TABLE events DROP CONSTRAINT ck_events_events_valid_status")
    op.execute(
        "ALTER TABLE events ADD CONSTRAINT ck_events_events_valid_status "
        "CHECK (status IN ('scheduled', 'postponed', 'cancelled', 'completed'))"
    )

    # Reconciliation results table
    op.create_table(
        "reconciliation_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_occurred", sa.Boolean, nullable=False),
        sa.Column("time_diff_seconds", sa.Integer, nullable=True),
        sa.Column("teams_correct", sa.Boolean, nullable=False),
        sa.Column("venue_correct", sa.Boolean, nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_reconciliation_results_event_id"),
    )

    # Source accuracy records table
    op.create_table(
        "source_accuracy_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column(
            "reconciliation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("reconciliation_results.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_id", sa.String(50), nullable=False),
        sa.Column("sport", sa.String(20), nullable=False),
        sa.Column("time_diff_seconds", sa.Integer, nullable=True),
        sa.Column("time_accurate", sa.Boolean, nullable=False),
        sa.Column("teams_correct", sa.Boolean, nullable=False),
        sa.Column("venue_correct", sa.Boolean, nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("sport IN ('nba', 'lol', 'soccer')", name="source_accuracy_valid_sport"),
    )

    op.create_index(
        "idx_source_accuracy_source_sport_recorded",
        "source_accuracy_records",
        ["source_id", "sport", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_table("source_accuracy_records")
    op.drop_table("reconciliation_results")

    # Restore original events status constraint
    op.execute("ALTER TABLE events DROP CONSTRAINT ck_events_events_valid_status")
    op.execute(
        "ALTER TABLE events ADD CONSTRAINT ck_events_events_valid_status "
        "CHECK (status IN ('scheduled', 'postponed', 'cancelled'))"
    )
