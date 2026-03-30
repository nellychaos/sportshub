"""Add timezone tracking columns to source_records and events.

Revision ID: 003
Revises: 002
Create Date: 2026-03-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # source_records: track what timezone the source reported in
    op.add_column(
        "source_records",
        sa.Column("source_timezone", sa.String(50), nullable=True),
    )

    # events: track the resolved venue timezone for the canonical event
    op.add_column(
        "events",
        sa.Column("venue_timezone", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("events", "venue_timezone")
    op.drop_column("source_records", "source_timezone")
