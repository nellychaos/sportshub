"""Event repository — the core query layer for canonical events."""

from datetime import datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.db.repositories.base import BaseRepository
from sportshub.db.tables import events_table
from sportshub.models import Event, Sport


class EventRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_id(self, event_id: UUID) -> Event | None:
        stmt = sa.select(events_table).where(events_table.c.id == event_id)
        row = await self._fetch_one(stmt)
        if row is None:
            return None
        return Event(**self._row_to_dict(row))

    async def get_list(
        self,
        sport: Sport | None = None,
        team_id: UUID | None = None,
        competition_id: UUID | None = None,
        status: str = "scheduled",
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        sort: str = "scheduled_at",
        page: int = 1,
        per_page: int = 25,
    ) -> tuple[list[Event], int]:
        stmt = sa.select(events_table)

        if sport:
            stmt = stmt.where(events_table.c.sport == sport.value)
        if team_id:
            stmt = stmt.where(
                sa.or_(
                    events_table.c.home_team_id == team_id,
                    events_table.c.away_team_id == team_id,
                )
            )
        if competition_id:
            stmt = stmt.where(events_table.c.competition_id == competition_id)
        if status:
            stmt = stmt.where(events_table.c.status == status)
        if from_dt:
            stmt = stmt.where(events_table.c.scheduled_at >= from_dt)
        if to_dt:
            stmt = stmt.where(events_table.c.scheduled_at <= to_dt)

        # Sorting
        if sort.startswith("-"):
            sort_col = getattr(events_table.c, sort[1:], events_table.c.scheduled_at)
            stmt = stmt.order_by(sort_col.desc())
        else:
            sort_col = getattr(events_table.c, sort, events_table.c.scheduled_at)
            stmt = stmt.order_by(sort_col.asc())

        total = await self._count(stmt)
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)
        rows = await self._fetch_all(stmt)
        return [Event(**self._row_to_dict(r)) for r in rows], total

    async def create(self, event: Event) -> Event:
        stmt = sa.insert(events_table).values(
            id=event.id,
            sport=event.sport.value,
            competition_id=event.competition_id,
            home_team_id=event.home_team_id,
            away_team_id=event.away_team_id,
            scheduled_at=event.scheduled_at,
            status=event.status.value,
            match_format=event.match_format.value,
            venue=event.venue,
            confidence_score=event.confidence_score,
            source_count=event.source_count,
            metadata=event.metadata,
        ).returning(events_table)
        row = await self._fetch_one(stmt)
        return Event(**self._row_to_dict(row))  # type: ignore[arg-type]

    async def update(self, event: Event) -> Event:
        stmt = (
            sa.update(events_table)
            .where(events_table.c.id == event.id)
            .values(
                scheduled_at=event.scheduled_at,
                status=event.status.value,
                match_format=event.match_format.value,
                venue=event.venue,
                confidence_score=event.confidence_score,
                source_count=event.source_count,
                metadata=event.metadata,
            )
            .returning(events_table)
        )
        row = await self._fetch_one(stmt)
        return Event(**self._row_to_dict(row))  # type: ignore[arg-type]

    async def get_upcoming_by_sport_and_teams(
        self,
        sport: Sport,
        team_ids: set[UUID],
        time_window: tuple[datetime, datetime],
    ) -> list[Event]:
        """Find candidate events for entity resolution matching.

        Returns events in the given sport where either home or away team is in
        team_ids and scheduled_at is within the time window.
        """
        stmt = (
            sa.select(events_table)
            .where(events_table.c.sport == sport.value)
            .where(events_table.c.status == "scheduled")
            .where(events_table.c.scheduled_at.between(*time_window))
            .where(
                sa.or_(
                    events_table.c.home_team_id.in_(team_ids),
                    events_table.c.away_team_id.in_(team_ids),
                )
            )
        )
        rows = await self._fetch_all(stmt)
        return [Event(**self._row_to_dict(r)) for r in rows]
