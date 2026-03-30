"""Health check logic for system components."""

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
import sqlalchemy as sa

from sportshub.cache.client import RedisClient

logger = structlog.get_logger()


class HealthChecker:
    """Check health of all system components."""

    def __init__(self, session: AsyncSession, redis: RedisClient) -> None:
        self._session = session
        self._redis = redis

    async def check_database(self) -> tuple[bool, str | None]:
        try:
            await self._session.execute(sa.text("SELECT 1"))
            return True, None
        except Exception as e:
            logger.error("database_health_check_failed", error=str(e))
            return False, str(e)

    async def check_redis(self) -> tuple[bool, str | None]:
        ok = await self._redis.ping()
        if ok:
            return True, None
        return False, "Redis ping failed"

    async def is_healthy(self) -> bool:
        db_ok, _ = await self.check_database()
        return db_ok  # Redis being down is degraded, not unhealthy
