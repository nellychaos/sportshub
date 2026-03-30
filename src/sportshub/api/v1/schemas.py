"""API-specific request/response Pydantic models (separate from domain models)."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from sportshub.models.common import PaginationMeta


# ─── Nested summaries ─────────────────────────────────────────────────────────


class TeamSummary(BaseModel):
    id: uuid.UUID
    name: str
    short_name: str
    abbreviation: str
    sport: str


class CompetitionSummary(BaseModel):
    id: uuid.UUID
    name: str
    short_name: str
    sport: str
    season: str | None = None


class SourceInfo(BaseModel):
    source_id: str
    source_event_id: str
    raw_home_team: str
    raw_away_team: str
    scheduled_at: datetime
    venue: str | None = None
    match_confidence: float | None = None
    created_at: datetime


# ─── Enrichment schemas (rosters, stats, injuries) ──────────────────────────


class PlayerSeasonStats(BaseModel):
    """Per-player season statistics."""
    games_played: int | None = None
    minutes_per_game: float | None = None
    points_per_game: float | None = None
    rebounds_per_game: float | None = None
    assists_per_game: float | None = None
    steals_per_game: float | None = None
    blocks_per_game: float | None = None
    turnovers_per_game: float | None = None
    fg_pct: float | None = None
    fg3_pct: float | None = None
    ft_pct: float | None = None
    plus_minus: float | None = None


class PlayerBio(BaseModel):
    """Biographical data for a player."""
    height: str | None = None
    weight: str | None = None
    age: int | None = None
    birthdate: str | None = None
    college: str | None = None
    headshot_url: str | None = None


class RosterPlayer(BaseModel):
    """A player on a team roster for a specific event."""
    player_id: uuid.UUID | None = None
    name: str
    jersey_number: str | None = None
    position: str | None = None
    bio: PlayerBio | None = None
    season_stats: PlayerSeasonStats | None = None


class TeamStats(BaseModel):
    """Team-level aggregate statistics."""
    wins: int | None = None
    losses: int | None = None
    win_pct: float | None = None
    points_per_game: float | None = None
    rebounds_per_game: float | None = None
    assists_per_game: float | None = None
    fg_pct: float | None = None
    fg3_pct: float | None = None
    ft_pct: float | None = None


class InjuryReport(BaseModel):
    """Injury report for a single player."""
    player_name: str
    position: str | None = None
    status: str  # e.g. "Out", "Day-To-Day", "Questionable"
    injury: str | None = None  # e.g. "Knee", "Ankle"
    details: str | None = None


class TeamEnrichment(BaseModel):
    """Full enrichment data for one side of a matchup."""
    record: str | None = None  # e.g. "17-57"
    stats: TeamStats | None = None
    roster: list[RosterPlayer] = []
    injuries: list[InjuryReport] = []


class EventEnrichment(BaseModel):
    """Typed enrichment metadata for an event detail response."""
    home_team: TeamEnrichment | None = None
    away_team: TeamEnrichment | None = None
    enriched_at: datetime | None = None
    enrichment_sources: list[str] = []


# ─── Event responses ──────────────────────────────────────────────────────────


class EventListItem(BaseModel):
    id: uuid.UUID
    sport: str
    home_team: TeamSummary
    away_team: TeamSummary
    competition: CompetitionSummary
    scheduled_at: datetime
    status: str
    match_format: str
    venue: str | None = None
    confidence_score: float
    source_count: int
    created_at: datetime
    updated_at: datetime


class EventListResponse(BaseModel):
    data: list[EventListItem]
    pagination: PaginationMeta


class EventDetailResponse(BaseModel):
    id: uuid.UUID
    sport: str
    home_team: TeamSummary
    away_team: TeamSummary
    competition: CompetitionSummary
    scheduled_at: datetime
    status: str
    match_format: str
    venue: str | None = None
    confidence_score: float
    source_count: int
    sources: list[SourceInfo]
    enrichment: EventEnrichment | None = None
    metadata: dict
    created_at: datetime
    updated_at: datetime


# ─── Team responses ───────────────────────────────────────────────────────────


class TeamResponse(BaseModel):
    id: uuid.UUID
    name: str
    short_name: str
    abbreviation: str
    sport: str
    active: bool
    metadata: dict
    created_at: datetime
    updated_at: datetime


class TeamListResponse(BaseModel):
    data: list[TeamResponse]
    pagination: PaginationMeta


# ─── Player responses ─────────────────────────────────────────────────────────


class PlayerAliasResponse(BaseModel):
    alias: str
    source_id: str
    is_primary: bool


class PlayerResponse(BaseModel):
    id: uuid.UUID
    name: str
    sport: str
    team_id: uuid.UUID | None
    position: str | None
    role: str | None
    jersey_number: str | None
    nationality: str | None
    active: bool
    metadata: dict
    created_at: datetime
    updated_at: datetime


class PlayerDetailResponse(PlayerResponse):
    aliases: list[PlayerAliasResponse] = []


class PlayerListResponse(BaseModel):
    data: list[PlayerResponse]
    pagination: PaginationMeta


# ─── Competition responses ────────────────────────────────────────────────────


class CompetitionResponse(BaseModel):
    id: uuid.UUID
    name: str
    short_name: str
    sport: str
    season: str | None
    region: str | None
    tier: str | None
    start_date: datetime | None
    end_date: datetime | None
    metadata: dict
    created_at: datetime
    updated_at: datetime


class CompetitionListResponse(BaseModel):
    data: list[CompetitionResponse]
    pagination: PaginationMeta


# ─── Health response ──────────────────────────────────────────────────────────


class ComponentHealth(BaseModel):
    status: str  # "healthy", "degraded", "unhealthy"
    details: str | None = None


class SourceHealth(BaseModel):
    source_id: str
    is_healthy: bool
    last_success: datetime | None = None
    consecutive_failures: int = 0


class HealthResponse(BaseModel):
    status: str
    database: ComponentHealth
    cache: ComponentHealth
    sources: list[SourceHealth]
    stats: dict


# ─── Error response ──────────────────────────────────────────────────────────


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
