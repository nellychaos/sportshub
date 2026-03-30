"""Source-related models: RawEvent, SourceRecord, IngestionRun, AdapterHealth."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from sportshub.models.common import Sport


class SourceReliability(str, Enum):
    OFFICIAL = "official"
    ESTABLISHED = "established"
    COMMUNITY = "community"


@dataclass
class RawEvent:
    """Universal adapter output. Contains raw fields exactly as the source provided them."""

    source_id: str
    source_event_id: str
    sport: Sport
    raw_home_team: str
    raw_away_team: str
    raw_competition: str
    scheduled_at: datetime
    venue: str | None = None
    source_timezone: str | None = None
    raw_metadata: dict = field(default_factory=dict)
    fetched_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class AdapterHealth:
    """Health status for a source adapter."""

    is_healthy: bool
    last_success: datetime | None
    last_error: str | None
    consecutive_failures: int


class SourceRecord(BaseModel):
    """Persisted provenance record linking a source observation to a canonical event."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_id: uuid.UUID | None = None
    source_id: str
    source_event_id: str
    sport: Sport
    raw_home_team: str
    raw_away_team: str
    raw_competition: str
    scheduled_at: datetime
    venue: str | None = None
    source_timezone: str | None = None
    raw_data: dict = Field(default_factory=dict)
    match_confidence: float | None = None
    ingestion_run_id: uuid.UUID
    created_at: datetime = Field(default_factory=datetime.utcnow)


class IngestionRun(BaseModel):
    """Audit log entry for a single ingestion fetch from one source."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_id: str
    started_at: datetime
    completed_at: datetime | None = None
    status: str = "running"
    records_fetched: int = 0
    records_new: int = 0
    records_updated: int = 0
    records_matched: int = 0
    error_message: str | None = None
    metadata: dict = Field(default_factory=dict)
