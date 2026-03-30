"""Cache key patterns and invalidation helpers."""

import hashlib
from typing import Any

from sportshub.cache.client import RedisClient

# TTLs in seconds
TTL_EVENTS_LIST = 300  # 5 minutes — events change with ingestion
TTL_EVENTS_DETAIL = 600  # 10 minutes
TTL_TEAMS = 86400  # 24 hours — teams rarely change
TTL_PLAYERS = 86400
TTL_COMPETITIONS = 86400


def _hash_params(**kwargs: Any) -> str:
    """Create a short hash of query parameters for cache keys."""
    raw = "|".join(f"{k}={v}" for k, v in sorted(kwargs.items()) if v is not None)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def events_list_key(sport: str | None = None, **kwargs: Any) -> str:
    h = _hash_params(sport=sport, **kwargs)
    return f"events:list:{h}"


def events_detail_key(event_id: str) -> str:
    return f"events:detail:{event_id}"


def teams_list_key(sport: str | None = None, **kwargs: Any) -> str:
    h = _hash_params(sport=sport, **kwargs)
    return f"teams:list:{h}"


def players_list_key(**kwargs: Any) -> str:
    h = _hash_params(**kwargs)
    return f"players:list:{h}"


def competitions_list_key(sport: str | None = None) -> str:
    return f"competitions:list:{sport or 'all'}"


async def invalidate_events(cache: RedisClient) -> int:
    """Invalidate all event-related cache entries."""
    return await cache.delete_keys("events:*")


async def invalidate_teams(cache: RedisClient) -> int:
    return await cache.delete_keys("teams:*")


async def invalidate_players(cache: RedisClient) -> int:
    return await cache.delete_keys("players:*")


async def invalidate_all(cache: RedisClient) -> int:
    """Nuclear option — clear everything."""
    count = 0
    count += await invalidate_events(cache)
    count += await invalidate_teams(cache)
    count += await invalidate_players(cache)
    count += await cache.delete_keys("competitions:*")
    return count
