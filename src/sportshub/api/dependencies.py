"""FastAPI dependencies: auth, database sessions, pagination."""

from fastapi import Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.cache.client import RedisClient
from sportshub.config import get_settings
from sportshub.db.engine import get_db_session
from sportshub.models.common import PaginationParams


async def verify_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    """Validate the API key from the request header."""
    settings = get_settings()
    valid_keys = {k.strip() for k in settings.api_keys.split(",") if k.strip()}
    if x_api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "UNAUTHORIZED", "message": "Invalid API key"}},
        )
    return x_api_key


def get_pagination(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(25, ge=1, le=100, description="Items per page"),
) -> PaginationParams:
    return PaginationParams(page=page, per_page=per_page)


async def get_redis(request: Request) -> RedisClient:
    """Get Redis client from app state."""
    return request.app.state.redis


# Re-export for convenience
get_db = get_db_session
