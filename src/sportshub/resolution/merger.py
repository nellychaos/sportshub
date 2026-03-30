"""Conflict resolution and canonical event merging."""

from datetime import datetime
from uuid import UUID

import structlog

from sportshub.models import Event, SourceRecord
from sportshub.models.common import EventStatus, MatchFormat, Sport
from sportshub.resolution.matcher import SOURCE_PRIORITY

logger = structlog.get_logger()


def merge_source_into_event(
    event: Event,
    record: SourceRecord,
    confidence: float,
    existing_records: list[SourceRecord],
    priority_map: dict[str, float] | None = None,
) -> Event:
    """Update a canonical event with data from a new source record.

    Higher-priority sources override lower-priority ones for:
    - scheduled_at (game time)
    - venue

    Always updated:
    - source_count (incremented)
    - confidence_score (takes max)

    When priority_map is provided, uses dynamic reliability scores
    instead of hardcoded SOURCE_PRIORITY values.
    """
    priorities = priority_map if priority_map is not None else SOURCE_PRIORITY
    source_priority = priorities.get(record.source_id, 0)
    current_max_priority = max(
        (priorities.get(r.source_id, 0) for r in existing_records),
        default=0,
    )

    if source_priority >= current_max_priority:
        event.scheduled_at = record.scheduled_at
        if record.venue:
            event.venue = record.venue

    # Always update aggregate fields
    event.source_count = len(existing_records) + 1
    event.confidence_score = max(event.confidence_score, confidence)
    event.updated_at = datetime.utcnow()

    return event


def create_event_from_source(
    record: SourceRecord,
    home_team_id: UUID,
    away_team_id: UUID,
    competition_id: UUID,
    confidence: float,
) -> Event:
    """Create a new canonical event from a source record."""
    # Determine match format from metadata
    match_format = MatchFormat.SINGLE
    raw_format = record.raw_data.get("match_format", "")
    if raw_format in ("bo3", "bo5", "bo7"):
        match_format = MatchFormat(raw_format)

    return Event(
        sport=record.sport,
        competition_id=competition_id,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        scheduled_at=record.scheduled_at,
        status=EventStatus.SCHEDULED,
        match_format=match_format,
        venue=record.venue,
        confidence_score=confidence,
        source_count=1,
    )
