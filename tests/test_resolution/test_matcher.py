"""Tests for event matching algorithm."""

import uuid
from datetime import datetime, timedelta

from sportshub.models import Event, SourceRecord
from sportshub.models.common import EventStatus, MatchFormat, Sport
from sportshub.resolution.matcher import find_matching_event


def _make_event(
    home_id: uuid.UUID,
    away_id: uuid.UUID,
    scheduled_at: datetime,
) -> Event:
    return Event(
        sport=Sport.NBA,
        competition_id=uuid.uuid4(),
        home_team_id=home_id,
        away_team_id=away_id,
        scheduled_at=scheduled_at,
    )


def _make_record(
    home_team: str,
    away_team: str,
    scheduled_at: datetime,
) -> SourceRecord:
    return SourceRecord(
        source_id="espn_nba",
        source_event_id="test-123",
        sport=Sport.NBA,
        raw_home_team=home_team,
        raw_away_team=away_team,
        raw_competition="NBA Regular Season",
        scheduled_at=scheduled_at,
        ingestion_run_id=uuid.uuid4(),
    )


class TestFindMatchingEvent:
    def test_exact_match(self):
        home_id = uuid.uuid4()
        away_id = uuid.uuid4()
        now = datetime.utcnow()

        event = _make_event(home_id, away_id, now)
        record = _make_record("Lakers", "Celtics", now)

        match, score = find_matching_event(home_id, away_id, record, [event])
        assert match == event
        assert score == 1.0

    def test_time_within_5_minutes(self):
        home_id = uuid.uuid4()
        away_id = uuid.uuid4()
        now = datetime.utcnow()

        event = _make_event(home_id, away_id, now)
        record = _make_record("Lakers", "Celtics", now + timedelta(minutes=3))

        match, score = find_matching_event(home_id, away_id, record, [event])
        assert match == event
        assert 0.90 <= score <= 1.0

    def test_time_within_30_minutes(self):
        home_id = uuid.uuid4()
        away_id = uuid.uuid4()
        now = datetime.utcnow()

        event = _make_event(home_id, away_id, now)
        record = _make_record("Lakers", "Celtics", now + timedelta(minutes=10))

        match, score = find_matching_event(home_id, away_id, record, [event])
        assert match == event
        assert 0.80 <= score <= 0.95

    def test_swapped_home_away(self):
        home_id = uuid.uuid4()
        away_id = uuid.uuid4()
        now = datetime.utcnow()

        event = _make_event(home_id, away_id, now)
        # Record has teams swapped
        record = _make_record("Celtics", "Lakers", now)

        match, score = find_matching_event(away_id, home_id, record, [event])
        assert match == event
        # Should match but without home/away bonus
        assert 0.85 <= score <= 0.95

    def test_no_team_match(self):
        home_id = uuid.uuid4()
        away_id = uuid.uuid4()
        other_id = uuid.uuid4()
        now = datetime.utcnow()

        event = _make_event(home_id, away_id, now)
        record = _make_record("Warriors", "Celtics", now)

        match, score = find_matching_event(other_id, away_id, record, [event])
        assert match is None
        assert score == 0.0

    def test_beyond_24_hours(self):
        home_id = uuid.uuid4()
        away_id = uuid.uuid4()
        now = datetime.utcnow()

        event = _make_event(home_id, away_id, now)
        record = _make_record("Lakers", "Celtics", now + timedelta(hours=25))

        match, score = find_matching_event(home_id, away_id, record, [event])
        assert match is None
        assert score == 0.0

    def test_soccer_match(self):
        """FIFA World Cup match between national teams."""
        home_id = uuid.uuid4()
        away_id = uuid.uuid4()
        now = datetime.utcnow()

        # Create a soccer event
        event = Event(
            sport=Sport.SOCCER,
            competition_id=uuid.uuid4(),
            home_team_id=home_id,
            away_team_id=away_id,
            scheduled_at=now,
        )
        record = SourceRecord(
            source_id="espn_fifa",
            source_event_id="wc-456",
            sport=Sport.SOCCER,
            raw_home_team="Brazil",
            raw_away_team="Argentina",
            raw_competition="FIFA World Cup 2026",
            scheduled_at=now + timedelta(minutes=2),
            ingestion_run_id=uuid.uuid4(),
        )

        match, score = find_matching_event(home_id, away_id, record, [event])
        assert match == event
        assert score >= 0.90  # Close time + correct home/away

    def test_picks_best_from_multiple(self):
        home_id = uuid.uuid4()
        away_id = uuid.uuid4()
        now = datetime.utcnow()

        event1 = _make_event(home_id, away_id, now - timedelta(hours=2))
        event2 = _make_event(home_id, away_id, now)  # exact time match
        record = _make_record("Lakers", "Celtics", now)

        match, score = find_matching_event(home_id, away_id, record, [event1, event2])
        assert match == event2
        assert score == 1.0
