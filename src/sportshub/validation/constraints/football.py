"""Football (FIFA) specific schedule constraints."""

from datetime import datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.db.tables import events_table
from sportshub.models.common import Sport
from sportshub.validation.constraints.base import Constraint, Violation


class FIFATeamRestPeriod(Constraint):
    """National teams must have a minimum 2-day rest between matches."""

    name = "fifa_team_rest_period"
    sport = Sport.FOOTBALL
    severity: str = "warning"

    MIN_REST_DAYS = 2

    async def check(
        self,
        event_id: UUID,
        event_data: dict,
        session: AsyncSession,
    ) -> Violation | None:
        scheduled_at: datetime = event_data["scheduled_at"]
        home_team_id = event_data["home_team_id"]
        away_team_id = event_data["away_team_id"]

        rest_window = timedelta(days=self.MIN_REST_DAYS)

        for team_id, role in [(home_team_id, "home"), (away_team_id, "away")]:
            # Look for any other match within the rest window (before or after)
            stmt = (
                sa.select(
                    events_table.c.id,
                    events_table.c.scheduled_at,
                )
                .where(
                    sa.and_(
                        events_table.c.sport == Sport.FOOTBALL.value,
                        events_table.c.status == "scheduled",
                        events_table.c.id != event_id,
                        events_table.c.scheduled_at >= scheduled_at - rest_window,
                        events_table.c.scheduled_at <= scheduled_at + rest_window,
                        sa.or_(
                            events_table.c.home_team_id == team_id,
                            events_table.c.away_team_id == team_id,
                        ),
                    )
                )
                .order_by(
                    sa.func.abs(
                        sa.extract("epoch", events_table.c.scheduled_at)
                        - sa.extract("epoch", sa.literal(scheduled_at))
                    )
                )
                .limit(1)
            )
            result = await session.execute(stmt)
            nearby = result.first()

            if nearby:
                other_scheduled = nearby.scheduled_at
                gap = abs((scheduled_at - other_scheduled).total_seconds())
                gap_hours = gap / 3600

                return Violation(
                    constraint_name=self.name,
                    severity=self.severity,
                    message=(
                        f"Football team ({role} team {team_id}) has only "
                        f"{gap_hours:.0f}h rest before/after another match "
                        f"(minimum {self.MIN_REST_DAYS * 24}h required)."
                    ),
                    event_id=event_id,
                    details={
                        "team_id": str(team_id),
                        "role": role,
                        "gap_hours": round(gap_hours, 1),
                        "min_rest_hours": self.MIN_REST_DAYS * 24,
                        "nearby_event_id": str(nearby.id),
                        "nearby_scheduled_at": other_scheduled.isoformat(),
                    },
                )

        return None
