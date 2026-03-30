"""Initial schema — all 9 tables, indexes, triggers.

Revision ID: 001
Revises: None
Create Date: 2026-03-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Extensions
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')

    # ─── Ingestion Runs (created first since source_records references it) ────
    op.create_table(
        "ingestion_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("source_id", sa.String(50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), server_default=sa.text("'running'"), nullable=False),
        sa.Column("records_fetched", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("records_new", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("records_updated", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("records_matched", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.CheckConstraint("status IN ('running', 'success', 'partial', 'failed')", name="ingestion_runs_valid_status"),
    )
    op.create_index("idx_ingestion_runs_source", "ingestion_runs", ["source_id", "started_at"])

    # ─── Teams ────────────────────────────────────────────────────────────────
    op.create_table(
        "teams",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("short_name", sa.String(100), nullable=False),
        sa.Column("abbreviation", sa.String(10), nullable=False),
        sa.Column("sport", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("abbreviation", "sport", name="uq_teams_abbreviation_sport"),
        sa.CheckConstraint("sport IN ('nba', 'lol', 'soccer')", name="teams_valid_sport"),
    )
    op.create_index("idx_teams_sport", "teams", ["sport"])
    op.create_index("idx_teams_sport_active", "teams", ["sport", "active"])
    op.execute("CREATE INDEX idx_teams_name_trgm ON teams USING gin (name gin_trgm_ops)")

    # ─── Team Aliases ─────────────────────────────────────────────────────────
    op.create_table(
        "team_aliases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(200), nullable=False),
        sa.Column("alias_normalized", sa.String(200), nullable=False),
        sa.Column("source_id", sa.String(50), nullable=False),
        sa.Column("is_primary", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.UniqueConstraint("alias_normalized", "source_id", name="uq_team_aliases_normalized_source"),
    )
    op.create_index("idx_team_aliases_normalized", "team_aliases", ["alias_normalized"])
    op.create_index("idx_team_aliases_team_id", "team_aliases", ["team_id"])
    op.execute("CREATE INDEX idx_team_aliases_trgm ON team_aliases USING gin (alias_normalized gin_trgm_ops)")

    # ─── Players ──────────────────────────────────────────────────────────────
    op.create_table(
        "players",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("sport", sa.String(20), nullable=False),
        sa.Column("position", sa.String(50), nullable=True),
        sa.Column("role", sa.String(50), nullable=True),
        sa.Column("jersey_number", sa.String(10), nullable=True),
        sa.Column("nationality", sa.String(100), nullable=True),
        sa.Column("active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("sport", "name", "team_id", name="uq_players_sport_name_team"),
        sa.CheckConstraint("sport IN ('nba', 'lol', 'soccer')", name="players_valid_sport"),
    )
    op.create_index("idx_players_team", "players", ["team_id"])
    op.create_index("idx_players_sport", "players", ["sport"])
    op.create_index("idx_players_sport_active", "players", ["sport", "active"])
    op.execute("CREATE INDEX idx_players_metadata ON players USING gin (metadata)")

    # ─── Player Aliases ───────────────────────────────────────────────────────
    op.create_table(
        "player_aliases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("player_id", UUID(as_uuid=True), sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(200), nullable=False),
        sa.Column("alias_normalized", sa.String(200), nullable=False),
        sa.Column("source_id", sa.String(50), nullable=False),
        sa.Column("is_primary", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.UniqueConstraint("alias_normalized", "source_id", name="uq_player_aliases_normalized_source"),
    )
    op.create_index("idx_player_aliases_normalized", "player_aliases", ["alias_normalized"])
    op.create_index("idx_player_aliases_player_id", "player_aliases", ["player_id"])
    op.execute("CREATE INDEX idx_player_aliases_trgm ON player_aliases USING gin (alias_normalized gin_trgm_ops)")

    # ─── Competitions ─────────────────────────────────────────────────────────
    op.create_table(
        "competitions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("short_name", sa.String(100), nullable=False),
        sa.Column("sport", sa.String(20), nullable=False),
        sa.Column("season", sa.String(50), nullable=True),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column("tier", sa.String(20), nullable=True),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("sport IN ('nba', 'lol', 'soccer')", name="competitions_valid_sport"),
    )
    op.create_index("idx_competitions_sport", "competitions", ["sport"])

    # ─── Events (Core) ───────────────────────────────────────────────────────
    op.create_table(
        "events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("sport", sa.String(20), nullable=False),
        sa.Column("competition_id", UUID(as_uuid=True), sa.ForeignKey("competitions.id"), nullable=False),
        sa.Column("home_team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("away_team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'scheduled'"), nullable=False),
        sa.Column("match_format", sa.String(10), server_default=sa.text("'single'"), nullable=False),
        sa.Column("venue", sa.String(300), nullable=True),
        sa.Column("confidence_score", sa.Float, server_default=sa.text("1.0"), nullable=False),
        sa.Column("source_count", sa.Integer, server_default=sa.text("1"), nullable=False),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("sport", "home_team_id", "away_team_id", "scheduled_at", name="uq_events_dedup"),
        sa.CheckConstraint("sport IN ('nba', 'lol', 'soccer')", name="events_valid_sport"),
        sa.CheckConstraint("status IN ('scheduled', 'postponed', 'cancelled')", name="events_valid_status"),
        sa.CheckConstraint("match_format IN ('single', 'bo3', 'bo5', 'bo7')", name="events_valid_format"),
        sa.CheckConstraint("confidence_score >= 0.0 AND confidence_score <= 1.0", name="events_valid_confidence"),
    )
    op.execute("CREATE INDEX idx_events_sport_scheduled ON events (sport, scheduled_at) WHERE status = 'scheduled'")
    op.create_index("idx_events_home_team", "events", ["home_team_id", "scheduled_at"])
    op.create_index("idx_events_away_team", "events", ["away_team_id", "scheduled_at"])
    op.create_index("idx_events_competition", "events", ["competition_id", "scheduled_at"])
    op.execute("CREATE INDEX idx_events_scheduled_at ON events (scheduled_at) WHERE status = 'scheduled'")
    op.create_index("idx_events_sport_status_scheduled", "events", ["sport", "status", "scheduled_at"])

    # ─── Source Records ───────────────────────────────────────────────────────
    op.create_table(
        "source_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_id", sa.String(50), nullable=False),
        sa.Column("source_event_id", sa.String(200), nullable=False),
        sa.Column("sport", sa.String(20), nullable=False),
        sa.Column("raw_home_team", sa.String(200), nullable=False),
        sa.Column("raw_away_team", sa.String(200), nullable=False),
        sa.Column("raw_competition", sa.String(200), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("venue", sa.String(300), nullable=True),
        sa.Column("raw_data", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("match_confidence", sa.Float, nullable=True),
        sa.Column("ingestion_run_id", UUID(as_uuid=True), sa.ForeignKey("ingestion_runs.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("source_id", "source_event_id", name="uq_source_records_source_event"),
        sa.CheckConstraint("sport IN ('nba', 'lol', 'soccer')", name="source_records_valid_sport"),
    )
    op.create_index("idx_source_records_event_id", "source_records", ["event_id"])
    op.create_index("idx_source_records_source", "source_records", ["source_id", "created_at"])
    op.execute("CREATE INDEX idx_source_records_unmatched ON source_records (sport, created_at) WHERE event_id IS NULL")

    # ─── Triggers: auto-update updated_at ─────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    for table_name in ("events", "teams", "competitions"):
        op.execute(f"""
            CREATE TRIGGER trg_{table_name}_updated_at
            BEFORE UPDATE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at();
        """)


def downgrade() -> None:
    for table_name in ("events", "teams", "competitions"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_updated_at ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at()")

    op.drop_table("source_records")
    op.drop_table("events")
    op.drop_table("competitions")
    op.drop_table("player_aliases")
    op.drop_table("players")
    op.drop_table("team_aliases")
    op.drop_table("teams")
    op.drop_table("ingestion_runs")
