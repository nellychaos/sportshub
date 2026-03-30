"""Health check endpoint (no authentication required)."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.api.dependencies import get_db, get_redis
from sportshub.api.v1.schemas import ComponentHealth, HealthResponse
from sportshub.cache.client import RedisClient
from sportshub.db.tables import events_table, source_records_table, teams_table, competitions_table

import sqlalchemy as sa

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(
    session: AsyncSession = Depends(get_db),
    cache: RedisClient = Depends(get_redis),
) -> HealthResponse:
    # Check database
    try:
        await session.execute(sa.text("SELECT 1"))
        db_health = ComponentHealth(status="healthy")
    except Exception as e:
        db_health = ComponentHealth(status="unhealthy", details=str(e))

    # Check Redis
    redis_ok = await cache.ping()
    cache_health = ComponentHealth(
        status="healthy" if redis_ok else "degraded",
        details=None if redis_ok else "Redis connection failed",
    )

    # Aggregate stats
    try:
        events_count = (await session.execute(
            sa.select(sa.func.count()).select_from(events_table)
        )).scalar_one()
        scheduled_count = (await session.execute(
            sa.select(sa.func.count()).select_from(events_table).where(events_table.c.status == "scheduled")
        )).scalar_one()
        teams_count = (await session.execute(
            sa.select(sa.func.count()).select_from(teams_table)
        )).scalar_one()
        competitions_count = (await session.execute(
            sa.select(sa.func.count()).select_from(competitions_table)
        )).scalar_one()
        unmatched_count = (await session.execute(
            sa.select(sa.func.count()).select_from(source_records_table).where(
                source_records_table.c.event_id.is_(None)
            )
        )).scalar_one()
    except Exception:
        events_count = scheduled_count = teams_count = competitions_count = unmatched_count = -1

    overall = "healthy"
    if db_health.status == "unhealthy":
        overall = "unhealthy"
    elif cache_health.status != "healthy":
        overall = "degraded"

    return HealthResponse(
        status=overall,
        database=db_health,
        cache=cache_health,
        sources=[],  # Populated by scheduling layer when available
        stats={
            "total_events": events_count,
            "events_scheduled": scheduled_count,
            "total_teams": teams_count,
            "total_competitions": competitions_count,
            "unmatched_source_records": unmatched_count,
        },
    )
