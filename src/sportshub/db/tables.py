"""SQLAlchemy Core table definitions for Sportshub.

Uses Table objects (not Declarative ORM) to keep Pydantic domain models
decoupled from persistence. Repositories translate between rows and models.
"""

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

# Naming convention for constraints (helps Alembic auto-generate)
convention = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=convention)

SPORT_CHECK = CheckConstraint("sport IN ('nba', 'lol', 'football')", name="valid_sport")

# ─── Teams ────────────────────────────────────────────────────────────────────

teams_table = Table(
    "teams",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
    Column("name", String(200), nullable=False),
    Column("short_name", String(100), nullable=False),
    Column("abbreviation", String(10), nullable=False),
    Column("sport", String(20), nullable=False),
    Column("active", Boolean, server_default=sa.text("true"), nullable=False),
    Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    UniqueConstraint("abbreviation", "sport", name="uq_teams_abbreviation_sport"),
    CheckConstraint("sport IN ('nba', 'lol', 'football')", name="teams_valid_sport"),
)

Index("idx_teams_sport", teams_table.c.sport)
Index("idx_teams_sport_active", teams_table.c.sport, teams_table.c.active)
Index("idx_teams_name_trgm", teams_table.c.name, postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"})

# ─── Team Aliases ─────────────────────────────────────────────────────────────

team_aliases_table = Table(
    "team_aliases",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
    Column("team_id", UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
    Column("alias", String(200), nullable=False),
    Column("alias_normalized", String(200), nullable=False),
    Column("source_id", String(50), nullable=False),
    Column("is_primary", Boolean, server_default=sa.text("false"), nullable=False),
    UniqueConstraint("alias_normalized", "source_id", name="uq_team_aliases_normalized_source"),
)

Index("idx_team_aliases_normalized", team_aliases_table.c.alias_normalized)
Index("idx_team_aliases_team_id", team_aliases_table.c.team_id)
Index(
    "idx_team_aliases_trgm",
    team_aliases_table.c.alias_normalized,
    postgresql_using="gin",
    postgresql_ops={"alias_normalized": "gin_trgm_ops"},
)

# ─── Players ──────────────────────────────────────────────────────────────────

players_table = Table(
    "players",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
    Column("team_id", UUID(as_uuid=True), ForeignKey("teams.id", ondelete="SET NULL"), nullable=True),
    Column("name", String(200), nullable=False),
    Column("sport", String(20), nullable=False),
    Column("position", String(50), nullable=True),
    Column("role", String(50), nullable=True),
    Column("jersey_number", String(10), nullable=True),
    Column("nationality", String(100), nullable=True),
    Column("active", Boolean, server_default=sa.text("true"), nullable=False),
    Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    UniqueConstraint("sport", "name", "team_id", name="uq_players_sport_name_team"),
    CheckConstraint("sport IN ('nba', 'lol', 'football')", name="players_valid_sport"),
)

Index("idx_players_team", players_table.c.team_id)
Index("idx_players_sport", players_table.c.sport)
Index("idx_players_sport_active", players_table.c.sport, players_table.c.active)
Index("idx_players_metadata", players_table.c.metadata, postgresql_using="gin")

# ─── Player Aliases ───────────────────────────────────────────────────────────

player_aliases_table = Table(
    "player_aliases",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
    Column("player_id", UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
    Column("alias", String(200), nullable=False),
    Column("alias_normalized", String(200), nullable=False),
    Column("source_id", String(50), nullable=False),
    Column("is_primary", Boolean, server_default=sa.text("false"), nullable=False),
    UniqueConstraint("alias_normalized", "source_id", name="uq_player_aliases_normalized_source"),
)

Index("idx_player_aliases_normalized", player_aliases_table.c.alias_normalized)
Index("idx_player_aliases_player_id", player_aliases_table.c.player_id)
Index(
    "idx_player_aliases_trgm",
    player_aliases_table.c.alias_normalized,
    postgresql_using="gin",
    postgresql_ops={"alias_normalized": "gin_trgm_ops"},
)

# ─── Competitions ─────────────────────────────────────────────────────────────

competitions_table = Table(
    "competitions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
    Column("name", String(300), nullable=False),
    Column("short_name", String(100), nullable=False),
    Column("sport", String(20), nullable=False),
    Column("season", String(50), nullable=True),
    Column("region", String(100), nullable=True),
    Column("tier", String(20), nullable=True),
    Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
    Column("start_date", DateTime(timezone=True), nullable=True),
    Column("end_date", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    CheckConstraint("sport IN ('nba', 'lol', 'football')", name="competitions_valid_sport"),
)

Index("idx_competitions_sport", competitions_table.c.sport)

# ─── Events (Core) ───────────────────────────────────────────────────────────

events_table = Table(
    "events",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
    Column("sport", String(20), nullable=False),
    Column("competition_id", UUID(as_uuid=True), ForeignKey("competitions.id"), nullable=False),
    Column("home_team_id", UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False),
    Column("away_team_id", UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False),
    Column("scheduled_at", DateTime(timezone=True), nullable=False),
    Column("status", String(20), server_default=sa.text("'scheduled'"), nullable=False),
    Column("match_format", String(10), server_default=sa.text("'single'"), nullable=False),
    Column("venue", String(300), nullable=True),
    Column("venue_timezone", String(50), nullable=True),
    Column("confidence_score", Float, server_default=sa.text("1.0"), nullable=False),
    Column("source_count", Integer, server_default=sa.text("1"), nullable=False),
    Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    UniqueConstraint("sport", "home_team_id", "away_team_id", "scheduled_at", name="uq_events_dedup"),
    CheckConstraint("sport IN ('nba', 'lol', 'football')", name="events_valid_sport"),
    CheckConstraint("status IN ('scheduled', 'postponed', 'cancelled')", name="events_valid_status"),
    CheckConstraint("match_format IN ('single', 'bo3', 'bo5', 'bo7')", name="events_valid_format"),
    CheckConstraint("confidence_score >= 0.0 AND confidence_score <= 1.0", name="events_valid_confidence"),
)

Index(
    "idx_events_sport_scheduled",
    events_table.c.sport,
    events_table.c.scheduled_at,
    postgresql_where=events_table.c.status == "scheduled",
)
Index("idx_events_home_team", events_table.c.home_team_id, events_table.c.scheduled_at)
Index("idx_events_away_team", events_table.c.away_team_id, events_table.c.scheduled_at)
Index("idx_events_competition", events_table.c.competition_id, events_table.c.scheduled_at)
Index(
    "idx_events_scheduled_at",
    events_table.c.scheduled_at,
    postgresql_where=events_table.c.status == "scheduled",
)
Index("idx_events_sport_status_scheduled", events_table.c.sport, events_table.c.status, events_table.c.scheduled_at)

# ─── Source Records ───────────────────────────────────────────────────────────

source_records_table = Table(
    "source_records",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
    Column("event_id", UUID(as_uuid=True), ForeignKey("events.id", ondelete="SET NULL"), nullable=True),
    Column("source_id", String(50), nullable=False),
    Column("source_event_id", String(200), nullable=False),
    Column("sport", String(20), nullable=False),
    Column("raw_home_team", String(200), nullable=False),
    Column("raw_away_team", String(200), nullable=False),
    Column("raw_competition", String(200), nullable=False),
    Column("scheduled_at", DateTime(timezone=True), nullable=False),
    Column("venue", String(300), nullable=True),
    Column("raw_data", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
    Column("source_timezone", String(50), nullable=True),
    Column("match_confidence", Float, nullable=True),
    Column("ingestion_run_id", UUID(as_uuid=True), ForeignKey("ingestion_runs.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    UniqueConstraint("source_id", "source_event_id", name="uq_source_records_source_event"),
    CheckConstraint("sport IN ('nba', 'lol', 'football')", name="source_records_valid_sport"),
)

Index("idx_source_records_event_id", source_records_table.c.event_id)
Index("idx_source_records_source", source_records_table.c.source_id, source_records_table.c.created_at)
Index(
    "idx_source_records_unmatched",
    source_records_table.c.sport,
    source_records_table.c.created_at,
    postgresql_where=source_records_table.c.event_id.is_(None),
)

# ─── Ingestion Runs ──────────────────────────────────────────────────────────

ingestion_runs_table = Table(
    "ingestion_runs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
    Column("source_id", String(50), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("status", String(20), server_default=sa.text("'running'"), nullable=False),
    Column("records_fetched", Integer, server_default=sa.text("0"), nullable=False),
    Column("records_new", Integer, server_default=sa.text("0"), nullable=False),
    Column("records_updated", Integer, server_default=sa.text("0"), nullable=False),
    Column("records_matched", Integer, server_default=sa.text("0"), nullable=False),
    Column("error_message", Text, nullable=True),
    Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
    CheckConstraint(
        "status IN ('running', 'success', 'partial', 'failed')",
        name="ingestion_runs_valid_status",
    ),
)

Index("idx_ingestion_runs_source", ingestion_runs_table.c.source_id, ingestion_runs_table.c.started_at)

# ─── LLM Resolutions ────────────────────────────────────────────────────────

llm_resolutions_table = Table(
    "llm_resolutions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
    Column("source_record_id", UUID(as_uuid=True), ForeignKey("source_records.id"), nullable=False),
    Column("resolution_type", String(20), nullable=False),
    Column("input_text", Text, nullable=False),
    Column("candidates", JSONB, nullable=False),
    Column("llm_output", JSONB, nullable=False),
    Column("resolved_id", UUID(as_uuid=True), nullable=True),
    Column("confidence", Float, nullable=False),
    Column("accepted", Boolean, nullable=False),
    Column("acceptance_reason", String(50), nullable=True),
    Column("alias_created", Boolean, server_default=sa.text("false"), nullable=False),
    Column("latency_ms", Integer, nullable=False),
    Column("model", String(50), nullable=False),
    Column("tokens_used", Integer, nullable=True),
    Column("created_at", DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    CheckConstraint("resolution_type IN ('team_name', 'event_match')", name="llm_resolutions_valid_type"),
)

Index("idx_llm_resolutions_type", llm_resolutions_table.c.resolution_type, llm_resolutions_table.c.created_at)
Index("idx_llm_resolutions_accepted", llm_resolutions_table.c.accepted, llm_resolutions_table.c.created_at)
Index("idx_llm_resolutions_source_record", llm_resolutions_table.c.source_record_id)

# ─── Confirmation Checks ────────────────────────────────────────────────

confirmation_checks_table = Table(
    "confirmation_checks",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
    Column("event_id", UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
    Column("source_id", String(50), nullable=False),
    Column("exists", Boolean, nullable=False),
    Column("confidence_boost", Float, nullable=False),
    Column("raw_data", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
    Column("checked_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("event_id", "source_id", name="uq_confirmation_checks_event_source"),
)

Index("idx_confirmation_checks_event_id", confirmation_checks_table.c.event_id)
Index("idx_confirmation_checks_source_id", confirmation_checks_table.c.source_id)

# ─── Reconciliation Results ──────────────────────────────────────────────────

reconciliation_results_table = Table(
    "reconciliation_results",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
    Column("event_id", UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
    Column("event_occurred", Boolean, nullable=False),
    Column("time_diff_seconds", Integer, nullable=True),
    Column("teams_correct", Boolean, nullable=False),
    Column("venue_correct", Boolean, nullable=False),
    Column("reconciled_at", DateTime(timezone=True), nullable=False),
    Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
    UniqueConstraint("event_id", name="uq_reconciliation_results_event_id"),
)

# ─── Source Accuracy Records ─────────────────────────────────────────────────

source_accuracy_records_table = Table(
    "source_accuracy_records",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
    Column("reconciliation_id", UUID(as_uuid=True), ForeignKey("reconciliation_results.id", ondelete="CASCADE"), nullable=False),
    Column("source_id", String(50), nullable=False),
    Column("sport", String(20), nullable=False),
    Column("time_diff_seconds", Integer, nullable=True),
    Column("time_accurate", Boolean, nullable=False),
    Column("teams_correct", Boolean, nullable=False),
    Column("venue_correct", Boolean, nullable=False),
    Column("recorded_at", DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    CheckConstraint("sport IN ('nba', 'lol', 'football')", name="source_accuracy_valid_sport"),
)

Index(
    "idx_source_accuracy_source_sport_recorded",
    source_accuracy_records_table.c.source_id,
    source_accuracy_records_table.c.sport,
    source_accuracy_records_table.c.recorded_at,
)
