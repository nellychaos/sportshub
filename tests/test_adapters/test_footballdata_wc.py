"""Tests for the Football-Data.org FIFA World Cup adapter."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from sportshub.ingestion.adapters.footballdata_wc import (
    BASE_URL,
    FootballDataWCAdapter,
)
from sportshub.models.common import Sport

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

MATCHES_URL = f"{BASE_URL}/competitions/WC/matches"


@pytest.fixture
def matches_data() -> dict:
    with open(FIXTURES_DIR / "footballdata_wc_matches.json") as f:
        return json.load(f)


@pytest.fixture
async def adapter():
    a = FootballDataWCAdapter(api_key="test-api-key-12345")
    await a.initialize()
    yield a
    await a.shutdown()


class TestFootballDataWCAdapterProperties:
    def test_source_id(self):
        adapter = FootballDataWCAdapter(api_key="key")
        assert adapter.source_id == "footballdata_wc"

    def test_sport(self):
        adapter = FootballDataWCAdapter(api_key="key")
        assert adapter.sport == Sport.SOCCER

    def test_reliability(self):
        from sportshub.models.source import SourceReliability

        adapter = FootballDataWCAdapter(api_key="key")
        assert adapter.reliability == SourceReliability.ESTABLISHED


class TestInitialize:
    @pytest.mark.asyncio
    async def test_api_key_set_in_headers(self):
        adapter = FootballDataWCAdapter(api_key="my-secret-key")
        await adapter.initialize()
        try:
            assert adapter._client is not None
            assert adapter._client.headers["X-Auth-Token"] == "my-secret-key"
        finally:
            await adapter.shutdown()


class TestParseMatch:
    """Test _parse_match directly with various inputs."""

    def test_valid_match_parsed(self, matches_data):
        adapter = FootballDataWCAdapter(api_key="key")
        match = matches_data["matches"][0]
        result = adapter._parse_match(match)

        assert result is not None
        assert result.source_id == "footballdata_wc"
        assert result.source_event_id == "500001"
        assert result.sport == Sport.SOCCER
        assert result.raw_home_team == "Mexico"
        assert result.raw_away_team == "Canada"
        assert result.raw_competition == "FIFA World Cup 2026"
        assert result.scheduled_at == datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc)
        assert result.venue == "Estadio Azteca"
        assert result.raw_metadata["home_tla"] == "MEX"
        assert result.raw_metadata["away_tla"] == "CAN"
        assert result.raw_metadata["stage"] == "GROUP_STAGE"
        assert result.raw_metadata["group"] == "Group A"
        assert result.raw_metadata["matchday"] == 1
        assert result.raw_metadata["competition_id"] == 2000
        assert result.raw_metadata["home_crest"] == "https://crests.football-data.org/769.png"

    def test_second_match_parsed(self, matches_data):
        adapter = FootballDataWCAdapter(api_key="key")
        match = matches_data["matches"][1]
        result = adapter._parse_match(match)

        assert result is not None
        assert result.source_event_id == "500002"
        assert result.raw_home_team == "Brazil"
        assert result.raw_away_team == "Serbia"
        assert result.venue == "MetLife Stadium"
        assert result.raw_metadata["group"] == "Group B"

    def test_tbd_match_skipped_null_names(self, matches_data):
        """Matches with null team names (knockout placeholders) are skipped."""
        adapter = FootballDataWCAdapter(api_key="key")
        match = matches_data["matches"][2]
        result = adapter._parse_match(match)
        assert result is None

    def test_empty_team_name_returns_none(self):
        adapter = FootballDataWCAdapter(api_key="key")
        match = {
            "id": 1,
            "utcDate": "2026-06-11T18:00:00Z",
            "homeTeam": {"name": "Mexico", "tla": "MEX"},
            "awayTeam": {"name": "", "tla": ""},
        }
        assert adapter._parse_match(match) is None

    def test_no_date_returns_none(self):
        adapter = FootballDataWCAdapter(api_key="key")
        match = {
            "id": 1,
            "utcDate": "",
            "homeTeam": {"name": "Mexico", "tla": "MEX"},
            "awayTeam": {"name": "Canada", "tla": "CAN"},
        }
        assert adapter._parse_match(match) is None

    def test_empty_venue_yields_none_venue(self):
        adapter = FootballDataWCAdapter(api_key="key")
        match = {
            "id": 1,
            "utcDate": "2026-06-11T18:00:00Z",
            "stage": "GROUP_STAGE",
            "group": "Group A",
            "matchday": 1,
            "homeTeam": {"name": "Mexico", "tla": "MEX", "crest": ""},
            "awayTeam": {"name": "Canada", "tla": "CAN", "crest": ""},
            "venue": "",
            "competition": {"id": 2000},
        }
        result = adapter._parse_match(match)
        assert result is not None
        assert result.venue is None

    def test_missing_optional_fields(self):
        """Match with minimal fields still parses correctly."""
        adapter = FootballDataWCAdapter(api_key="key")
        match = {
            "id": 99,
            "utcDate": "2026-06-11T18:00:00Z",
            "homeTeam": {"name": "Mexico"},
            "awayTeam": {"name": "Canada"},
        }
        result = adapter._parse_match(match)
        assert result is not None
        assert result.source_event_id == "99"
        assert result.raw_metadata["home_tla"] == ""
        assert result.raw_metadata["stage"] == ""
        assert result.raw_metadata["matchday"] is None


class TestFetchUpcoming:
    """Test fetch_upcoming with mocked HTTP responses."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_happy_path(self, matches_data, adapter):
        """Valid response yields correctly parsed events, TBD skipped."""
        respx.get(MATCHES_URL).mock(
            return_value=httpx.Response(200, json=matches_data)
        )

        events = await adapter.fetch_upcoming()

        # 3 matches in fixture, 1 is TBD -> 2 valid
        assert len(events) == 2
        assert all(e.sport == Sport.SOCCER for e in events)
        assert all(e.source_id == "footballdata_wc" for e in events)
        assert events[0].raw_home_team == "Mexico"
        assert events[1].raw_home_team == "Brazil"

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_matches(self, adapter):
        """Empty matches list yields no events."""
        respx.get(MATCHES_URL).mock(
            return_value=httpx.Response(200, json={"matches": []})
        )

        events = await adapter.fetch_upcoming()
        assert events == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_error_records_failure(self, adapter):
        """HTTP error results in empty list and recorded failure."""
        respx.get(MATCHES_URL).mock(return_value=httpx.Response(403))

        events = await adapter.fetch_upcoming()
        assert events == []

        health = await adapter.health_check()
        assert health.last_error is not None
        assert health.consecutive_failures == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_malformed_match_skipped(self, adapter):
        """Malformed individual matches are skipped, valid ones still returned."""
        data = {
            "matches": [
                {"id": "bad"},  # missing homeTeam/awayTeam -- will raise KeyError on id cast
                {
                    "id": 500001,
                    "utcDate": "2026-06-11T18:00:00Z",
                    "stage": "GROUP_STAGE",
                    "group": "Group A",
                    "matchday": 1,
                    "homeTeam": {"name": "Mexico", "tla": "MEX", "crest": ""},
                    "awayTeam": {"name": "Canada", "tla": "CAN", "crest": ""},
                    "venue": "Estadio Azteca",
                    "competition": {"id": 2000},
                },
            ]
        }
        respx.get(MATCHES_URL).mock(
            return_value=httpx.Response(200, json=data)
        )

        events = await adapter.fetch_upcoming()
        # First match is malformed -> None (no homeTeam key), second is valid
        assert len(events) == 1
        assert events[0].raw_home_team == "Mexico"

    @pytest.mark.asyncio
    @respx.mock
    async def test_all_tbd_matches_returns_empty(self, adapter):
        """Response with only TBD matches yields empty list."""
        data = {
            "matches": [
                {
                    "id": 1,
                    "utcDate": "2026-07-10T20:00:00Z",
                    "homeTeam": {"name": None},
                    "awayTeam": {"name": None},
                },
                {
                    "id": 2,
                    "utcDate": "2026-07-11T20:00:00Z",
                    "homeTeam": {"name": ""},
                    "awayTeam": {"name": "Germany"},
                },
            ]
        }
        respx.get(MATCHES_URL).mock(
            return_value=httpx.Response(200, json=data)
        )

        events = await adapter.fetch_upcoming()
        assert events == []
