"""League of Legends-specific schedule constraints."""

from datetime import datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.db.tables import events_table
from sportshub.models.common import Sport
from sportshub.validation.constraints.base import Constraint, Violation


class LoLTeamMatchSpacing(Constraint):
    """Teams in the same LoL league should not play twice on the same calendar day."""

    name = "lol_team_match_spacing"
    sport = Sport.LOL
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
        competition_id = event_data.get("competition_id")

        day_start = scheduled_at.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        for team_id, role in [(home_team_id, "home"), (away_team_id, "away")]:
            conditions = [
                events_table.c.sport == Sport.LOL.value,
                events_table.c.status == "scheduled",
                events_table.c.scheduled_at >= day_start,
                events_table.c.scheduled_at < day_end,
                events_table.c.id != event_id,
                sa.or_(
                    events_table.c.home_team_id == team_id,
                    events_table.c.away_team_id == team_id,
                ),
            ]

            # Restrict to same competition (league) if known
            if competition_id:
                conditions.append(events_table.c.competition_id == competition_id)

            stmt = (
                sa.select(sa.func.count())
                .select_from(events_table)
                .where(sa.and_(*conditions))
            )
            result = await session.execute(stmt)
            count = result.scalar_one()

            if count >= 1:
                return Violation(
                    constraint_name=self.name,
                    severity=self.severity,
                    message=(
                        f"LoL team ({role} team {team_id}) already has a match "
                        f"in the same league on {day_start.strftime('%Y-%m-%d')}."
                    ),
                    event_id=event_id,
                    details={
                        "team_id": str(team_id),
                        "role": role,
                        "date": day_start.strftime("%Y-%m-%d"),
                        "competition_id": str(competition_id) if competition_id else None,
                        "existing_matches": count,
                    },
                )

        return None
