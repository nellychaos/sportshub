"""Team and TeamAlias repository."""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.db.repositories.base import BaseRepository
from sportshub.db.tables import team_aliases_table, teams_table
from sportshub.models import Sport, Team, TeamAlias


class TeamRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_id(self, team_id: UUID) -> Team | None:
        stmt = sa.select(teams_table).where(teams_table.c.id == team_id)
        row = await self._fetch_one(stmt)
        if row is None:
            return None
        return Team(**self._row_to_dict(row))

    async def get_all(
        self,
        sport: Sport | None = None,
        search: str | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> tuple[list[Team], int]:
        stmt = sa.select(teams_table)

        if sport:
            stmt = stmt.where(teams_table.c.sport == sport.value)
        if search:
            stmt = stmt.where(
                sa.func.similarity(teams_table.c.name, search) > 0.3
            ).order_by(sa.func.similarity(teams_table.c.name, search).desc())

        total = await self._count(stmt)
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)
        rows = await self._fetch_all(stmt)
        return [Team(**self._row_to_dict(r)) for r in rows], total

    async def create(self, team: Team) -> Team:
        stmt = sa.insert(teams_table).values(
            id=team.id,
            name=team.name,
            short_name=team.short_name,
            abbreviation=team.abbreviation,
            sport=team.sport.value,
            active=team.active,
            metadata=team.metadata,
        ).returning(teams_table)
        row = await self._fetch_one(stmt)
        return Team(**self._row_to_dict(row))  # type: ignore[arg-type]

    async def get_alias_map(self, sport: Sport | None = None) -> dict[str, UUID]:
        """Load full alias cache: normalized_alias -> team_id, and normalized:source_id -> team_id."""
        stmt = (
            sa.select(
                team_aliases_table.c.alias_normalized,
                team_aliases_table.c.source_id,
                team_aliases_table.c.team_id,
            )
            .join(teams_table, team_aliases_table.c.team_id == teams_table.c.id)
        )
        if sport:
            stmt = stmt.where(teams_table.c.sport == sport.value)

        rows = await self._fetch_all(stmt)
        cache: dict[str, UUID] = {}
        for row in rows:
            mapping = row._mapping
            normalized = mapping["alias_normalized"]
            source_id = mapping["source_id"]
            team_id = mapping["team_id"]
            # Source-specific key (checked first in resolution)
            cache[f"{normalized}:{source_id}"] = team_id
            # Global key (fallback)
            cache[normalized] = team_id
        return cache

    async def add_alias(self, alias: TeamAlias) -> TeamAlias:
        stmt = sa.insert(team_aliases_table).values(
            id=alias.id,
            team_id=alias.team_id,
            alias=alias.alias,
            alias_normalized=alias.alias_normalized,
            source_id=alias.source_id,
            is_primary=alias.is_primary,
        ).returning(team_aliases_table)
        row = await self._fetch_one(stmt)
        return TeamAlias(**self._row_to_dict(row))  # type: ignore[arg-type]

    async def fuzzy_search(
        self, name: str, sport: Sport, threshold: float = 0.6
    ) -> list[tuple[Team, float]]:
        """Search teams by name using pg_trgm similarity."""
        similarity = sa.func.similarity(teams_table.c.name, name)
        stmt = (
            sa.select(teams_table, similarity.label("score"))
            .where(teams_table.c.sport == sport.value)
            .where(similarity > threshold)
            .order_by(similarity.desc())
            .limit(5)
        )
        rows = await self._fetch_all(stmt)
        results = []
        for row in rows:
            mapping = dict(row._mapping)
            score = mapping.pop("score")
            results.append((Team(**mapping), score))
        return results

    async def get_aliases_for_team(self, team_id: UUID) -> list[TeamAlias]:
        stmt = sa.select(team_aliases_table).where(team_aliases_table.c.team_id == team_id)
        rows = await self._fetch_all(stmt)
        return [TeamAlias(**self._row_to_dict(r)) for r in rows]
