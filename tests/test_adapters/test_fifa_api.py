"""Tests for the FIFA official API adapter."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from sportshub.ingestion.adapters.fifa_api import (
    BASE_URL,
    WC_COMPETITION_ID,
    WC_SEASON_ID,
    FIFAApiAdapter,
)
from sportshub.models.common import Sport

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

CALENDAR_URL = f"{BASE_URL}/calendar/matches"


@pytest.fixture
def calendar_data() -> dict:
    with open(FIXTURES_DIR / "fifa_api_calendar.json") as f:
        return json.load(f)


@pytest.fixture
async def adapter():
    a = FIFAApiAdapter()
    await a.initialize()
    yield a
    await a.shutdown()


class TestFIFAApiAdapterProperties:
    def test_source_id(self):
        adapter = FIFAApiAdapter()
        assert adapter.source_id == "fifa_api"

    def test_sport(self):
        adapter = FIFAApiAdapter()
        assert adapter.sport == Sport.SOCCER

    def test_reliability(self):
        from sportshub.models.source import SourceReliability

        adapter = FIFAApiAdapter()
        assert adapter.reliability == SourceReliability.OFFICIAL


class TestParseFIFADate:
    """Test the _parse_fifa_date static method with various formats."""

    def test_date_millis_format(self):
        result = FIFAApiAdapter._parse_fifa_date("/Date(1781362800000)/")
        assert result is not None
        assert isinstance(result, datetime)
        # 1781362800000 ms = 1781362800 seconds from epoch
        expected = datetime.utcfromtimestamp(1781362800)
        assert result == expected

    def test_date_millis_with_timezone_offset(self):
        result = FIFAApiAdapter._parse_fifa_date("/Date(1783868400000+0000)/")
        assert result is not None
        expected = datetime.utcfromtimestamp(1783868400)
        assert result == expected

    def test_iso_format(self):
        result = FIFAApiAdapter._parse_fifa_date("2026-06-12T20:00:00Z")
        assert result is not None
        assert result == datetime(2026, 6, 12, 20, 0, tzinfo=timezone.utc)

    def test_empty_string_returns_none(self):
        assert FIFAApiAdapter._parse_fifa_date("") is None

    def test_invalid_string_returns_none(self):
        assert FIFAApiAdapter._parse_fifa_date("not-a-date") is None

    def test_invalid_millis_returns_none(self):
        assert FIFAApiAdapter._parse_fifa_date("/Date(abc)/") is None


class TestExtractTeamName:
    """Test the _extract_team_name static method."""

    def test_localized_list(self):
        team = {
            "TeamName": [
                {"Locale": "en", "Description": "Mexico"},
                {"Locale": "es", "Description": "México"},
            ],
            "Abbreviation": "MEX",
        }
        assert FIFAApiAdapter._extract_team_name(team) == "Mexico"

    def test_fallback_to_short_club_name(self):
        team = {
            "TeamName": [],
            "ShortClubName": [
                {"Locale": "en", "Description": "Mexico"},
            ],
            "Abbreviation": "MEX",
        }
        assert FIFAApiAdapter._extract_team_name(team) == "Mexico"

    def test_fallback_to_abbreviation(self):
        team = {
            "TeamName": [],
            "ShortClubName": [],
            "Abbreviation": "MEX",
        }
        assert FIFAApiAdapter._extract_team_name(team) == "MEX"

    def test_empty_team_returns_empty(self):
        team = {"TeamName": [], "ShortClubName": [], "Abbreviation": ""}
        assert FIFAApiAdapter._extract_team_name(team) == ""

    def test_dict_format_team_name(self):
        """TeamName as dict instead of list."""
        team = {
            "TeamName": {"Description": "Mexico"},
            "Abbreviation": "MEX",
        }
        assert FIFAApiAdapter._extract_team_name(team) == "Mexico"

    def test_no_english_locale_returns_abbreviation(self):
        team = {
            "TeamName": [
                {"Locale": "es", "Description": "México"},
            ],
            "ShortClubName": [
                {"Locale": "es", "Description": "México"},
            ],
            "Abbreviation": "MEX",
        }
        assert FIFAApiAdapter._extract_team_name(team) == "MEX"


class TestExtractDescription:
    """Test the _extract_description static method."""

    def test_list_with_field(self):
        obj = {"Name": [{"Locale": "en", "Description": "Estadio Azteca"}]}
        assert FIFAApiAdapter._extract_description(obj, "Name") == "Estadio Azteca"

    def test_dict_with_field(self):
        obj = {"Name": {"Description": "Estadio Azteca"}}
        assert FIFAApiAdapter._extract_description(obj, "Name") == "Estadio Azteca"

    def test_list_without_field(self):
        obj = [{"Locale": "en", "Description": "Group Stage"}]
        assert FIFAApiAdapter._extract_description(obj) == "Group Stage"

    def test_empty_obj_returns_empty(self):
        assert FIFAApiAdapter._extract_description({}) == ""
        assert FIFAApiAdapter._extract_description(None) == ""

    def test_string_target(self):
        obj = {"Name": "plain-string"}
        assert FIFAApiAdapter._extract_description(obj, "Name") == "plain-string"


class TestParseMatch:
    """Test _parse_match directly with fixture data."""

    def test_valid_match_with_date_millis(self, calendar_data):
        adapter = FIFAApiAdapter()
        match = calendar_data["Results"][0]
        result = adapter._parse_match(match)

        assert result is not None
        assert result.source_id == "fifa_api"
        assert result.source_event_id == "300001"
        assert result.sport == Sport.SOCCER
        assert result.raw_home_team == "Mexico"
        assert result.raw_away_team == "Canada"
        assert result.raw_competition == "FIFA World Cup 2026"
        assert result.scheduled_at == datetime.utcfromtimestamp(1781362800)
        assert result.venue == "Estadio Azteca, Mexico City"
        assert result.raw_metadata["home_abbreviation"] == "MEX"
        assert result.raw_metadata["away_abbreviation"] == "CAN"
        assert result.raw_metadata["stage"] == "Group Stage"
        assert result.raw_metadata["group"] == "Group A"
        assert result.raw_metadata["match_number"] == 1
        assert result.raw_metadata["stadium_id"] == "400090001"
        assert result.raw_metadata["venue_city"] == "Mexico City"

    def test_valid_match_with_iso_date(self, calendar_data):
        adapter = FIFAApiAdapter()
        match = calendar_data["Results"][1]
        result = adapter._parse_match(match)

        assert result is not None
        assert result.source_event_id == "300002"
        assert result.raw_home_team == "Brazil"
        assert result.raw_away_team == "Serbia"
        assert result.scheduled_at == datetime(2026, 6, 12, 20, 0, tzinfo=timezone.utc)
        assert result.venue == "MetLife Stadium, East Rutherford"

    def test_placeholder_match_skipped(self, calendar_data):
        """Matches with empty TeamName lists (knockout placeholders) are skipped."""
        adapter = FIFAApiAdapter()
        match = calendar_data["Results"][2]
        result = adapter._parse_match(match)
        assert result is None

    def test_completed_match_skipped(self, calendar_data):
        """Matches with MatchStatus=3 (completed) are skipped."""
        adapter = FIFAApiAdapter()
        match = calendar_data["Results"][3]
        result = adapter._parse_match(match)
        assert result is None

    def test_no_date_returns_none(self):
        adapter = FIFAApiAdapter()
        match = {
            "IdMatch": "1",
            "Date": "",
            "MatchStatus": 1,
            "Home": {
                "TeamName": [{"Locale": "en", "Description": "Mexico"}],
                "Abbreviation": "MEX",
            },
            "Away": {
                "TeamName": [{"Locale": "en", "Description": "Canada"}],
                "Abbreviation": "CAN",
            },
        }
        assert adapter._parse_match(match) is None

    def test_venue_without_city(self):
        adapter = FIFAApiAdapter()
        match = {
            "IdMatch": "1",
            "Date": "/Date(1781362800000)/",
            "MatchStatus": 1,
            "Home": {
                "TeamName": [{"Locale": "en", "Description": "Mexico"}],
                "Abbreviation": "MEX",
            },
            "Away": {
                "TeamName": [{"Locale": "en", "Description": "Canada"}],
                "Abbreviation": "CAN",
            },
            "Stadium": {
                "IdStadium": "123",
                "Name": [{"Locale": "en", "Description": "Estadio Azteca"}],
                "CityName": [],
            },
            "StageName": [],
            "GroupName": [],
        }
        result = adapter._parse_match(match)
        assert result is not None
        # venue_name is set but venue_city is empty -> venue is just the name
        assert result.venue == "Estadio Azteca"


class TestFetchUpcoming:
    """Test fetch_upcoming with mocked HTTP responses."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_happy_path(self, calendar_data, adapter):
        """Valid response yields correctly parsed events, placeholders and completed skipped."""
        respx.get(CALENDAR_URL).mock(
            return_value=httpx.Response(200, json=calendar_data)
        )

        events = await adapter.fetch_upcoming()

        # 4 results in fixture: 2 valid, 1 placeholder (empty teams), 1 completed (status=3)
        assert len(events) == 2
        assert all(e.sport == Sport.SOCCER for e in events)
        assert all(e.source_id == "fifa_api" for e in events)
        assert events[0].raw_home_team == "Mexico"
        assert events[1].raw_home_team == "Brazil"

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_results(self, adapter):
        """Empty Results list yields no events."""
        respx.get(CALENDAR_URL).mock(
            return_value=httpx.Response(200, json={"Results": []})
        )

        events = await adapter.fetch_upcoming()
        assert events == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_error_records_failure(self, adapter):
        """HTTP error results in empty list and recorded failure."""
        respx.get(CALENDAR_URL).mock(return_value=httpx.Response(500))

        events = await adapter.fetch_upcoming()
        assert events == []

        health = await adapter.health_check()
        assert health.last_error is not None
        assert health.consecutive_failures == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_malformed_match_skipped(self, adapter):
        """Malformed individual match entries are skipped."""
        data = {
            "Results": [
                {"IdMatch": "bad"},  # missing Home/Away keys
                {
                    "IdMatch": "300001",
                    "Date": "/Date(1781362800000)/",
                    "MatchNumber": 1,
                    "MatchStatus": 1,
                    "Home": {
                        "TeamName": [{"Locale": "en", "Description": "Mexico"}],
                        "Abbreviation": "MEX",
                    },
                    "Away": {
                        "TeamName": [{"Locale": "en", "Description": "Canada"}],
                        "Abbreviation": "CAN",
                    },
                    "Stadium": {
                        "IdStadium": "123",
                        "Name": [{"Locale": "en", "Description": "Estadio Azteca"}],
                        "CityName": [{"Locale": "en", "Description": "Mexico City"}],
                    },
                    "StageName": [{"Locale": "en", "Description": "Group Stage"}],
                    "GroupName": [{"Locale": "en", "Description": "Group A"}],
                },
            ]
        }
        respx.get(CALENDAR_URL).mock(
            return_value=httpx.Response(200, json=data)
        )

        events = await adapter.fetch_upcoming()
        assert len(events) == 1
        assert events[0].raw_home_team == "Mexico"

    @pytest.mark.asyncio
    @respx.mock
    async def test_date_millis_with_offset_parsed(self, adapter):
        """The /Date(millis+offset)/ format is handled correctly."""
        data = {
            "Results": [
                {
                    "IdMatch": "400001",
                    "Date": "/Date(1781362800000+0300)/",
                    "MatchNumber": 10,
                    "MatchStatus": 0,
                    "Home": {
                        "TeamName": [{"Locale": "en", "Description": "Germany"}],
                        "Abbreviation": "GER",
                    },
                    "Away": {
                        "TeamName": [{"Locale": "en", "Description": "Japan"}],
                        "Abbreviation": "JPN",
                    },
                    "Stadium": {},
                    "StageName": [],
                    "GroupName": [],
                },
            ]
        }
        respx.get(CALENDAR_URL).mock(
            return_value=httpx.Response(200, json=data)
        )

        events = await adapter.fetch_upcoming()
        assert len(events) == 1
        assert events[0].raw_home_team == "Germany"
        assert events[0].raw_away_team == "Japan"
        # The millis portion (ignoring offset) should be parsed
        assert events[0].scheduled_at == datetime.utcfromtimestamp(1781362800)
