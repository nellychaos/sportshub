"""Player and PlayerAlias domain models."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from sportshub.models.common import Sport


class Player(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    team_id: uuid.UUID | None = None
    name: str
    sport: Sport
    position: str | None = None
    role: str | None = None
    jersey_number: str | None = None
    nationality: str | None = None
    active: bool = True
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PlayerAlias(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    player_id: uuid.UUID
    alias: str
    alias_normalized: str = ""
    source_id: str
    is_primary: bool = False
