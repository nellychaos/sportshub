"""Mollybet confirmation source — cross-checks events against live Mollybet markets.

Uses the Mollybet WebSocket stream to fetch all available events, then fuzzy-matches
against our canonical events.  A match gives a +0.20 confidence boost (higher than
OddsAPI's 0.15 because Mollybet aggregates dozens of bookmakers — if a real-money
market exists here, the fixture is definitively happening).

Results are cached per sport in Redis (TTL 1h) to minimise WebSocket reconnections.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
import structlog

try:
    import websockets
except ImportError:
    websockets = None  # type: ignore[assignment]

from sportshub.cache.client import RedisClient
from sportshub.models.common import Sport

from .base import ConfirmationResult, ConfirmationSource

logger = structlog.get_logger()

# -- Sport -> Mollybet sport code ------------------------------------------
SPORT_CODE_MAP: dict[str, str] = {
    "football": "fb",
    "nba": "basket",
    "lol": "esports",
}

# -- Cache / timing --------------------------------------------------------
CACHE_TTL_SECONDS = 3600  # 1 hour per sport
CONFIDENCE_BOOST = 0.20
KICKOFF_TOLERANCE_SECONDS = 90 * 60  # +/-90 minutes
_WS_TIMEOUT = 60
_WS_CONNECT_TIMEOUT = 15


def _cache_key(mollybet_sport: str) -> str:
    return f"sportshub:mollybet:events:{mollybet_sport}"


def _normalize(name: str) -> str:
    return name.strip().lower()


def _teams_match(mb_home: str, mb_away: str, home: str, away: str) -> bool:
    """Substring fuzzy match — same pattern as OddsConfirmationAdapter."""
    h = _normalize(home)
    a = _normalize(away)
    mh = _normalize(mb_home)
    ma = _normalize(mb_away)

    def _partial(needle: str, haystack: str) -> bool:
        return needle in haystack or haystack in needle

    return _partial(h, mh) and _partial(a, ma)


def _parse_kickoff(raw) -> datetime | None:
    if not raw:
        return None
    try:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _empty_result() -> ConfirmationResult:
    return ConfirmationResult(exists=False, confidence_boost=0.0)


class MollybetConfirmationSource(ConfirmationSource):
    """Confirmation source backed by the Mollybet multi-bookie aggregator.

    Fetches all events via the WebSocket stream (cached per sport in Redis),
    then fuzzy-matches team names and kickoff times.
    """

    def __init__(
        self,
        username: str,
        password: str,
        api_url: str = "https://api.mollybet.com",
        cache: RedisClient | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._api_url = api_url.rstrip("/")
        self._ws_url = api_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
        self._cache = cache
        self._client: httpx.AsyncClient | None = None

        self._session_token: str | None = None
        self._session_expires_at: datetime | None = None

    @property
    def source_id(self) -> str:
        return "mollybet"

    # -- Lifecycle ---------------------------------------------------------

    async def initialize(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self._api_url,
            timeout=30.0,
            headers={"User-Agent": "Sportshub/0.1"},
            follow_redirects=True,
        )

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # -- Auth --------------------------------------------------------------

    def _session_expired(self) -> bool:
        if self._session_expires_at is None:
            return True
        return datetime.utcnow() >= self._session_expires_at

    async def _ensure_session(self) -> bool:
        if self._session_token and not self._session_expired():
            return True

        if not self._client:
            await self.initialize()

        try:
            resp = await self._client.post(  # type: ignore[union-attr]
                "/v1/sessions/",
                json={"username": self._username, "password": self._password},
            )
            resp.raise_for_status()
            data = resp.json()
            self._session_token = data.get("session_id") or data.get("data") or data.get("token")
            self._session_expires_at = datetime.utcnow() + timedelta(hours=23)
            logger.info("mollybet_confirmation_session_created", expires_in_hours=23)
            return True
        except Exception as exc:
            logger.error("mollybet_confirmation_auth_failed", error=str(exc))
            self._session_token = None
            return False

    # -- Public interface --------------------------------------------------

    async def check_event_exists(
        self,
        sport: Sport,
        home_team: str,
        away_team: str,
        scheduled_at: datetime,
    ) -> ConfirmationResult:
        sport_val = sport.value if isinstance(sport, Sport) else sport
        mollybet_sport = SPORT_CODE_MAP.get(sport_val)
        if not mollybet_sport:
            return _empty_result()

        mb_events = await self._get_sport_events(mollybet_sport)
        if not mb_events:
            return _empty_result()

        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

        for ev in mb_events:
            mb_home = ev.get("home", "")
            mb_away = ev.get("away", "")
            kickoff = _parse_kickoff(ev.get("start_time"))
            if not _teams_match(mb_home, mb_away, home_team, away_team):
                continue
            if kickoff is None:
                continue
            time_diff = abs((kickoff - scheduled_at).total_seconds())
            if time_diff <= KICKOFF_TOLERANCE_SECONDS:
                return ConfirmationResult(
                    exists=True,
                    confidence_boost=CONFIDENCE_BOOST,
                    raw_data={
                        "mollybet_event_id": str(ev.get("event_id", "")),
                        "mollybet_home": mb_home,
                        "mollybet_away": mb_away,
                        "mollybet_kickoff": kickoff.isoformat(),
                        "mollybet_sport": mollybet_sport,
                        "time_diff_seconds": time_diff,
                    },
                )

        return ConfirmationResult(
            exists=False,
            confidence_boost=0.0,
            raw_data={"mollybet_sport": mollybet_sport, "events_checked": len(mb_events)},
        )

    async def check_batch(
        self,
        events: list[dict],
    ) -> dict[UUID, ConfirmationResult]:
        results: dict[UUID, ConfirmationResult] = {}

        if not await self._ensure_session():
            return {ev["id"]: _empty_result() for ev in events}

        # Group by sport
        by_sport: dict[str, list[dict]] = {}
        for ev in events:
            sport_val = ev["sport"].value if isinstance(ev["sport"], Sport) else ev["sport"]
            mollybet_sport = SPORT_CODE_MAP.get(sport_val)
            if not mollybet_sport:
                results[ev["id"]] = _empty_result()
                continue
            by_sport.setdefault(mollybet_sport, []).append(ev)

        for mollybet_sport, sport_events in by_sport.items():
            mb_events = await self._get_sport_events(mollybet_sport)
            if not mb_events:
                for ev in sport_events:
                    results[ev["id"]] = _empty_result()
                continue

            for ev in sport_events:
                scheduled_at = ev["scheduled_at"]
                if scheduled_at.tzinfo is None:
                    scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

                matched = False
                for mb_ev in mb_events:
                    mb_home = mb_ev.get("home", "")
                    mb_away = mb_ev.get("away", "")
                    kickoff = _parse_kickoff(mb_ev.get("start_time"))
                    if not _teams_match(mb_home, mb_away, ev["home_team"], ev["away_team"]):
                        continue
                    if kickoff is None:
                        continue
                    time_diff = abs((kickoff - scheduled_at).total_seconds())
                    if time_diff <= KICKOFF_TOLERANCE_SECONDS:
                        results[ev["id"]] = ConfirmationResult(
                            exists=True,
                            confidence_boost=CONFIDENCE_BOOST,
                            raw_data={
                                "mollybet_event_id": str(mb_ev.get("event_id", "")),
                                "mollybet_home": mb_home,
                                "mollybet_away": mb_away,
                                "mollybet_kickoff": kickoff.isoformat(),
                                "mollybet_sport": mollybet_sport,
                                "time_diff_seconds": time_diff,
                            },
                        )
                        matched = True
                        break

                if not matched:
                    results[ev["id"]] = ConfirmationResult(
                        exists=False,
                        confidence_boost=0.0,
                        raw_data={"mollybet_sport": mollybet_sport, "events_checked": len(mb_events)},
                    )

        return results

    # -- Internal helpers --------------------------------------------------

    async def _get_sport_events(self, mollybet_sport: str) -> list[dict]:
        """Return cached or freshly-fetched Mollybet events for a sport."""
        # Check Redis cache
        if self._cache:
            cached = await self._cache.get_json(_cache_key(mollybet_sport))
            if cached is not None:
                logger.debug("mollybet_confirmation_cache_hit", mollybet_sport=mollybet_sport)
                return cached

        if websockets is None:
            logger.error("mollybet_websockets_missing", hint="pip install websockets")
            return []

        if not await self._ensure_session():
            return []

        # Stream all events via WebSocket, filter by sport
        all_events = await self._stream_all_events(mollybet_sport)

        logger.info(
            "mollybet_confirmation_fetched",
            mollybet_sport=mollybet_sport,
            event_count=len(all_events),
        )

        # Cache for 1 hour
        if self._cache and all_events:
            await self._cache.set_json(
                _cache_key(mollybet_sport), all_events, ttl=CACHE_TTL_SECONDS
            )

        return all_events

    async def _stream_all_events(self, mollybet_sport: str) -> list[dict]:
        """Connect to WebSocket, collect all normal events for a sport until sync.

        Mollybet streams batched messages:
            {"ts": <float>, "data": [["event", {...}], ...]}
        We only keep "normal" event_type (individual matches with home/away).
        """
        import asyncio

        ws_url = f"{self._ws_url}/v1/stream/?token={self._session_token}"
        events: list[dict] = []
        seen_ids: set[str] = set()

        try:
            async with websockets.connect(  # type: ignore[union-attr]
                ws_url,
                open_timeout=_WS_CONNECT_TIMEOUT,
                close_timeout=5,
            ) as ws:
                deadline = asyncio.get_event_loop().time() + _WS_TIMEOUT
                synced = False

                while not synced:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        break

                    try:
                        raw_msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break

                    try:
                        msg = json.loads(raw_msg)
                    except (json.JSONDecodeError, TypeError):
                        continue

                    if not isinstance(msg, dict) or "data" not in msg:
                        continue

                    for item in msg["data"]:
                        if not isinstance(item, list) or len(item) < 2:
                            continue

                        if item[0] == "sync":
                            synced = True
                            break

                        if item[0] != "event":
                            continue

                        ev = item[1] if isinstance(item[1], dict) else None
                        if not ev:
                            continue

                        # Only normal events (individual matches)
                        if ev.get("event_type") != "normal":
                            continue

                        if ev.get("sport") != mollybet_sport:
                            continue

                        eid = str(ev.get("event_id", ""))
                        if not eid or eid in seen_ids:
                            continue
                        seen_ids.add(eid)
                        events.append(ev)

        except Exception as exc:
            logger.error(
                "mollybet_confirmation_ws_error",
                mollybet_sport=mollybet_sport,
                error=str(exc),
            )

        return events
