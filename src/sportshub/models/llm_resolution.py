"""LLM Resolution domain model."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class LLMResolution(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_record_id: uuid.UUID
    resolution_type: str  # "team_name" or "event_match"
    input_text: str
    candidates: dict  # what was presented to the LLM
    llm_output: dict  # full LLM response
    resolved_id: uuid.UUID | None = None
    confidence: float
    accepted: bool
    acceptance_reason: str | None = None
    alias_created: bool = False
    latency_ms: int
    model: str
    tokens_used: int | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
