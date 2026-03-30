"""Odds API confirmation adapter — checks whether events appear on betting markets."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from uuid import UUID

import httpx
import structlog

from sportshub.cache.client import RedisClient
from sportshub.models.common import Sport

from .base import ConfirmationResult, ConfirmationSource

logger = structlog.get_logger()

# Maps our Sport enum values to the-odds-api sport keys.
# Sports that have no odds coverage map to None and are skipped.
SPORT_KEY_MAP: dict[str, str | None] = {
    "nba": "basketball_nba",
    "lol": None,  # No odds coverage for LoL on this provider
    "football": "soccer_fifa_world_cup",
}

# Rate-limit: max requests per window
RATE_LIMIT_MAX = 30
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_REDIS_KEY = "sportshub:odds_api:rate_limit"

CACHE_TTL_SECONDS = 3600  # 1 hour


def _cache_key(sport_key: str) -> str:
    return f"sportshub:odds_api:events:{sport_key}"


def _normalize(name: str) -> str:
    """Lowercase, strip whitespace for fuzzy comparison."""
    return name.strip().lower()


def _teams_match(api_home: str, api_away: str, home: str, away: str) -> bool:
    """Check whether API team names match our team names (substring matching)."""
    h = _normalize(home)
    a = _normalize(away)
    api_h = _normalize(api_home)
    api_a = _normalize(api_away)

    def _partial(needle: str, haystack: str) -> bool:
        return needle in haystack or haystack in needle

    return _partial(h, api_h) and _partial(a, api_a)


class OddsConfirmationAdapter(ConfirmationSource):
    """Concrete confirmation source backed by the-odds-api.com."""

    def __init__(
        self,
        api_key: str | None = None,
        api_url: str = "https://api.the-odds-api.com/v4",
        cache: RedisClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_url = api_url.rstrip("/")
        self._cache = cache
        self._client: httpx.AsyncClient | None = None

    @property
    def source_id(self) -> str:
        return "odds_api"

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": "Sportshub/0.1"},
            follow_redirects=True,
        )

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Public interface ─────────────────────────────────────────────────

    async def check_event_exists(
        self,
        sport: Sport,
        home_team: str,
        away_team: str,
        scheduled_at: datetime,
    ) -> ConfirmationResult:
        if not self._api_key:
            return _empty_result()

        sport_key = SPORT_KEY_MAP.get(sport.value if isinstance(sport, Sport) else sport)
        if sport_key is None:
            return _empty_result()

        events = await self._fetch_odds_events(sport_key)
        if events is None:
            return _empty_result()

        for ev in events:
            api_home = ev.get("home_team", "")
            api_away = ev.get("away_team", "")
            if _teams_match(api_home, api_away, home_team, away_team):
                return ConfirmationResult(
                    exists=True,
                    confidence_boost=0.15,
                    raw_data={
                        "odds_event_id": ev.get("id"),
                        "home_team": api_home,
                        "away_team": api_away,
                        "commence_time": ev.get("commence_time"),
                        "sport_key": sport_key,
                    },
                )

        return ConfirmationResult(
            exists=False,
            confidence_boost=0.0,
            raw_data={"sport_key": sport_key, "events_checked": len(events)},
        )

    async def check_batch(
        self,
        events: list[dict],
    ) -> dict[UUID, ConfirmationResult]:
        results: dict[UUID, ConfirmationResult] = {}

        if not self._api_key:
            for ev in events:
                results[ev["id"]] = _empty_result()
            return results

        # Group events by sport key to minimize API calls
        by_sport: dict[str, list[dict]] = {}
        for ev in events:
            sport_val = ev["sport"].value if isinstance(ev["sport"], Sport) else ev["sport"]
            sport_key = SPORT_KEY_MAP.get(sport_val)
            if sport_key is None:
                results[ev["id"]] = _empty_result()
                continue
            by_sport.setdefault(sport_key, []).append(ev)

        for sport_key, sport_events in by_sport.items():
            odds_events = await self._fetch_odds_events(sport_key)
            if odds_events is None:
                for ev in sport_events:
                    results[ev["id"]] = _empty_result()
                continue

            for ev in sport_events:
                matched = False
                for oe in odds_events:
                    if _teams_match(
                        oe.get("home_team", ""),
                        oe.get("away_team", ""),
                        ev["home_team"],
                        ev["away_team"],
                    ):
                        results[ev["id"]] = ConfirmationResult(
                            exists=True,
                            confidence_boost=0.15,
                            raw_data={
                                "odds_event_id": oe.get("id"),
                                "home_team": oe.get("home_team"),
                                "away_team": oe.get("away_team"),
                                "commence_time": oe.get("commence_time"),
                                "sport_key": sport_key,
                            },
                        )
                        matched = True
                        break
                if not matched:
                    results[ev["id"]] = ConfirmationResult(
                        exists=False,
                        confidence_boost=0.0,
                        raw_data={"sport_key": sport_key},
                    )

        return results

    # ── Internal helpers ─────────────────────────────────────────────────

    async def _fetch_odds_events(self, sport_key: str) -> list[dict] | None:
        """Fetch events from the odds API, with caching and rate limiting."""
        # Try cache first
        if self._cache:
            cached = await self._cache.get_json(_cache_key(sport_key))
            if cached is not None:
                logger.debug("odds_api_cache_hit", sport_key=sport_key)
                return cached

        # Rate-limit check
        if self._cache:
            count = await self._cache.increment(
                RATE_LIMIT_REDIS_KEY, ttl=RATE_LIMIT_WINDOW_SECONDS
            )
            if count > RATE_LIMIT_MAX:
                logger.warning(
                    "odds_api_rate_limited",
                    sport_key=sport_key,
                    count=count,
                )
                return None

        # Make the API call
        if not self._client:
            await self.initialize()

        try:
            url = f"{self._api_url}/sports/{sport_key}/events"
            response = await self._client.get(  # type: ignore[union-attr]
                url,
                params={"apiKey": self._api_key},
            )
            response.raise_for_status()
            events = response.json()

            # Cache the result
            if self._cache:
                await self._cache.set_json(
                    _cache_key(sport_key), events, ttl=CACHE_TTL_SECONDS
                )

            logger.info(
                "odds_api_fetched",
                sport_key=sport_key,
                event_count=len(events),
            )
            return events

        except httpx.HTTPStatusError as exc:
            logger.error(
                "odds_api_http_error",
                sport_key=sport_key,
                status=exc.response.status_code,
            )
            return None
        except Exception as exc:
            logger.error("odds_api_request_failed", sport_key=sport_key, error=str(exc))
            return None


def _empty_result() -> ConfirmationResult:
    """Return a neutral confirmation result (no signal)."""
    return ConfirmationResult(exists=False, confidence_boost=0.0)
