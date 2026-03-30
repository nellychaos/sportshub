"""Dashboard API response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SystemOverviewResponse(BaseModel):
    status: str  # "healthy", "degraded", "unhealthy"
    total_events: int
    events_by_sport: dict[str, int]
    total_teams: int
    total_competitions: int
    last_successful_ingestion: datetime | None
    uptime_seconds: float


class SourceStatusResponse(BaseModel):
    source_id: str
    sport: str
    last_run_status: str | None
    last_run_at: datetime | None
    last_success_at: datetime | None
    records_fetched_last: int
    consecutive_failures: int
    circuit_breaker_open: bool
    circuit_breaker_until: datetime | None


class IngestionRunResponse(BaseModel):
    id: UUID
    source_id: str
    started_at: datetime
    completed_at: datetime | None
    status: str
    duration_seconds: float | None
    records_fetched: int
    records_new: int
    records_updated: int
    records_matched: int
    error_message: str | None


class DataQualityResponse(BaseModel):
    confidence_distribution: list[dict]
    source_coverage: list[dict]
    unmatched_by_source: list[dict]
    total_unmatched: int


class AlertResponse(BaseModel):
    severity: str  # "critical", "warning", "info"
    alert_type: str
    message: str
    timestamp: datetime | None
    details: dict | None = None


class LLMEffectivenessResponse(BaseModel):
    total_calls: int
    accepted: int
    rejected: int
    acceptance_rate: float
    aliases_created: int
    self_healing_rate: float
    total_tokens: int
    estimated_cost_usd: float
    avg_latency_ms: float
    p95_latency_ms: float
    confidence_distribution: list[dict]
    daily_trend: list[dict]
    recent_decisions: list[dict]
