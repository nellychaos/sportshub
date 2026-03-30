"""Player and PlayerAlias repository."""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.db.repositories.base import BaseRepository
from sportshub.db.tables import player_aliases_table, players_table
from sportshub.models import Player, PlayerAlias, Sport


class PlayerRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_id(self, player_id: UUID) -> Player | None:
        stmt = sa.select(players_table).where(players_table.c.id == player_id)
        row = await self._fetch_one(stmt)
        if row is None:
            return None
        return Player(**self._row_to_dict(row))

    async def get_by_team(
        self,
        team_id: UUID,
        active: bool | None = True,
        position: str | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> tuple[list[Player], int]:
        stmt = sa.select(players_table).where(players_table.c.team_id == team_id)
        if active is not None:
            stmt = stmt.where(players_table.c.active == active)
        if position:
            stmt = stmt.where(players_table.c.position == position)

        total = await self._count(stmt)
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)
        rows = await self._fetch_all(stmt)
        return [Player(**self._row_to_dict(r)) for r in rows], total

    async def get_all(
        self,
        sport: Sport | None = None,
        team_id: UUID | None = None,
        search: str | None = None,
        position: str | None = None,
        active: bool | None = True,
        page: int = 1,
        per_page: int = 25,
    ) -> tuple[list[Player], int]:
        stmt = sa.select(players_table)

        if sport:
            stmt = stmt.where(players_table.c.sport == sport.value)
        if team_id:
            stmt = stmt.where(players_table.c.team_id == team_id)
        if active is not None:
            stmt = stmt.where(players_table.c.active == active)
        if position:
            stmt = stmt.where(players_table.c.position == position)
        if search:
            stmt = stmt.where(
                sa.func.similarity(players_table.c.name, search) > 0.3
            ).order_by(sa.func.similarity(players_table.c.name, search).desc())

        total = await self._count(stmt)
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)
        rows = await self._fetch_all(stmt)
        return [Player(**self._row_to_dict(r)) for r in rows], total

    async def create(self, player: Player) -> Player:
        stmt = sa.insert(players_table).values(
            id=player.id,
            team_id=player.team_id,
            name=player.name,
            sport=player.sport.value,
            position=player.position,
            role=player.role,
            jersey_number=player.jersey_number,
            nationality=player.nationality,
            active=player.active,
            metadata=player.metadata,
        ).returning(players_table)
        row = await self._fetch_one(stmt)
        return Player(**self._row_to_dict(row))  # type: ignore[arg-type]

    async def add_alias(self, alias: PlayerAlias) -> PlayerAlias:
        stmt = sa.insert(player_aliases_table).values(
            id=alias.id,
            player_id=alias.player_id,
            alias=alias.alias,
            alias_normalized=alias.alias_normalized,
            source_id=alias.source_id,
            is_primary=alias.is_primary,
        ).returning(player_aliases_table)
        row = await self._fetch_one(stmt)
        return PlayerAlias(**self._row_to_dict(row))  # type: ignore[arg-type]

    async def get_aliases_for_player(self, player_id: UUID) -> list[PlayerAlias]:
        stmt = sa.select(player_aliases_table).where(
            player_aliases_table.c.player_id == player_id
        )
        rows = await self._fetch_all(stmt)
        return [PlayerAlias(**self._row_to_dict(r)) for r in rows]
