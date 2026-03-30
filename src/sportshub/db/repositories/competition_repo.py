"""Competition repository."""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.db.repositories.base import BaseRepository
from sportshub.db.tables import competitions_table
from sportshub.models import Competition, Sport


class CompetitionRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_id(self, competition_id: UUID) -> Competition | None:
        stmt = sa.select(competitions_table).where(competitions_table.c.id == competition_id)
        row = await self._fetch_one(stmt)
        if row is None:
            return None
        return Competition(**self._row_to_dict(row))

    async def get_all(
        self,
        sport: Sport | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> tuple[list[Competition], int]:
        stmt = sa.select(competitions_table)
        if sport:
            stmt = stmt.where(competitions_table.c.sport == sport.value)

        total = await self._count(stmt)
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)
        rows = await self._fetch_all(stmt)
        return [Competition(**self._row_to_dict(r)) for r in rows], total

    async def create(self, competition: Competition) -> Competition:
        stmt = sa.insert(competitions_table).values(
            id=competition.id,
            name=competition.name,
            short_name=competition.short_name,
            sport=competition.sport.value,
            season=competition.season,
            region=competition.region,
            tier=competition.tier,
            metadata=competition.metadata,
            start_date=competition.start_date,
            end_date=competition.end_date,
        ).returning(competitions_table)
        row = await self._fetch_one(stmt)
        return Competition(**self._row_to_dict(row))  # type: ignore[arg-type]

    async def find_by_name(self, name: str, sport: Sport) -> Competition | None:
        """Find a competition by short_name or name (case-insensitive)."""
        sport_val = sport.value if isinstance(sport, Sport) else str(sport)
        # Try exact short_name first
        stmt = sa.select(competitions_table).where(
            sa.and_(
                competitions_table.c.short_name == name,
                competitions_table.c.sport == sport_val,
            )
        )
        row = await self._fetch_one(stmt)
        if row:
            return Competition(**self._row_to_dict(row))

        # Try case-insensitive name match
        stmt = sa.select(competitions_table).where(
            sa.and_(
                sa.func.lower(competitions_table.c.name).contains(name.lower()),
                competitions_table.c.sport == sport_val,
            )
        )
        row = await self._fetch_one(stmt)
        if row:
            return Competition(**self._row_to_dict(row))
        return None
