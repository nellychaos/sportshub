"""Team endpoints: list teams and team roster."""

import math
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.api.dependencies import get_db, get_pagination, verify_api_key
from sportshub.api.v1.schemas import (
    PlayerListResponse,
    PlayerResponse,
    TeamListResponse,
    TeamResponse,
)
from sportshub.db.repositories import PlayerRepository, TeamRepository
from sportshub.models.common import PaginationMeta, PaginationParams, Sport

router = APIRouter(prefix="/teams", tags=["teams"], dependencies=[Depends(verify_api_key)])


@router.get("", response_model=TeamListResponse)
async def list_teams(
    sport: str | None = Query(None),
    search: str | None = Query(None, description="Fuzzy search by team name"),
    pagination: PaginationParams = Depends(get_pagination),
    session: AsyncSession = Depends(get_db),
) -> TeamListResponse:
    repo = TeamRepository(session)
    sport_enum = Sport(sport) if sport else None
    teams, total = await repo.get_all(
        sport=sport_enum, search=search,
        page=pagination.page, per_page=pagination.per_page,
    )

    return TeamListResponse(
        data=[
            TeamResponse(
                id=t.id, name=t.name, short_name=t.short_name,
                abbreviation=t.abbreviation, sport=t.sport.value,
                active=t.active, metadata=t.metadata,
                created_at=t.created_at, updated_at=t.updated_at,
            )
            for t in teams
        ],
        pagination=PaginationMeta(
            page=pagination.page, per_page=pagination.per_page,
            total=total, total_pages=math.ceil(total / pagination.per_page) if total > 0 else 0,
        ),
    )


@router.get("/{team_id}/players", response_model=PlayerListResponse)
async def list_team_players(
    team_id: UUID,
    active: bool | None = Query(True),
    position: str | None = Query(None),
    pagination: PaginationParams = Depends(get_pagination),
    session: AsyncSession = Depends(get_db),
) -> PlayerListResponse:
    repo = PlayerRepository(session)
    players, total = await repo.get_by_team(
        team_id=team_id, active=active, position=position,
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
