"""Team and TeamAlias domain models."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from sportshub.models.common import Sport


class Team(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str
    short_name: str
    abbreviation: str
    sport: Sport
    active: bool = True
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TeamAlias(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    team_id: uuid.UUID
    alias: str
    alias_normalized: str = ""
    source_id: str
    is_primary: bool = False
