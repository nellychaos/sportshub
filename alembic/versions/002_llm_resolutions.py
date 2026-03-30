"""Add llm_resolutions table for LLM-based entity resolution tracking.

Revision ID: 002
Revises: 001
Create Date: 2026-03-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_resolutions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id"), nullable=False),
        sa.Column("resolution_type", sa.String(20), nullable=False),
        sa.Column("input_text", sa.Text, nullable=False),
        sa.Column("candidates", JSONB, nullable=False),
        sa.Column("llm_output", JSONB, nullable=False),
        sa.Column("resolved_id", UUID(as_uuid=True), nullable=True),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("accepted", sa.Boolean, nullable=False),
        sa.Column("acceptance_reason", sa.String(50), nullable=True),
        sa.Column("alias_created", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("model", sa.String(50), nullable=False),
        sa.Column("tokens_used", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "resolution_type IN ('team_name', 'event_match')",
            name="llm_resolutions_valid_type",
        ),
    )
    op.create_index("idx_llm_resolutions_type", "llm_resolutions", ["resolution_type", "created_at"])
    op.create_index("idx_llm_resolutions_accepted", "llm_resolutions", ["accepted", "created_at"])
    op.create_index("idx_llm_resolutions_source_record", "llm_resolutions", ["source_record_id"])


def downgrade() -> None:
    op.drop_table("llm_resolutions")
