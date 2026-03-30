"""Tests for the ESPN FIFA World Cup adapter."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from sportshub.ingestion.adapters.espn_fifa import BASE_URL, ESPNFIFAAdapter
from sportshub.models.common import Sport

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def scoreboard_data() -> dict:
    with open(FIXTURES_DIR / "espn_fifa_scoreboard.json") as f:
        return json.load(f)


@pytest.fixture
async def adapter():
    a = ESPNFIFAAdapter()
    await a.initialize()
    yield a
    await a.shutdown()


class TestESPNFIFAAdapterProperties:
    def test_source_id(self):
        adapter = ESPNFIFAAdapter()
        assert adapter.source_id == "espn_fifa"

    def test_sport(self):
        adapter = ESPNFIFAAdapter()
        assert adapter.sport == Sport.SOCCER

    def test_reliability(self):
        from sportshub.models.source import SourceReliability

        adapter = ESPNFIFAAdapter()
        assert adapter.reliability == SourceReliability.ESTABLISHED


class TestParseEvent:
    """Test _parse_event directly with various inputs."""

    def test_valid_event_parsed(self, scoreboard_data):
        adapter = ESPNFIFAAdapter()
        event = scoreboard_data["events"][0]
        result = adapter._parse_event(event)

        assert result is not None
        assert result.source_id == "espn_fifa"
        assert result.source_event_id == "700001"
        assert result.sport == Sport.SOCCER
        assert result.raw_home_team == "Mexico"
        assert result.raw_away_team == "Canada"
        assert result.raw_competition == "FIFA World Cup 2026"
        assert result.scheduled_at == datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc)
        assert result.venue == "Estadio Azteca"
        assert result.raw_metadata["home_abbreviation"] == "MEX"
        assert result.raw_metadata["away_abbreviation"] == "CAN"
        assert result.raw_metadata["venue_city"] == "Mexico City"
        assert result.raw_metadata["stage"] == "Group A - Matchday 1"
        assert result.raw_metadata["status"] == "STATUS_SCHEDULED"

    def test_second_event_parsed(self, scoreboard_data):
        adapter = ESPNFIFAAdapter()
        event = scoreboard_data["events"][1]
        result = adapter._parse_event(event)

        assert result is not None
        assert result.source_event_id == "700002"
        assert result.raw_home_team == "Brazil"
        assert result.raw_away_team == "Serbia"
        assert result.venue == "MetLife Stadium"
        assert result.raw_metadata["venue_city"] == "East Rutherford"

    def test_tbd_match_skipped(self, scoreboard_data):
        adapter = ESPNFIFAAdapter()
        event = scoreboard_data["events"][2]
        result = adapter._parse_event(event)
        assert result is None

    def test_no_competitions_returns_none(self):
        adapter = ESPNFIFAAdapter()
        event = {"id": "1", "date": "2026-06-11T18:00:00Z"}
        assert adapter._parse_event(event) is None

    def test_empty_competitors_returns_none(self):
        adapter = ESPNFIFAAdapter()
        event = {
            "id": "1",
            "date": "2026-06-11T18:00:00Z",
            "competitions": [{"competitors": []}],
        }
        assert adapter._parse_event(event) is None

    def test_single_competitor_returns_none(self):
        adapter = ESPNFIFAAdapter()
        event = {
            "id": "1",
            "date": "2026-06-11T18:00:00Z",
            "competitions": [
                {
                    "competitors": [
                        {
                            "homeAway": "home",
                            "team": {"displayName": "Mexico", "abbreviation": "MEX"},
                        }
                    ]
                }
            ],
        }
        assert adapter._parse_event(event) is None

    def test_no_date_returns_none(self):
        adapter = ESPNFIFAAdapter()
        event = {
            "id": "1",
            "date": "",
            "competitions": [
                {
                    "competitors": [
                        {
                            "homeAway": "home",
                            "team": {"displayName": "Mexico", "abbreviation": "MEX"},
                        },
                        {
                            "homeAway": "away",
                            "team": {"displayName": "Canada", "abbreviation": "CAN"},
                        },
                    ]
                }
            ],
        }
        assert adapter._parse_event(event) is None

    def test_empty_home_name_returns_none(self):
        adapter = ESPNFIFAAdapter()
        event = {
            "id": "1",
            "date": "2026-06-11T18:00:00Z",
            "competitions": [
                {
                    "competitors": [
                        {
                            "homeAway": "home",
                            "team": {"displayName": "", "abbreviation": ""},
                        },
                        {
                            "homeAway": "away",
                            "team": {"displayName": "Canada", "abbreviation": "CAN"},
                        },
                    ]
                }
            ],
        }
        assert adapter._parse_event(event) is None

    def test_no_venue_yields_none_venue(self):
        adapter = ESPNFIFAAdapter()
        event = {
            "id": "1",
            "date": "2026-06-11T18:00:00Z",
            "competitions": [
                {
                    "competitors": [
                        {
                            "homeAway": "home",
                            "team": {"displayName": "Mexico", "abbreviation": "MEX"},
                        },
                        {
                            "homeAway": "away",
                            "team": {"displayName": "Canada", "abbreviation": "CAN"},
                        },
                    ],
                }
            ],
        }
        result = adapter._parse_event(event)
        assert result is not None
        assert result.venue is None

    def test_no_notes_yields_empty_stage(self):
        adapter = ESPNFIFAAdapter()
        event = {
            "id": "1",
            "date": "2026-06-11T18:00:00Z",
            "competitions": [
                {
                    "competitors": [
                        {
                            "homeAway": "home",
                            "team": {"displayName": "Mexico", "abbreviation": "MEX"},
                        },
                        {
                            "homeAway": "away",
                            "team": {"displayName": "Canada", "abbreviation": "CAN"},
                        },
                    ],
                    "notes": [],
                }
            ],
        }
        result = adapter._parse_event(event)
        assert result is not None
        assert result.raw_metadata["stage"] == ""


class TestFetchUpcoming:
    """Test fetch_upcoming with mocked HTTP responses."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_happy_path(self, scoreboard_data, adapter):
        """Valid responses across days yield parsed events."""
        # Mock all 15 day requests to return same fixture data
        respx.get(BASE_URL).mock(
            return_value=httpx.Response(200, json=scoreboard_data)
        )

        events = await adapter.fetch_upcoming()

        # Fixture has 3 events but 1 is TBD, so 2 valid events per day * 15 days
        assert len(events) == 30
        assert all(e.sport == Sport.SOCCER for e in events)
        assert all(e.source_id == "espn_fifa" for e in events)

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_response(self, adapter):
        """Empty events list yields no events."""
        respx.get(BASE_URL).mock(
            return_value=httpx.Response(200, json={"events": []})
        )

        events = await adapter.fetch_upcoming()
        assert events == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_error_records_failure(self, adapter):
        """HTTP error results in empty list and recorded failure."""
        respx.get(BASE_URL).mock(return_value=httpx.Response(500))

        events = await adapter.fetch_upcoming()
        assert events == []

        health = await adapter.health_check()
        assert health.last_error is not None
        assert health.consecutive_failures == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_malformed_event_skipped(self, adapter):
        """Malformed events are skipped without crashing."""
        data = {
            "events": [
                {"id": "bad-event"},  # no competitions key
                {
                    "id": "700001",
                    "date": "2026-06-11T18:00:00Z",
                    "status": {"type": {"name": "STATUS_SCHEDULED"}},
                    "competitions": [
                        {
                            "competitors": [
                                {
                                    "homeAway": "home",
                                    "team": {"displayName": "Mexico", "abbreviation": "MEX"},
                                },
                                {
                                    "homeAway": "away",
                                    "team": {"displayName": "Canada", "abbreviation": "CAN"},
                                },
                            ]
                        }
                    ],
                },
            ]
        }
        respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=data))

        events = await adapter.fetch_upcoming()
        # First event is malformed (no competitions) -> None, second is valid
        # Over 15 days: 1 valid event per day
        assert len(events) == 15
