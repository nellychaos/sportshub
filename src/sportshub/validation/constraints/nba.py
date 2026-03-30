"""NBA-specific schedule constraints."""

from datetime import datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.db.tables import events_table
from sportshub.models.common import Sport
from sportshub.validation.constraints.base import Constraint, Violation


class NBANoSameDayDoubleHeader(Constraint):
    """A single NBA team should not play more than one game on the same calendar date."""

    name = "nba_no_same_day_double_header"
    sport = Sport.NBA
    severity: str = "error"

    async def check(
        self,
        event_id: UUID,
        event_data: dict,
        session: AsyncSession,
    ) -> Violation | None:
        scheduled_at: datetime = event_data["scheduled_at"]
        home_team_id = event_data["home_team_id"]
        away_team_id = event_data["away_team_id"]

        # Calendar-date boundaries in UTC
        day_start = scheduled_at.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        for team_id, role in [(home_team_id, "home"), (away_team_id, "away")]:
            stmt = (
                sa.select(sa.func.count())
                .select_from(events_table)
                .where(
                    sa.and_(
                        events_table.c.sport == Sport.NBA.value,
                        events_table.c.status == "scheduled",
                        events_table.c.scheduled_at >= day_start,
                        events_table.c.scheduled_at < day_end,
                        events_table.c.id != event_id,
                        sa.or_(
                            events_table.c.home_team_id == team_id,
                            events_table.c.away_team_id == team_id,
                        ),
                    )
                )
            )
            result = await session.execute(stmt)
            count = result.scalar_one()

            if count >= 1:
                return Violation(
                    constraint_name=self.name,
                    severity=self.severity,
                    message=(
                        f"NBA team ({role} team {team_id}) already has a game "
                        f"scheduled on {day_start.strftime('%Y-%m-%d')}."
                    ),
                    event_id=event_id,
                    details={
                        "team_id": str(team_id),
                        "role": role,
                        "date": day_start.strftime("%Y-%m-%d"),
                        "existing_games": count,
                    },
                )

        return None


class NBASeasonDateRange(Constraint):
    """Warn if an NBA game is scheduled outside the typical season window (Oct-Jun)."""

    name = "nba_season_date_range"
    sport = Sport.NBA
    severity: str = "warning"

    # NBA regular season + playoffs: roughly mid-October through late June
    SEASON_START_MONTH = 10  # October
    SEASON_END_MONTH = 6    # June

    async def check(
        self,
        event_id: UUID,
        event_data: dict,
        session: AsyncSession,
    ) -> Violation | None:
        scheduled_at: datetime = event_data["scheduled_at"]
        month = scheduled_at.month

        # Valid months: October (10) through June (6) of the following year
        # i.e. months 10, 11, 12, 1, 2, 3, 4, 5, 6
        in_season = month >= self.SEASON_START_MONTH or month <= self.SEASON_END_MONTH

        if not in_season:
            return Violation(
                constraint_name=self.name,
                severity=self.severity,
                message=(
                    f"NBA game scheduled in {scheduled_at.strftime('%B %Y')} "
                    f"is outside the typical season window (Oct-Jun)."
                ),
                event_id=event_id,
                details={
                    "scheduled_month": month,
                    "scheduled_at": scheduled_at.isoformat(),
                },
            )

        return None
