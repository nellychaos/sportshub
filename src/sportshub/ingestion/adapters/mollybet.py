"""Mollybet ingestion adapter.

Fetches upcoming events from the Mollybet multi-bookie aggregator for a single sport.
Three instances are registered (fb -> FOOTBALL, basket -> NBA, esports -> LOL).

Event discovery uses the Mollybet WebSocket stream:
  1. REST login -> session token
  2. Connect wss://api.mollybet.com/v1/stream/?token=TOKEN
  3. Receive all `event` messages until `sync` marker
  4. Filter events by sport, emit as RawEvent

Mollybet API docs: https://api.mollybet.com/docs/api/contents/
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog

try:
    import websockets
except ImportError:
    websockets = None  # type: ignore[assignment]

from sportshub.ingestion.base import SourceAdapter
from sportshub.models.common import Sport
from sportshub.models.source import RawEvent, SourceReliability

logger = structlog.get_logger()

# How long to wait for the sync message (seconds)
_WS_TIMEOUT = 60
_WS_CONNECT_TIMEOUT = 15

# Competition whitelist per Mollybet sport code. Matched against the
# lowercased `competition_name` field via substring containment. Without this
# filter, the basket stream floods the pipeline with Dominican / Mexican /
# Venezuelan leagues (none in our teams table), the fb stream adds every J-League
# division globally, and the esports stream returns FIFA video-game matches.
MOLLYBET_COMPETITION_WHITELIST: dict[str, tuple[str, ...]] = {
    "basket": ("usa nba", "nba",),
    "fb": (
        "fifa world cup",
        "world cup",
        "uefa euro",
        "copa america",
        "world cup qualification",
        "euro qualification",
    ),
    "esports": (
        "lol",
        "league of legends",
        "worlds",
        "lck",
        "lpl",
        "lec",
        "lcs",
        "msi",
    ),
}


def _is_whitelisted_competition(mollybet_sport: str, competition_name: str) -> bool:
    allowed = MOLLYBET_COMPETITION_WHITELIST.get(mollybet_sport, ())
    if not allowed or not competition_name:
        return False
    cn = competition_name.lower()
    return any(token in cn for token in allowed)


def _parse_kickoff(raw: Any) -> datetime | None:
    """Parse Mollybet kickoff time to an aware UTC datetime."""
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


class MollybetAdapter(SourceAdapter):
    """Ingestion adapter for one Mollybet sport (fb / basket / esports).

    Connects to the Mollybet WebSocket stream, collects all event messages
    until the sync marker, filters for the configured sport, and returns
    RawEvents for the ingestion pipeline.
    """

    def __init__(
        self,
        username: str,
        password: str,
        sport: Sport,
        mollybet_sport: str,
        api_url: str = "https://api.mollybet.com",
    ) -> None:
        super().__init__()
        self._username = username
        self._password = password
        self._sport = sport
        self._mollybet_sport = mollybet_sport
        self._api_url = api_url.rstrip("/")
        self._ws_url = api_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")

        self._session_token: str | None = None
        self._session_expires_at: datetime | None = None
        self._client: httpx.AsyncClient | None = None

    # -- SourceAdapter properties ------------------------------------------

    @property
    def source_id(self) -> str:
        return f"mollybet_{self._mollybet_sport}"

    @property
    def sport(self) -> Sport:
        return self._sport

    @property
    def reliability(self) -> SourceReliability:
        return SourceReliability.ESTABLISHED

    @property
    def source_timezone(self) -> str:
        return "UTC"

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
        """Authenticate (or re-authenticate) with Mollybet. Returns True on success."""
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
            logger.info(
                "mollybet_session_created",
                source_id=self.source_id,
                expires_in_hours=23,
            )
            return True
        except httpx.HTTPStatusError as exc:
            logger.error(
                "mollybet_auth_failed",
                source_id=self.source_id,
                status=exc.response.status_code,
                body=exc.response.text[:200],
            )
            self._session_token = None
            return False
        except Exception as exc:
            logger.error("mollybet_auth_error", source_id=self.source_id, error=str(exc))
            self._session_token = None
            return False

    # -- Fetch (WebSocket) -------------------------------------------------

    async def fetch_upcoming(self) -> list[RawEvent]:
        if websockets is None:
            logger.error(
                "mollybet_websockets_missing",
                source_id=self.source_id,
                hint="pip install websockets",
            )
            return []

        try:
            if not await self._ensure_session():
                self._record_failure("authentication failed")
                return []

            events = await self._stream_events()

            self._record_success()
            logger.info(
                "mollybet_fetch_complete",
                source_id=self.source_id,
                event_count=len(events),
            )
            return events

        except Exception as exc:
            self._record_failure(str(exc))
            logger.error(
                "mollybet_fetch_error",
                source_id=self.source_id,
                error=str(exc),
            )
            return []

    async def _stream_events(self) -> list[RawEvent]:
        """Connect to Mollybet WebSocket, collect events until sync, filter by sport.

        Mollybet streams batched messages in the format:
            {"ts": <float>, "data": [["event", {...}], ["event", {...}], ...]}
        The "sync" marker signals the end of the initial snapshot.
        We only keep "normal" event_type (individual matches with home/away);
        "multirunner" events (season outrights) are skipped.
        """
        import asyncio

        ws_url = f"{self._ws_url}/v1/stream/?token={self._session_token}"
        raw_events: list[RawEvent] = []
        seen_ids: set[str] = set()

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
                    logger.warning(
                        "mollybet_ws_timeout",
                        source_id=self.source_id,
                        events_so_far=len(raw_events),
                    )
                    break

                try:
                    raw_msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break

                try:
                    msg = json.loads(raw_msg)
                except (json.JSONDecodeError, TypeError):
                    continue

                # Messages are wrapped: {"ts": ..., "data": [[type, payload], ...]}
                if not isinstance(msg, dict) or "data" not in msg:
                    continue

                for item in msg["data"]:
                    if not isinstance(item, list) or len(item) < 2:
                        continue

                    msg_type = item[0]

                    if msg_type == "sync":
                        synced = True
                        break

                    if msg_type != "event":
                        continue

                    ev = item[1] if isinstance(item[1], dict) else None
                    if not ev:
                        continue

                    # Only normal events (individual matches) — skip multirunner outrights
                    if ev.get("event_type") != "normal":
                        continue

                    # Filter by our sport
                    if ev.get("sport", "") != self._mollybet_sport:
                        continue

                    event_id = str(ev.get("event_id", ""))
                    if not event_id or event_id in seen_ids:
                        continue
                    seen_ids.add(event_id)

                    home_team = ev.get("home", "")
                    away_team = ev.get("away", "")

                    if not home_team or not away_team:
                        continue

                    kickoff = _parse_kickoff(ev.get("start_time"))
                    if kickoff is None:
                        continue

                    # Only include upcoming events (not in-running or finished)
                    now_utc = datetime.now(tz=timezone.utc)
                    if kickoff < now_utc - timedelta(hours=3):
                        continue

                    # Normalise to naive UTC for pipeline compatibility
                    scheduled_at = kickoff.replace(tzinfo=None)

                    competition_id = str(ev.get("competition_id", ""))
                    competition_name = ev.get("competition_name", "")
                    competition_country = ev.get("competition_country", "")

                    # Skip competitions not on the whitelist (e.g., Dominican LNB when we want NBA)
                    if not _is_whitelisted_competition(self._mollybet_sport, competition_name):
                        continue

                    raw_events.append(RawEvent(
                        source_id=self.source_id,
                        source_event_id=event_id,
                        sport=self._sport,
                        raw_home_team=home_team,
                        raw_away_team=away_team,
                        raw_competition=competition_name or competition_id,
                        scheduled_at=scheduled_at,
                        venue=None,
                        source_timezone="UTC",
                        raw_metadata={
                            "mollybet_event_id": event_id,
                            "mollybet_competition_id": competition_id,
                            "mollybet_competition_name": competition_name,
                            "mollybet_competition_country": competition_country,
                            "mollybet_sport": self._mollybet_sport,
                            "event_name": ev.get("event_name", ""),
                            "ir_status": ev.get("ir_status", ""),
                        },
                    ))

        return raw_events
