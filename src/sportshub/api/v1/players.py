"""Player endpoints: list and detail."""

import math
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.api.dependencies import get_db, get_pagination, verify_api_key
from sportshub.api.v1.schemas import (
    PlayerAliasResponse,
    PlayerDetailResponse,
    PlayerListResponse,
    PlayerResponse,
)
from sportshub.db.repositories import PlayerRepository
from sportshub.models.common import PaginationMeta, PaginationParams, Sport

router = APIRouter(prefix="/players", tags=["players"], dependencies=[Depends(verify_api_key)])


@router.get("", response_model=PlayerListResponse)
async def list_players(
    sport: str | None = Query(None),
    team_id: UUID | None = Query(None),
    search: str | None = Query(None),
    position: str | None = Query(None),
    active: bool | None = Query(True),
    pagination: PaginationParams = Depends(get_pagination),
    session: AsyncSession = Depends(get_db),
) -> PlayerListResponse:
    repo = PlayerRepository(session)
    sport_enum = Sport(sport) if sport else None
    players, total = await repo.get_all(
        sport=sport_enum, team_id=team_id, search=search,
        position=position, active=active,
        page=pagination.page, per_page=pagination.per_page,
    )

    return PlayerListResponse(
        data=[
            PlayerResponse(
                id=p.id, name=p.name, sport=p.sport.value,
                team_id=p.team_id, position=p.position, role=p.role,
                jersey_number=p.jersey_number, nationality=p.nationality,
                active=p.active, metadata=p.metadata,
                created_at=p.created_at, updated_at=p.updated_at,
            )
            for p in players
        ],
        pagination=PaginationMeta(
            page=pagination.page, per_page=pagination.per_page,
            total=total, total_pages=math.ceil(total / pagination.per_page) if total > 0 else 0,
        ),
    )


@router.get("/{player_id}", response_model=PlayerDetailResponse)
async def get_player(
    player_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> PlayerDetailResponse:
    repo = PlayerRepository(session)
    player = await repo.get_by_id(player_id)
    if not player:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Player not found"}})

    aliases = await repo.get_aliases_for_player(player_id)

    return PlayerDetailResponse(
        id=player.id, name=player.name, sport=player.sport.value,
        team_id=player.team_id, position=player.position, role=player.role,
        jersey_number=player.jersey_number, nationality=player.nationality,
        active=player.active, metadata=player.metadata,
        created_at=player.created_at, updated_at=player.updated_at,
        aliases=[
            PlayerAliasResponse(alias=a.alias, source_id=a.source_id, is_primary=a.is_primary)
            for a in aliases
        ],
    )
