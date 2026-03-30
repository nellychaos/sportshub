"""Competition endpoint."""

import math

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.api.dependencies import get_db, get_pagination, verify_api_key
from sportshub.api.v1.schemas import CompetitionListResponse, CompetitionResponse
from sportshub.db.repositories import CompetitionRepository
from sportshub.models.common import PaginationMeta, PaginationParams, Sport

router = APIRouter(prefix="/competitions", tags=["competitions"], dependencies=[Depends(verify_api_key)])


@router.get("", response_model=CompetitionListResponse)
async def list_competitions(
    sport: str | None = Query(None),
    pagination: PaginationParams = Depends(get_pagination),
    session: AsyncSession = Depends(get_db),
) -> CompetitionListResponse:
    repo = CompetitionRepository(session)
    sport_enum = Sport(sport) if sport else None
    competitions, total = await repo.get_all(
        sport=sport_enum, page=pagination.page, per_page=pagination.per_page,
    )

    return CompetitionListResponse(
        data=[
            CompetitionResponse(
                id=c.id, name=c.name, short_name=c.short_name,
                sport=c.sport.value, season=c.season, region=c.region,
                tier=c.tier, start_date=c.start_date, end_date=c.end_date,
                metadata=c.metadata, created_at=c.created_at, updated_at=c.updated_at,
            )
            for c in competitions
        ],
        pagination=PaginationMeta(
            page=pagination.page, per_page=pagination.per_page,
            total=total, total_pages=math.ceil(total / pagination.per_page) if total > 0 else 0,
        ),
    )
