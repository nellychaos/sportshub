"""Competition domain model."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from sportshub.models.common import Sport


class Competition(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str
    short_name: str
    sport: Sport
    season: str | None = None
    region: str | None = None
    tier: str | None = None
    metadata: dict = Field(default_factory=dict)
    start_date: datetime | None = None
    end_date: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
