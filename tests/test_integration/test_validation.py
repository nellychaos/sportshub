"""Integration tests for the constraint validation engine.

Tests NBA constraints (NBASeasonDateRange, NBANoSameDayDoubleHeader)
against the Constraint.check() interface with mocked database sessions.

Requires Python 3.10+ (application models use PEP 604 union syntax).
"""

import sys
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

# Skip entire module on Python < 3.10 (application models use `X | None` syntax)
if sys.version_info < (3, 10):
    pytest.skip("Requires Python 3.10+ for PEP 604 union types", allow_module_level=True)

from sportshub.validation.constraints.nba import (
    NBANoSameDayDoubleHeader,
    NBASeasonDateRange,
)


@pytest.fixture
def mock_session():
    """Create a mock async session."""
    return AsyncMock()


@pytest.fixture
def make_event_data():
    """Factory for creating event data dicts."""
    def _make(
        scheduled_at,
        sport="nba",
        home_team_id=None,
        away_team_id=None,
    ):
        return {
            "sport": sport,
            "scheduled_at": scheduled_at,
            "home_team_id": home_team_id or uuid.uuid4(),
            "away_team_id": away_team_id or uuid.uuid4(),
            "competition_id": uuid.uuid4(),
            "status": "scheduled",
            "metadata": {},
        }
    return _make


class TestNBASeasonDateRange:
    """Test NBASeasonDateRange constraint."""

    @pytest.fixture
    def constraint(self):
        return NBASeasonDateRange()

    @pytest.mark.asyncio
    async def test_game_in_season_passes(self, constraint, mock_session, make_event_data):
        """Games during Oct-Jun should not trigger a violation."""
        event_id = uuid.uuid4()
        data = make_event_data(datetime(2026, 1, 15, 19, 0, tzinfo=timezone.utc))
        result = await constraint.check(event_id, data, mock_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_game_in_october_passes(self, constraint, mock_session, make_event_data):
        event_id = uuid.uuid4()
        data = make_event_data(datetime(2025, 10, 22, 19, 0, tzinfo=timezone.utc))
        result = await constraint.check(event_id, data, mock_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_game_in_june_passes(self, constraint, mock_session, make_event_data):
        event_id = uuid.uuid4()
        data = make_event_data(datetime(2026, 6, 20, 19, 0, tzinfo=timezone.utc))
        result = await constraint.check(event_id, data, mock_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_game_in_july_warns(self, constraint, mock_session, make_event_data):
        """July game should trigger a warning."""
        event_id = uuid.uuid4()
        data = make_event_data(datetime(2026, 7, 15, 19, 0, tzinfo=timezone.utc))
        result = await constraint.check(event_id, data, mock_session)
        assert result is not None
        assert result.severity == "warning"
        assert result.constraint_name == "nba_season_date_range"
        assert "July" in result.message

    @pytest.mark.asyncio
    async def test_game_in_august_warns(self, constraint, mock_session, make_event_data):
        event_id = uuid.uuid4()
        data = make_event_data(datetime(2026, 8, 10, 19, 0, tzinfo=timezone.utc))
        result = await constraint.check(event_id, data, mock_session)
        assert result is not None
        assert result.severity == "warning"

    @pytest.mark.asyncio
    async def test_game_in_september_warns(self, constraint, mock_session, make_event_data):
        event_id = uuid.uuid4()
        data = make_event_data(datetime(2026, 9, 5, 19, 0, tzinfo=timezone.utc))
        result = await constraint.check(event_id, data, mock_session)
        assert result is not None


class TestNBANoSameDayDoubleHeader:
    """Test NBANoSameDayDoubleHeader constraint."""

    @pytest.fixture
    def constraint(self):
        return NBANoSameDayDoubleHeader()

    @pytest.mark.asyncio
    async def test_no_conflict_passes(self, constraint, make_event_data):
        """When no other games exist for the team, should pass."""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0
        session.execute.return_value = mock_result

        event_id = uuid.uuid4()
        data = make_event_data(datetime(2026, 1, 15, 19, 0, tzinfo=timezone.utc))
        result = await constraint.check(event_id, data, session)
        assert result is None

    @pytest.mark.asyncio
    async def test_conflict_detected(self, constraint, make_event_data):
        """When a team already has a game on the same day, should error."""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 1
        session.execute.return_value = mock_result

        event_id = uuid.uuid4()
        data = make_event_data(datetime(2026, 1, 15, 19, 0, tzinfo=timezone.utc))
        result = await constraint.check(event_id, data, session)
        assert result is not None
        assert result.severity == "error"
        assert result.constraint_name == "nba_no_same_day_double_header"
        assert "already has a game" in result.message
