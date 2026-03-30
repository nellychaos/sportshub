"""Redis client with graceful fallback on connection failure."""

import json
from typing import Any

import structlog

logger = structlog.get_logger()


class RedisClient:
    """Async Redis wrapper. All methods are safe to call even if Redis is down."""

    def __init__(self) -> None:
        self._redis = None

    async def connect(self, url: str) -> None:
        """Connect to Redis."""
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(url, decode_responses=True)
            await self._redis.ping()
        except Exception as e:
            logger.warning("redis_connection_failed", error=str(e))
            self._redis = None

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()  # type: ignore[union-attr]
            self._redis = None

    async def ping(self) -> bool:
        if not self._redis:
            return False
        try:
            return await self._redis.ping()
        except Exception:
            return False

    async def get_cached(self, key: str) -> str | None:
        if not self._redis:
            return None
        try:
            return await self._redis.get(key)
        except Exception as e:
            logger.warning("redis_get_failed", key=key, error=str(e))
            return None

    async def set_cached(self, key: str, value: str, ttl: int = 3600) -> None:
        if not self._redis:
            return
        try:
            await self._redis.set(key, value, ex=ttl)
        except Exception as e:
            logger.warning("redis_set_failed", key=key, error=str(e))

    async def get_json(self, key: str) -> Any | None:
        raw = await self.get_cached(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def set_json(self, key: str, value: Any, ttl: int = 3600) -> None:
        await self.set_cached(key, json.dumps(value, default=str), ttl)

    async def delete_keys(self, pattern: str) -> int:
        """Delete all keys matching a pattern. Returns count of deleted keys."""
        if not self._redis:
            return 0
        try:
            keys = []
            async for key in self._redis.scan_iter(match=pattern, count=100):
                keys.append(key)
            if keys:
                return await self._redis.delete(*keys)
            return 0
        except Exception as e:
            logger.warning("redis_delete_failed", pattern=pattern, error=str(e))
            return 0

    async def increment(self, key: str, ttl: int | None = None) -> int:
        """Increment a counter. Used for rate limiting."""
        if not self._redis:
            return 0
        try:
            val = await self._redis.incr(key)
            if ttl and val == 1:
                await self._redis.expire(key, ttl)
            return val
        except Exception as e:
            logger.warning("redis_incr_failed", key=key, error=str(e))
            return 0
