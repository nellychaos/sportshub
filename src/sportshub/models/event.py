"""Event domain model — the core canonical entity."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from sportshub.models.common import EventStatus, MatchFormat, Sport


class Event(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    sport: Sport
    competition_id: uuid.UUID
    home_team_id: uuid.UUID
    away_team_id: uuid.UUID
    scheduled_at: datetime
    status: EventStatus = EventStatus.SCHEDULED
    match_format: MatchFormat = MatchFormat.SINGLE
    venue: str | None = None
    venue_timezone: str | None = None
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    source_count: int = Field(default=1, ge=1)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
